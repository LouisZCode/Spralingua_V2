"""Spoken-attempt examiner for Satzschmiede (SATZ-002, Phase 3: speak & check).

The learner records ONE German sentence for the card in front of them. Two
one-shot calls turn the clip into feedback:

1. **Deepgram prerecorded REST** — the finished clip goes up in a single POST
   and the transcript comes back in the response body. No streaming, no VAD,
   no Pipecat: the conversation pipeline exists for live turn-taking; this is
   a plain request/response, so it uses none of it.
2. **Examiner LLM** — the same Cerebras ``gpt-oss-120b`` structured-output
   wiring as ``satz/enricher.py`` judges whether the sentence uses the card's
   word correctly and writes one learner-facing feedback line.
"""

from typing import Literal, Optional

import aiohttp
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from config import deepgram_api_key, openrouter_api_key, openrouter_base_url

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
    verdict: Literal["correct", "close"] = Field(
        description=(
            "correct = a grammatical German sentence that uses the target word "
            "properly; close = anything less"
        )
    )
    feedback: str = Field(
        description=(
            "One or two short, friendly English sentences for the learner "
            "(shown to them verbatim)"
        )
    )
    corrected: Optional[str] = Field(
        default=None,
        description=(
            "Only when verdict=close: the learner's sentence minimally repaired, "
            "in German. Null when verdict=correct"
        ),
    )


PROMPT = """# Role
You examine one spoken attempt in a German vocabulary trainer. The learner saw a word card and had to say ONE German sentence of their own that uses that word. Judge the attempt and give feedback.

# The card
{card}

# What the learner said (speech-recognition transcript)
"{transcript}"

# How to judge
- The transcript comes from speech recognition: IGNORE punctuation and capitalization entirely, and forgive an obviously misheard small word — judge the sentence the learner most plausibly said.
- verdict "correct": a natural, grammatical German sentence that uses the target word in its intended meaning. Any correctly conjugated or declined form counts as using the word; separable verbs may split. A filler ("ähm") is fine.
- verdict "close": everything else — the word is missing or used in the wrong sense, wrong article/gender/case agreement involving the word, a missing reflexive pronoun on a reflexive verb, broken conjugation or word order, or the sentence isn't German at all.
- `feedback`: one or two short, friendly English sentences. When correct: confirm it, optionally add one tiny nuance. When close: name the SINGLE most important problem — don't list everything.
- `corrected`: when close, repair the learner's own sentence with the smallest possible change (keep their idea and their words). If the target word was missing entirely, write the simplest sentence that expresses their idea WITH the word. When correct: null.
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
    prompt = PROMPT.replace("{card}", _card_brief(card)).replace(
        "{transcript}", transcript
    )
    result = await llm.ainvoke(prompt)
    if result.verdict == "correct":
        result.corrected = None  # a "fix" on a correct sentence only confuses
    return result
