"""Spoken-attempt examiner for Satzschmiede (SATZ-002, Phase 3: speak & check).

The learner records ONE German sentence for the card in front of them. Two
one-shot calls turn the clip into feedback:

1. **Deepgram prerecorded REST** — the finished clip goes up in a single POST
   and the transcript comes back in the response body. No streaming, no VAD,
   no Pipecat: the conversation pipeline exists for live turn-taking; this is
   a plain request/response, so it uses none of it.
2. **Examiner LLM** — the same Cerebras ``gpt-oss-120b`` structured-output
   wiring as ``satz/enricher.py`` makes two separate judgements: was the
   card's WORD used correctly (that is what the card tests — it drives the
   green/red verdict), and is the rest of the sentence grammatical (feedback
   only, shown as a grammar note — it never fails the attempt).
"""

from typing import Optional

import aiohttp
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from config import deepgram_api_key, openrouter_api_key, openrouter_base_url
from grammar import load_taxonomy, taxonomy_brief

EXAMINER_MODEL = "openai/gpt-oss-120b"

# One-shot (prerecorded) endpoint — same nova-2 family as the live pipeline's
# streaming STT, but German: Satzschmiede sentences are in the target language,
# unlike the (currently English) conversation lessons.
_DEEPGRAM_URL = (
    "https://api.deepgram.com/v1/listen?model=nova-2&language=de&smart_format=true"
)


async def transcribe_attempt(audio: bytes, mimetype: str | None) -> str:
    """One POST to Deepgram's prerecorded API: finished clip in, transcript out.

    Browsers hand us opus-in-webm (Chrome/Firefox) or aac-in-mp4 (Safari);
    Deepgram accepts both as-is, so the upload's content type passes through.
    """
    headers = {
        "Authorization": f"Token {deepgram_api_key}",
        "Content-Type": mimetype or "audio/webm",
    }
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(_DEEPGRAM_URL, headers=headers, data=audio) as resp:
            resp.raise_for_status()
            body = await resp.json()
    return body["results"]["channels"][0]["alternatives"][0]["transcript"].strip()


class Judgement(BaseModel):
    """Two separate judgements: the WORD is what the card tests (drives the
    pass/fail colour); the rest of the sentence's grammar is feedback only."""

    word_ok: bool = Field(
        description=(
            "True iff the target word itself was used correctly: present, in "
            "its intended sense, with the grammar the word owns done right"
        )
    )
    grammar_ok: bool = Field(
        description="True iff the rest of the sentence is grammatical German"
    )
    error: Optional[str] = Field(
        default=None,
        description=(
            "One short English line (AT MOST 10 words) naming the broken rule; "
            "null when there is nothing to name"
        ),
    )
    corrected: Optional[str] = Field(
        default=None,
        description=(
            "The learner's sentence minimally repaired, in German — whenever "
            "anything is wrong. Null when everything is right"
        ),
    )
    # GRAM-001: the harvest seam. Classified here, in the same call that
    # already names the error — no second LLM call, no response change.
    pattern_id: Optional[str] = Field(
        default=None,
        description=(
            "When a mistake matches exactly ONE entry of the grammar-pattern "
            "catalog, that entry's id verbatim; null when nothing is wrong, "
            "the slip is purely about this word, or no catalog entry fits"
        ),
    )


PROMPT = """# Role
You examine one spoken attempt in a German vocabulary trainer. The learner saw a word card and had to say ONE German sentence of their own that uses that word. Judge the attempt and give feedback.

# The card
{card}

# What the learner said (speech-recognition transcript)
"{transcript}"

# How to judge — two SEPARATE calls
- The transcript comes from speech recognition: IGNORE punctuation and capitalization entirely, and forgive an obviously misheard small word — judge the sentence the learner most plausibly said. A filler ("ähm") is fine.
- `word_ok` — did the learner use the TARGET WORD correctly? This covers only the word itself: it appears (any correctly conjugated or declined form counts; separable verbs may split), in its intended meaning, with the grammar the word OWNS done right — its article/gender/case agreement, its own ending, the reflexive pronoun if it is reflexive. false when the word is missing, used in the wrong sense, or its own grammar is broken.
- `grammar_ok` — is the REST of the sentence grammatical German? Word order, other verbs' conjugation, other words' articles and endings. A sentence can be word_ok but not grammar_ok: "Ich hasse Winter, weil ich habe viele Allergien" uses the target 'Allergie' perfectly, but the weil-clause word order is wrong. If the sentence isn't German at all, both are false.
- `corrected`: whenever anything is false — repair the learner's own sentence with the smallest possible change (keep their idea and their words). If the target word was missing entirely, write the simplest sentence that expresses their idea WITH the word. When everything is right: null.
- `error`: when grammar_ok=false, ONE short English line naming the rule that was broken (e.g. "'weil' sends the verb to the end") — the correction sits right next to it, so name the WHY, never restate the fix. When only word_ok=false, add it only when the correction alone doesn't reveal why (e.g. "'freuen' is reflexive — it needs 'mich'"), otherwise null. AT MOST 10 words. When everything is right: null.

# Grammar-pattern catalog (for `pattern_id`)
{taxonomy}

- `pattern_id`: when your judgement found a mistake (word_ok or grammar_ok false) AND the broken rule matches exactly one catalog entry above, return that entry's id. Use ONLY ids from the catalog — never invent one. Purely lexical slips — the target's gender misremembered, a wrong word sense — get null: the card's own schedule already tracks those. But a case, word-order or tense error is a pattern even when it lands on the target word (a nominative article in an object slot is akkusativ-artikel, a missing reflexive pronoun is reflexivpronomen). When everything is right: null.
"""


def _card_brief(card) -> str:
    """Render the card facts the examiner needs (accepts the ORM VocabCard)."""
    lines = [f"- word to use: {card.target} ({card.type})"]
    if card.article:
        lines.append(f"- it is a noun; its article is: {card.article}")
    if card.reflexive:
        lines.append(
            "- it is a REFLEXIVE verb: a correct sentence must pair it with the "
            "matching reflexive pronoun (mich/dich/sich/uns/euch)"
        )
    if card.type == "adjective":
        lines.append(
            "- it is an adjective: endingless predicative use ('Das Auto ist "
            "schnell') is correct; attributive use must carry the right "
            "declension ending ('ein schnelles Auto'). Comparative/superlative "
            "forms count as using the word"
        )
    if card.type == "preposition":
        lines.append(
            "- it is a preposition: a correct sentence must actually use it as a "
            "preposition and put its object in the case named in the grammar "
            "note. A two-way preposition takes the ACCUSATIVE for motion toward "
            "a goal (Wohin?) and the DATIVE for a static location (Wo?). "
            "word_ok=false if the object is in the wrong case or the word is missing"
        )
    if card.tense == "past":
        lines.append(
            "- this card tests the SPOKEN PAST: the sentence must use the verb "
            "in a past tense — a correctly built Perfekt OR Präteritum both "
            "count (Germans speak both). Using the verb in the present or "
            "future means word_ok=false, even if the sentence is otherwise fine"
        )
        if card.tense_form:
            lines.append(f"- its natural spoken past: {card.tense_form}")
    lines.append(f"- meaning: {card.gloss}")
    if card.note:
        lines.append(f"- grammar note: {card.note}")
    return "\n".join(lines)


async def examine_attempt(card, transcript: str) -> Judgement:
    """One structured-output judgement call over the card + transcript."""
    llm = ChatOpenAI(
        model=EXAMINER_MODEL,
        base_url=openrouter_base_url,
        api_key=openrouter_api_key,
        extra_body={"provider": {"order": ["cerebras"], "allow_fallbacks": True}},
    ).with_structured_output(Judgement)
    prompt = (
        PROMPT.replace("{card}", _card_brief(card))
        .replace("{transcript}", transcript)
        .replace("{taxonomy}", taxonomy_brief())
    )
    result = await llm.ainvoke(prompt)
    if result.word_ok and result.grammar_ok:
        # A "fix" on a fully correct sentence only confuses.
        result.corrected = None
        result.error = None
        result.pattern_id = None
    if result.pattern_id and result.pattern_id not in load_taxonomy():
        # The ledger keys on catalog ids — a hallucinated slug must not enter.
        result.pattern_id = None
    return result
