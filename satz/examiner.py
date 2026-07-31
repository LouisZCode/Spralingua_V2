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

import re
from typing import Optional
from urllib.parse import quote

import aiohttp
from agents.openrouter_llm import structured_judge_llm
from pydantic import BaseModel, Field

from agents.observability import (
    generation_span,
    record_generation_output,
    unwrap_structured_output,
)
from config import deepgram_api_key
from grammar import load_taxonomy, taxonomy_brief

EXAMINER_MODEL = "openai/gpt-oss-120b"

# One-shot (prerecorded) endpoint — same nova-3 model as the live pipeline's
# streaming STT (services/stt.py::DEEPGRAM_MODEL), always German: Satzschmiede
# sentences are in the target language (the English-by-design conversation
# lessons don't apply here).
_DEEPGRAM_MODEL = "nova-3"
_DEEPGRAM_URL = (
    f"https://api.deepgram.com/v1/listen?model={_DEEPGRAM_MODEL}&language=de&smart_format=true"
)


async def transcribe_attempt(
    audio: bytes, mimetype: str | None, keyterms: list[str] | None = None
) -> str:
    """One POST to Deepgram's prerecorded API: finished clip in, transcript out.

    Browsers hand us opus-in-webm (Chrome/Firefox) or aac-in-mp4 (Safari);
    Deepgram accepts both as-is, so the upload's content type passes through.

    STT-003 P2: ``keyterms`` are nova-3 keyterm-prompting hints (repeated
    ``&keyterm=`` query params). Satzschmiede's strongest case — the card names
    the exact word the learner was told to say, so we bias the recognizer toward
    it and its spoken past form, cutting *false* fails on rare target words.
    """
    url = _DEEPGRAM_URL
    terms = [t for t in ((kt or "").strip() for kt in keyterms or []) if t]
    for term in terms:
        url += f"&keyterm={quote(term)}"  # quote handles umlauts/ß/spaces
    headers = {
        "Authorization": f"Token {deepgram_api_key}",
        "Content-Type": mimetype or "audio/webm",
    }
    timeout = aiohttp.ClientTimeout(total=30)
    # OBS-006: the `stt` child of the route's satz-attempt trace — span
    # duration is the Deepgram round-trip, the half of the attempt's latency
    # the examiner LLM can't explain.
    with generation_span(
        "stt",
        system="deepgram",
        model=_DEEPGRAM_MODEL,
        operation="transcription",
        input_text=f"[{len(audio)} bytes, {mimetype or 'audio/webm'}]",
    ) as span:
        if terms:
            span.set_attribute("keyterms", ", ".join(terms))
        span.set_attribute("audio.bytes", len(audio))
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, headers=headers, data=audio) as resp:
                resp.raise_for_status()
                body = await resp.json()
        transcript = body["results"]["channels"][0]["alternatives"][0]["transcript"].strip()
        record_generation_output(span, transcript)
    return transcript


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
- MEANING CHECK for `word_ok` (SATZ-008): before settling word_ok, first restate to yourself what the learner is trying to SAY, then ask: is the target the word a German speaker would actually use for that idea? The word being present with clean grammar is NOT enough. Worked example: card 'erkennen' (to recognize) in "Ich habe letztes Jahr eine neue App erkannt, und jetzt verkaufe ich sie" — selling it means they MADE it, and you cannot 'erkennen' something you created yourself: the fitting verb is 'entwickelt' or 'erfunden', so word_ok=false and `corrected` swaps the verb in. Fail on sense only when the mismatch is clear from the sentence itself; a genuinely plausible correct reading keeps word_ok=true.
- {evidence_line}
- `grammar_ok` — is the REST of the sentence grammatical German? Word order, other verbs' conjugation, other words' articles and endings. A sentence can be word_ok but not grammar_ok: "Ich hasse Winter, weil ich habe viele Allergien" uses the target 'Allergie' perfectly, but the weil-clause word order is wrong. If the sentence isn't German at all, both are false.
- STYLE IS NOT AN ERROR (SATZ-008): if everything said is grammatical, grammar_ok=true — even when you would phrase it differently, prefer another word order, or would merge or split sentences. A learner may speak TWO short sentences instead of one — that is fine; judge them together. Two short main clauses are GOOD German („Ich habe immer die Wahrheit gesagt. Ich bin kein Lügner." is fully correct — never "fix" it into one sentence). A style preference must never set word_ok or grammar_ok to false, and `corrected` must never merely restyle a correct attempt.
- `corrected`: whenever anything is false — repair the learner's own sentence with the smallest possible change (keep their idea and their words). If the target word was missing entirely, write the simplest sentence that expresses their idea WITH the word. When everything is right: null. Always write `corrected` in standard German orthography — capitalize the sentence start and every noun — no matter how the transcript was cased; a lowercased correction makes the app flag the learner's correctly-spoken nouns as errors (SATZ-016).
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


# --- TASK 6: programmatic word-presence guard --------------------------
#
# The LLM judge alone would occasionally mark word_ok=true when the target
# word was never actually said (a similar-sounding word, or the model being
# lenient). There was zero programmatic check on this before — this is a
# deterministic, conservative stem scan run BEFORE the LLM call (its result
# is injected into the prompt) and used again AFTER parsing to veto a
# word_ok=true the scan clearly contradicts. "Conservative" means: any
# ambiguity resolves to True (word present) — it only returns False when the
# target is CLEARLY absent from the transcript.

_UMLAUT_TABLE = str.maketrans({"ä": "a", "ö": "o", "ü": "u", "ß": "ss"})
_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)

# Longest-first so e.g. "zurück" is matched before the shorter "zu" would
# otherwise wrongly match its first two letters.
_SEPARABLE_PREFIXES = sorted(
    [
        "ab", "an", "auf", "aus", "bei", "ein", "mit", "nach", "vor", "zu",
        "zurück", "weg", "her", "hin", "um", "los",
    ],
    key=len,
    reverse=True,
)

# Function words that never carry the target's own inflection — dropped
# when picking content words out of a (possibly multi-word) target/tense_form
# like "sich freuen auf". Already-normalized (casefold, umlauts folded).
_SKIP_WORDS = {
    "sich",
    "der", "die", "das", "den", "dem", "des",
    "ein", "eine", "einen", "einem", "einer", "eines",
    "an", "auf", "in", "im", "am", "aus", "bei", "mit", "nach", "seit",
    "von", "vom", "zu", "zum", "zur", "durch", "fur", "gegen", "ohne",
    "um", "uber", "unter", "vor", "hinter", "neben", "zwischen",
    "gegenuber", "wegen", "trotz", "wahrend", "statt", "anstatt",
    "und", "oder",
}


def _normalize_de(text: str) -> str:
    """casefold + ä/ö/ü→a/o/u, ß→ss + punctuation stripped — a forgiving
    normalization so a stem match survives ASR spelling noise."""
    return _PUNCT_RE.sub("", text.casefold().translate(_UMLAUT_TABLE))


def _stem(word: str) -> str:
    """Word minus its last 2 chars, floored at a 3-char minimum stem — below
    that threshold the whole (short) word IS the stem."""
    return word[:-2] if len(word) - 2 >= 3 else word


def _candidate_stems(word: str) -> set[str]:
    """One normalized content word -> its stem(s). A separable-prefix verb
    ("einladen") also contributes the stem of the remainder after the
    prefix ("lad"), so a split spoken form ("... lade ... ein") still
    matches even though the transcript token never contains the full verb."""
    stems = {_stem(word)}
    for prefix in _SEPARABLE_PREFIXES:
        if word.startswith(prefix) and len(word) > len(prefix):
            remainder = word[len(prefix):]
            if len(remainder) > 3:
                stems.add(_stem(remainder))
            break  # longest matching prefix only
    return stems


def _target_evidence(card, transcript: str, extra_forms: list[str] | None = None) -> bool:
    """Deterministic, conservative scan: does ANY transcript token plausibly
    carry an inflected or separated form of the card's target word?
    ``extra_forms`` carries sibling spoken forms — e.g. the past sibling's
    tense_form for a base verb card — so strong-verb pasts count as evidence.

    Returns True (word present / stay conservative) when the transcript is
    empty, when no usable stem could be built from the target at all, or
    when a matching token is found. Returns False only when the target has
    usable stems and NONE of them show up anywhere in the transcript —
    the "clearly absent" case this guard exists to catch.
    """
    transcript_tokens = _normalize_de(transcript or "").split()
    if not transcript_tokens:
        return True

    candidate_words = _normalize_de(getattr(card, "target", "") or "").split()
    tense_form = getattr(card, "tense_form", None)
    if tense_form:
        candidate_words += _normalize_de(tense_form).split()
    for form in extra_forms or []:
        candidate_words += _normalize_de(form).split()

    stems: set[str] = set()
    for word in candidate_words:
        if word in _SKIP_WORDS or len(word) <= 3:
            continue
        stems |= _candidate_stems(word)

    if not stems:
        return True

    return any(tok.startswith(stem) for tok in transcript_tokens for stem in stems)


async def examine_attempt(
    card,
    transcript: str,
    *,
    span_name: str = "satz-judge",
    extra_forms: list[str] | None = None,
) -> Judgement:
    """One structured-output judgement call over the card + transcript.

    ``span_name`` (TASK 4) lets sibling drills that reuse this examiner
    (e.g. verbformen) file their spans under their own name instead of the
    generic default, so Langfuse traces stop reading as an undifferentiated
    "llm" soup.
    """
    # Cerebras-direct primary + OpenRouter fallback with 12s/leg deadline —
    # see agents/openrouter_llm.structured_judge_llm.
    llm = structured_judge_llm(Judgement)

    # TASK 6: run the programmatic scan BEFORE the call so its result can be
    # injected into the prompt as a hint (the LLM still makes the final call
    # — this only nudges/vetoes, it doesn't replace the judgement).
    evidence = _target_evidence(card, transcript, extra_forms=extra_forms)
    evidence_line = (
        "Programmatic scan: an inflected form of the target WAS detected in "
        "the transcript."
        if evidence
        else "Programmatic scan: an inflected form of the target was NOT "
        "detected in the transcript. If you still set word_ok=true you must "
        "be certain an inflected or separated form is genuinely present — a "
        "similar-sounding or related word does not count."
    )

    prompt = (
        PROMPT.replace("{card}", _card_brief(card))
        .replace("{transcript}", transcript)
        .replace("{taxonomy}", taxonomy_brief())
        .replace("{evidence_line}", evidence_line)
    )
    # OBS-006/OBS-008: the judge's own generation span — the usual suspect
    # for the slow attempts (OpenRouter queueing / provider fallback), now
    # measured per call with model + tokens + served-model attached.
    with generation_span(span_name, model=EXAMINER_MODEL, input_text=prompt) as span:
        span.set_attribute("word_evidence", evidence)
        result, usage, response_metadata = unwrap_structured_output(await llm.ainvoke(prompt))
        record_generation_output(span, result.model_dump_json(), usage, response_metadata)

    if not evidence and result.word_ok:
        # The scan clearly found no trace of the target — override a
        # false-positive word_ok and tell the learner why, as one short
        # clause prepended to whatever feedback the model already wrote.
        result.word_ok = False
        note = "target word not heard"
        result.error = f"{note} — {result.error}" if result.error else note

    if result.word_ok and result.grammar_ok:
        # A "fix" on a fully correct sentence only confuses.
        result.corrected = None
        result.error = None
        result.pattern_id = None
    if result.pattern_id and result.pattern_id not in load_taxonomy():
        # The ledger keys on catalog ids — a hallucinated slug must not enter.
        result.pattern_id = None
    return result
