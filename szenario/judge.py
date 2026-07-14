"""Structure judge for Szenario-Sparring (P1, thin slice).

An in-character persona asks the learner ONE unpredictable German question;
the learner answers with ONE spoken production. This judge reviews the
answer's STRUCTURE ONLY — did it open with a clear anchor, is each idea its
own clean sentence or a bolted-together run-on, did it close cleanly — and
extracts the skeleton hiding in what the learner actually said. Grammar,
gender, case, adjective endings and vocabulary correctness are explicitly
out of scope here; those are mined separately and silently from the same
transcript by ``agents/error_extractor.py`` and never shown to the learner
in this exercise.

Same Cerebras ``gpt-oss-120b`` structured-output wiring as ``satz/enricher.py``.
"""

from typing import Literal, Optional

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from agents.observability import (
    generation_span,
    record_generation_output,
    unwrap_structured_output,
)
from config import openrouter_api_key, openrouter_base_url

JUDGE_MODEL = "openai/gpt-oss-120b"


class SentenceRead(BaseModel):
    """One segmented sentence, Satz-Ampel rated. Structure only — never a
    grammar comment."""

    text: str = Field(description="The sentence as the learner said it")
    color: Literal["green", "yellow", "red"] = Field(
        description=(
            "Satz-Ampel: green = one clean simple idea; yellow = slightly "
            "overloaded but still readable in one pass; red = two or more "
            "ideas welded together into one run-on"
        )
    )
    cut: Optional[str] = Field(
        default=None,
        description=(
            "Only when color='red': a concrete rewrite, in the learner's own "
            "words, showing exactly where to split this sentence into two. "
            "Null for green/yellow"
        ),
    )


class Skeleton(BaseModel):
    """The skeleton hiding in what the learner actually said — not the ideal
    answer, theirs."""

    kern: str = Field(
        description="The one-sentence anchor to keep — the point, first, in the learner's own words"
    )
    punkte: list[str] = Field(
        description="2-4 supporting beats the learner made, each one short simple idea"
    )
    absprung: str = Field(description="A clean one-line closing line")
    vokabel_anker: list[str] = Field(
        description=(
            "Domain words from this scene's target vocabulary that the "
            "learner used, or that naturally belong in a strong answer here"
        )
    )


class StructureVerdict(BaseModel):
    """Structure-only verdict on one spoken answer. Never contains a grammar
    judgement — that lives entirely outside this module."""

    anchor_present: bool = Field(
        description=(
            "True iff the answer opened with a clear anchor: the point first, "
            "one simple sentence that IS the answer, not a wind-up"
        )
    )
    anchor_note: str = Field(
        description="At most one English sentence coaching the anchor (structure only, never grammar)"
    )
    sentences: list[SentenceRead] = Field(
        description="Every sentence the learner's answer was segmented into, each Satz-Ampel rated"
    )
    close_clean: bool = Field(
        description="True iff the answer closed cleanly (a clean stop), rather than trailing off"
    )
    close_note: str = Field(
        description="At most one English sentence coaching the close (structure only, never grammar)"
    )
    skeleton: Skeleton = Field(
        description="The skeleton hiding in what the learner actually said"
    )
    takeaway: str = Field(
        description="One-line English coaching takeaway, about structure only"
    )


PROMPT = """# Role
You judge one SPOKEN answer in a German speaking drill ("Szenario-Sparring"). An in-character persona asked the learner ONE unpredictable question; the learner gave ONE spoken answer. You judge the STRUCTURE of that answer ONLY.

# The question the learner was asked
"{question}"

# What the learner said (raw speech-recognition transcript)
"{transcript}"

# Domain vocabulary for this scene (for the skeleton's vokabel_anker)
{ziel_vokabular}

# What you judge — STRUCTURE, and STRUCTURE ONLY
1. Anchor first — did the answer open with a clear anchor: the point first, one simple sentence that IS the answer, not a wind-up, a throat-clear, or a run-up to the point?
2. One idea per sentence — is each supporting idea its own simple sentence, or are ideas bolted together into an overloaded run-on?
3. Clean close — did the answer close cleanly (a clean stop), or trail off without landing?

# What you must NEVER judge — read this twice
Do NOT correct, flag, or even comment on grammar, gender, case, adjective endings, verb conjugation, word order, or vocabulary correctness. Those belong to OTHER exercises and are handled elsewhere, invisibly to this judge. If ANY note or field you write mentions a grammar rule, an article, a case, a verb ending, or "should be" for a WORD choice — you have failed this task. Judge structure and structure only.

# The transcript has no punctuation
This is SPOKEN German straight from speech recognition: there is no punctuation and no capitalization, and sentence boundaries are not marked. Segment it into sentences yourself, by meaning and breath, before judging each one. Forgive obviously misheard filler words ("ähm", "so") — they are not part of the structure being judged.

# Satz-Ampel — rate EVERY sentence you segment
- green: one clean, simple idea.
- yellow: slightly overloaded (e.g. one extra clause tacked on) but still readable in a single pass.
- red: two or more ideas welded into one run-on sentence. For every red sentence, `cut` must give a concrete rewrite — the learner's own words, split into two sentences at the natural seam.

# The skeleton
Extract the skeleton hiding in what the learner actually said — not the ideal answer, THEIRS:
- `kern`: the one sentence that is the real anchor, the one to keep (their words, cleaned only of run-on welding — never their grammar).
- `punkte`: 2-4 supporting beats they actually made (or clearly gestured toward), each one short idea.
- `absprung`: one clean closing line, in the spirit of what they said.
- `vokabel_anker`: the domain words from the scene's vocabulary list above that the learner actually used, or the ones that would naturally belong in a strong answer to this question.

# Output discipline
`anchor_note`, `close_note`, and `takeaway` are each AT MOST one short English sentence, about structure only. The skeleton's fields stay in German, in the learner's own words.
"""


async def judge_structure(
    question: str,
    transcript: str,
    ziel_vokabular: list[str],
    user_id: str | None = None,
) -> StructureVerdict:
    """One structured-output judgement call: structure only, never grammar."""
    llm = ChatOpenAI(
        model=JUDGE_MODEL,
        base_url=openrouter_base_url,
        api_key=openrouter_api_key,
        timeout=30,
        extra_body={"provider": {"order": ["cerebras"], "allow_fallbacks": True}},
    ).with_structured_output(StructureVerdict, include_raw=True)
    prompt = (
        PROMPT.replace("{question}", question)
        .replace("{transcript}", transcript)
        .replace("{ziel_vokabular}", ", ".join(ziel_vokabular))
    )
    with generation_span(
        "szenario-structure",
        model=JUDGE_MODEL,
        input_text=transcript,
        user_id=user_id,
    ) as span:
        result, usage = unwrap_structured_output(await llm.ainvoke(prompt))
        record_generation_output(span, result.model_dump_json(), usage)
    return result
