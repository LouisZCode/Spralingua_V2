"""Spoken-attempt judge for Sprechen & transkribieren (GRAM-002, Exercise B).

The learner speaks against a constrained task; the judge answers two things
and ONLY two things: was the constraint followed (the task, counted), and did
the TARGET structure fire correctly wherever it was attempted. Other grammar
— articles, endings, vocabulary — is deliberately ignored (design rule 3:
the rule is the objective; other drills own the rest). Same Cerebras
``gpt-oss-120b`` structured-output wiring as ``satz/examiner.py``.
"""

from typing import Optional

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from agents.observability import (
    generation_span,
    record_generation_output,
    unwrap_structured_output,
)
from config import openrouter_api_key, openrouter_base_url
from grammar import load_taxonomy

JUDGE_MODEL = "openai/gpt-oss-120b"


class Slip(BaseModel):
    """One place the TARGET structure broke — the learner's words, repaired."""

    quote: str = Field(description="The learner's own words where it broke")
    corrected: str = Field(description="The minimal repair of that quote, in German")
    note: str = Field(
        description="One short English line (AT MOST 10 words) naming the broken rule"
    )


class SpokenVerdict(BaseModel):
    """Constraint compliance + target-structure slips. Nothing else is judged."""

    constraint_met: bool = Field(
        description=(
            "True iff the learner actually did the task as constrained — count "
            "the required elements (sentences, repetitions, listed verbs)"
        )
    )
    constraint_note: Optional[str] = Field(
        default=None,
        description=(
            "When constraint_met=false: one short English line saying what is "
            "missing ('weil appeared once, the task asks for two'); null when met"
        ),
    )
    hits: int = Field(
        description="How many times the TARGET structure was produced correctly"
    )
    slips: list[Slip] = Field(
        default_factory=list,
        description=(
            "Every place the TARGET structure broke; empty when it always fired. "
            "NEVER include unrelated grammar mistakes"
        ),
    )


PROMPT = """# Role
You judge one SPOKEN attempt in a German speaking drill. The learner was given a constrained speaking task; you get the raw speech-recognition transcript. The constraint exists to force ONE target structure — judge whether the constraint was followed and whether that structure fired.

# The task the learner was given
"{prompt}"

# What the constraint forces (judge against this, including how to count)
{forces}

# The target structure
{pattern_line}

# Transcript (raw speech recognition)
"{transcript}"

# How to judge
- The transcript comes from speech recognition: IGNORE punctuation and capitalization, forgive obviously misheard small words and fillers ("ähm"), and judge the sentences the learner most plausibly said. Sentence boundaries may be missing — infer them.
- `constraint_met` — did the learner ATTEMPT the task in the required quantity? Count attempts, not quality: an element with broken word order still counts toward the constraint (its break is recorded as a slip instead). constraint_met=false ONLY when the elements the forces section names are missing or too few (said "weil" once when two were asked, skipped a required verb). Count ONLY the named elements — clauses, conjunctions, openers, verbs. NEVER count "sentences": speech recognition strips the boundaries, so two spoken sentences often arrive joined as one.
- `constraint_note` — when constraint_met=false: one short English line naming what's MISSING (never a grammar comment). null when met.
- `hits` — how many of the attempted elements produced the TARGET structure correctly.
- `slips` — every ATTEMPTED element where the target structure broke: `quote` (the learner's own words, from the transcript), `corrected` (the minimal repair, in German, keeping their words), `note` (ONE English line, AT MOST 10 words, naming the broken rule — the correction sits next to it, so name the WHY, never restate the fix).
- An element that simply doesn't attempt the target (a subject-first sentence when the task asks for fronting, a sentence with no weil) is NOT a slip — it is grammatical German that dodged the task, and it counts only against the constraint.
- Judge ONLY the target structure. Wrong articles, adjective endings, vocabulary choices, or other unrelated grammar are NOT slips here and must be ignored — other drills own those. A sentence still counts as a hit when its only errors are unrelated to the target structure.
"""


async def judge_spoken(task: dict, transcript: str) -> SpokenVerdict:
    """One structured-output judgement call over the task + transcript."""
    llm = ChatOpenAI(
        model=JUDGE_MODEL,
        base_url=openrouter_base_url,
        api_key=openrouter_api_key,
        timeout=30,
        extra_body={"provider": {"order": ["cerebras"], "allow_fallbacks": True}},
    ).with_structured_output(SpokenVerdict, include_raw=True)
    p = load_taxonomy()[task["pattern_id"]]
    pattern_line = f'{p["label"]} — "{p["wrong"]}" → "{p["right"]}"'
    prompt = (
        PROMPT.replace("{prompt}", task["prompt"])
        .replace("{forces}", task["forces"])
        .replace("{pattern_line}", pattern_line)
        .replace("{transcript}", transcript)
    )
    # OBS-007: the `llm` child of the route's sprechen-attempt trace.
    with generation_span("llm", model=JUDGE_MODEL, input_text=prompt) as span:
        result, usage = unwrap_structured_output(await llm.ainvoke(prompt))
        record_generation_output(span, result.model_dump_json(), usage)
    if result.constraint_met:
        result.constraint_note = None
    return result
