"""On-demand error explanation for Satzschmiede (SATZ-007).

The examiner's feedback is deliberately terse: a corrected sentence plus (at
most) a 10-word rule note. When that isn't enough, the learner taps "Explain
more" and this module makes ONE further structured-output call that unpacks
the correction in plain learner English — why the corrected version is right,
named simply, never a grammar lecture.

Same Cerebras structured-output wiring as ``satz/examiner.py``.
"""

from typing import Optional

from agents.openrouter_llm import structured_judge_llm
from pydantic import BaseModel, Field

from agents.observability import (
    generation_span,
    record_generation_output,
    unwrap_structured_output,
)
from satz.examiner import _card_brief

EXPLAINER_MODEL = "openai/gpt-oss-120b"


class Explanation(BaseModel):
    """One plain-English unpacking of a correction the learner just saw."""

    explanation: str = Field(
        description=(
            "2-4 short English sentences explaining WHY the corrected version "
            "is right: name the rule in everyday words, point at what changed "
            "(quoting the German bits), keep a warm coaching tone"
        )
    )


PROMPT = """# Role
You are a warm, plain-spoken German coach inside a vocabulary trainer. A learner spoke a sentence, our examiner corrected it, and the learner tapped "Explain more" because the short note wasn't enough. Explain the correction so it clicks.

# The card the learner was practicing
{card}

# What the learner said (speech-recognition transcript)
"{transcript}"

# The corrected version they were shown
"{corrected}"

# The short note they already saw
{error_line}

# Write `explanation` — 2-4 short English sentences
- Explain WHY the corrected version is right and the original wasn't: name the rule in everyday words (say "with 'weil' the verb moves to the end", not "subordinate clause verb-final syntax").
- Point at the exact words that changed, quoting the German bits.
- If the short note named a rule, unpack THAT rule — do not invent a different diagnosis, and do not introduce a new correction.
- The transcript comes from speech recognition: it has no punctuation or capitalization, and may mishear small words. NEVER comment on punctuation, capitalization/spelling, or an obvious mishearing — those are transcription artifacts, not learner errors.
- Warm and encouraging, never scolding. No lists, no headings — just 2-4 flowing sentences.
"""


async def explain_correction(
    card,
    transcript: str,
    corrected: str,
    error: Optional[str],
    user_id: str | None = None,
) -> Explanation:
    """One structured-output call unpacking an examiner correction."""
    # Cerebras-direct primary + OpenRouter fallback with 12s/leg deadline —
    # see agents/openrouter_llm.structured_judge_llm.
    llm = structured_judge_llm(Explanation)
    error_line = (
        f'"{error}"' if error else "(none was shown — the correction stood alone)"
    )
    prompt = (
        PROMPT.replace("{card}", _card_brief(card))
        .replace("{transcript}", transcript)
        .replace("{corrected}", corrected)
        .replace("{error_line}", error_line)
    )
    with generation_span(
        "satz-explain", model=EXPLAINER_MODEL, input_text=prompt, user_id=user_id
    ) as span:
        result, usage, response_metadata = unwrap_structured_output(
            await llm.ainvoke(prompt)
        )
        record_generation_output(span, result.explanation, usage, response_metadata)
    return result
