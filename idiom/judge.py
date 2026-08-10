"""The "more German way" rephrase judge (IDIOM-002 Proposal-1).

Grades ONE learner line for idiomaticity — nothing else. The calibrated bar
is lifted from ``briefkasten/judge.py``'s ``natural_version`` section (the
one place this idea already shipped): rewrite only when a native would
genuinely phrase it differently, plain-but-native is the target, and null is
the common, correct outcome. Two hard exclusions keep it from becoming a
second grammar judge: transcription artifacts (the learner SPOKE most of
these lines — spelling is Deepgram's, not theirs) and grammar slips inside
natural phrasing (the drill judges and the debrief own those). Same Cerebras
``gpt-oss-120b`` wiring as every sibling judge.
"""

from typing import Optional

from pydantic import BaseModel, Field

from agents.observability import (
    generation_span,
    record_generation_output,
    unwrap_structured_output,
)
from agents.openrouter_llm import structured_judge_llm

JUDGE_MODEL = "openai/gpt-oss-120b"


class IdiomRephrase(BaseModel):
    """One idiomaticity read of a learner's line. Phrasing only — never a
    grammar verdict, never a comment on spelling or punctuation."""

    natural: Optional[str] = Field(
        default=None,
        description=(
            "The line the way a German would REALLY say it in this "
            "register. MUST be null unless a native would genuinely phrase "
            "it differently — plain, simple German that a native would "
            "also produce gets null, not a polish"
        ),
    )
    explanation: Optional[str] = Field(
        default=None,
        description=(
            "1-2 short English lines naming what a German says differently "
            "and why it sounds more natural. Never grammar talk, never "
            "rule names. MUST be null whenever natural is null"
        ),
    )


_REGISTER_GUIDANCE = {
    "informal": (
        "everyday spoken German between people who know each other — the "
        "way people really talk, not textbook German"
    ),
    "formal": (
        "natural but polite Sie-register German — the way a German actually "
        "speaks to a stranger or writes to an office, still plain, never stiff"
    ),
}


PROMPT = """# Role
You are a native German speaker reading ONE line a German learner just produced. Your only question: would a German have said it THIS way? If yes, you stay silent. If not, you show how a German would really say it.

# Their line
"{text}"

# What they were responding to (context only)
{context}

# STEP 1 — judge their line alone
The context above exists so you understand what they meant. It is someone else's words or the task they were given — it is NOT yours to rewrite or comment on. Everything you return is about the learner's line only.

# STEP 2 — this line usually comes from speech recognition
The learner most likely SPOKE it; the spelling on your screen is the recognizer's, not theirs. Lowercase nouns, missing umlauts, stray or missing punctuation, and homophone spellings (das/dass, seid/seit) are transcription artifacts. They NEVER count toward your decision and NEVER appear in your explanation.
- "ich habe diese woche viel zu tun" → natural: null. Lowercase is the recognizer's; the phrasing is exactly what a German says.

# STEP 3 — phrasing, not grammar
Other judges already grade grammar, and their verdict is sitting next to yours. You are not a grammar checker: never name a rule, never list corrections. The line between the two:
- A wrong ENDING, ARTICLE or VERB FORM inside a phrasing a German would use → null. The words are the right words; only their form slipped, and the grammar judge owns that.
- A wrong WORD CHOICE or PATTERN — the preposition, verb or construction English would pick, not German → rewrite. That is idiom, even when every ending is correct.

Worked examples:
- "Ich habe gestern mit meine Freundin Kaffee getrunken" → natural: null. "mit meine" is a case slip, but the PHRASING is exactly how a German says it — not your department.
- "Er hat mir mit den Hausaufgaben geholfen" → natural: "Er hat mir bei den Hausaufgaben geholfen." "mit" is English's *help with* wearing German clothes — Germans help "bei" something. Word choice, so it IS your department.
- CONTROL — "Ich bin glücklich zu hören, dass deine Prüfung gut war" → natural: "Schön, dass deine Prüfung gut gelaufen ist!" The grammar is fine; the phrasing is English wearing German words. THIS is your department.

When you rewrite, your German will naturally come out with correct endings even where theirs slipped — that is unavoidable and fine, but the `explanation` NEVER points at it. No mention of endings, articles, cases or rules, ever — only the phrasing choice.

# `natural` — read this twice
Write one when the line is German that no native would produce — most often English translated word for word. These are exactly the cases to catch:
- "Ich bin glücklich zu hören, dass..." → a German says "Schön, dass..." or "Das freut mich."
- "Lass mich wissen, wann..." → "Sag mir Bescheid, wann..."
- "Ich frage mich, wie das Wetter ist" → "Wie ist denn das Wetter bei dir?"
- "Ich hatte eine sehr beschäftigte Woche" → "Ich hatte viel zu tun."
- "Ich werde dich besuchen kommen" → "Dann komme ich vorbei."

Return null when the line already sounds like German — simple, plain German counts as natural. Do NOT rewrite to add sophistication, vary word choice, or make it more interesting. Plain but native is the target, not impressive.

The test is: would a German notice something is off in how it's PHRASED? If yes, rewrite. If they would simply hear it and answer, return null.

Keep the rewrite the same size and register as their line — one spoken turn, not a speech. Register: {register_guidance}

# `explanation`
1-2 short English lines: which phrasing a German reaches for instead, and what makes it the natural choice. Talk like a friend explaining how people actually say it, not like a teacher. Null whenever `natural` is null.
"""


def _normalized(s: str) -> str:
    """Casefold and strip everything but letters/digits — two lines that
    differ only in spacing, casing or punctuation are the same line."""
    return "".join(ch for ch in s.casefold() if ch.isalnum())


async def germanize(
    text: str, context: str | None, register: str = "informal"
) -> IdiomRephrase:
    """One structured-output rephrase call over the learner's line."""
    # temperature=0: the null/not-null decision is a verdict — the same line
    # must always get the same silence (see structured_judge_llm).
    llm = structured_judge_llm(IdiomRephrase, temperature=0)
    prompt = (
        PROMPT.replace("{text}", text)
        .replace("{context}", context or "(none given)")
        .replace(
            "{register_guidance}",
            _REGISTER_GUIDANCE.get(register, _REGISTER_GUIDANCE["informal"]),
        )
    )
    with generation_span("idiom-judge", model=JUDGE_MODEL, input_text=prompt) as span:
        result, usage, response_metadata = unwrap_structured_output(
            await llm.ainvoke(prompt)
        )
        record_generation_output(span, result.model_dump_json(), usage, response_metadata)
    # A whitespace-only rewrite, or one identical to the input modulo
    # casing/punctuation, IS the null case — same guard briefkasten runs.
    if result.natural is not None:
        candidate = result.natural.strip()
        if not candidate or _normalized(candidate) == _normalized(text):
            result.natural = None
    if result.natural is None:
        result.explanation = None
    return result
