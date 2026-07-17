"""Simplicity coach for Szenario-Sparring (P1, thin slice).

An in-character persona asks the learner ONE unpredictable German question;
the learner answers with ONE spoken production. This judge is not a
right/wrong grader — it reads how COMPLEX the learner's sentence
architecture was and coaches them toward SIMPLER German: affirm when the
answer was clear and simple, gently redirect (with a lighter rebuild) when
it was overcomplicated. It never says "wrong" and it never mentions
grammar, case, gender, adjective endings, verb conjugation, or vocabulary
correctness — those are mined separately and silently from the same
transcript by ``agents/error_extractor.py`` and never shown to the learner
in this exercise. The coaching insight this exercise leans on: complexity is
where grammar breaks, so coaching a learner down to simple B1/B2 sentence
architecture quietly cleans up their grammar too — without this judge ever
naming a grammar rule.

Same Cerebras ``gpt-oss-120b`` structured-output wiring as ``satz/enricher.py``.
"""

from typing import Literal, Optional

from agents.openrouter_llm import ProviderChatOpenAI, judge_http_client
from pydantic import BaseModel, Field

from agents.observability import (
    generation_span,
    record_generation_output,
    unwrap_structured_output,
)
from config import openrouter_api_key, openrouter_base_url

JUDGE_MODEL = "openai/gpt-oss-120b"


class SentenceRead(BaseModel):
    """One segmented sentence, read for COMPLEXITY of its architecture —
    never for correctness, and never a grammar comment."""

    text: str = Field(description="The sentence as the learner said it, in their own German")
    weight: Literal["light", "medium", "heavy"] = Field(
        description=(
            "How COMPLEX this sentence's architecture is — NOT whether it is "
            "correct. light = a simple main-clause sentence (the target); "
            "medium = one subordinate clause or a slightly heavier build, "
            "still fine; heavy = nested clauses or a heavy construction "
            "(stranded verb, Passiv, Konjunktiv II, older Präteritum, Futur)"
        )
    )
    simpler: Optional[str] = Field(
        default=None,
        description=(
            "The SAME idea rebuilt in simpler German — short main clauses, "
            "verb in position 2, present or perfect tense. REQUIRED when "
            "weight='heavy'; optional for 'medium'; MUST be null for 'light'"
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
    """Simplicity-coach verdict on one spoken answer: how complex was the
    sentence architecture, and how to make it lighter. Never contains a
    grammar judgement — that lives entirely outside this module. Never a
    pass/fail — only a warm, coaching read."""

    verdict: Literal["clear", "a_bit_heavy", "overcomplicated"] = Field(
        description=(
            "Overall complexity read: clear = all/mostly light sentences; "
            "a_bit_heavy = some medium sentences, no heavy ones; "
            "overcomplicated = one or more heavy sentences"
        )
    )
    level_read: str = Field(
        description=(
            "A short, gentle, coach-tone CEFR-ish label for the verdict "
            "(e.g. '≈ B1 — clear and simple', '≈ B2 — still easy to follow', "
            "'≈ C1 architecture — heavier than you need here')"
        )
    )
    coach_message: str = Field(
        description=(
            "The warm headline instructor message, English, 1-2 sentences: "
            "affirm when clear/a_bit_heavy, gently redirect toward the "
            "simpler version when overcomplicated. Never harsh, never 'wrong'"
        )
    )
    sentences: list[SentenceRead] = Field(
        description="Every sentence the learner's answer was segmented into, each complexity-weighted"
    )
    skeleton: Skeleton = Field(
        description="The skeleton hiding in what the learner actually said"
    )


PROMPT = """# Role
You are a warm, encouraging German speaking COACH — not a grader. You look at ONE spoken answer from a German speaking drill ("Szenario-Sparring"): an in-character persona asked the learner ONE unpredictable question, and the learner gave ONE spoken answer. You coach the learner toward SIMPLER German sentence architecture. You NEVER say "wrong."

# The question the learner was asked
"{question}"

# What the learner said (raw speech-recognition transcript)
"{transcript}"

# Domain vocabulary for this scene (for the skeleton's vokabel_anker)
{ziel_vokabular}

# What you judge — ONE thing, and one thing only
COMPLEXITY vs SIMPLICITY of the sentence architecture. Nothing else.

# What you must NEVER judge — read this twice
Do NOT flag, correct, or even mention grammar, case, gender, adjective endings, verb conjugation, or vocabulary mistakes. A verb-final `weil`-clause is CORRECT German — it is NOT an error, it is a complexity LOAD. Another exercise owns grammar entirely. If ANY field you write mentions a grammar rule, an article, a case, a verb ending, or a vocabulary correction — you have failed this task.

# Why simpler is better
Nested German is where learners collapse under pressure. Short main clauses (verb in position 2, present or perfect tense) are clearer AND easier to get right. You are coaching the learner toward simple — that is the whole job.

# The transcript has no punctuation
This is SPOKEN German straight from speech recognition: there is no punctuation and no capitalization, and sentence boundaries are not marked. Segment it into sentences yourself, by meaning, before weighing each one. Forgive obviously misheard filler words ("ähm", "so") — they are not part of what you are weighing.

# Per-sentence `weight` — rate EVERY sentence you segment
- `light`: a single main clause, OR two main clauses joined by und/aber/oder/denn/deshalb/dann/also, with the verb in position 2, present or perfect tense. THIS IS THE TARGET — this is good. IMPORTANT: two main clauses coordinated with "und" is LIGHT, not heavy — do NOT tell the learner to split clean coordination. `simpler` MUST be null.
- `medium`: ONE subordinate clause that pushes the verb to the end (weil/dass/wenn/obwohl/da/während/damit), or one slightly heavier construction. Fine and common — note it lightly. `simpler` is optional.
- `heavy`: NESTING — two or more subordinate clauses, or the main verb stranded far at the end; OR a heavy construction — Präteritum other than war/hatte, Konjunktiv II beyond würde/könnte, Passiv, or Futur. `simpler` is REQUIRED and must rebuild the same meaning as short main clauses (verb position 2, present/perfect, joined with und/deshalb/dann).

# Overall `verdict`
- `clear`: all or mostly light sentences.
- `a_bit_heavy`: some medium sentences, no heavy ones.
- `overcomplicated`: one or more heavy sentences.

# `level_read`
Map the verdict to a short, gentle CEFR-ish label, kind and never clinical:
- clear → something like "≈ B1 — clear and simple"
- a_bit_heavy → something like "≈ B2 — still easy to follow"
- overcomplicated → something like "≈ C1 architecture — heavier than you need here"

# `coach_message` — English, warm, 1-2 sentences
- clear / a_bit_heavy → AFFIRM. Something like: "Clear and simple — people follow this easily. Nice." Adapt it to what they actually said.
- overcomplicated → GENTLE REDIRECT, never harsh, never "wrong". Something like: "You're building this heavier than you need to. In German, shorter sentences are actually clearer — break the big one into small pieces. Here's the lighter version."

# The skeleton
Extract the skeleton hiding in what the learner actually said — not the ideal answer, THEIRS:
- `kern`: the one sentence that is the real anchor, the one to keep, in their own German.
- `punkte`: 2-4 supporting beats they actually made (or clearly gestured toward), each one short idea.
- `absprung`: one clean closing line, in the spirit of what they said.
- `vokabel_anker`: the domain words from the scene's vocabulary list above that the learner actually used, or the ones that would naturally belong in a strong answer to this question.

# Output discipline
`coach_message` and `level_read` are warm English. Sentence `text` and `simpler`, and every skeleton field, stay in German, in the learner's own words.
"""


async def judge_structure(
    question: str,
    transcript: str,
    ziel_vokabular: list[str],
    user_id: str | None = None,
) -> StructureVerdict:
    """One structured-output judgement call: complexity-coaching only, never grammar."""
    llm = ProviderChatOpenAI(
        model=JUDGE_MODEL,
        base_url=openrouter_base_url,
        api_key=openrouter_api_key,
        # 10s/attempt x max_retries=2 ~= 31.5s worst case. Keep-alive reuse
        # disabled (judge_http_client()) so a silently-dropped pooled socket
        # (Railway NAT / OpenRouter LB, no RST) can't be handed out again —
        # that cost 61-120s user-facing hangs before this (2026-07-16 prod
        # traces).
        timeout=10,
        max_retries=2,
        http_async_client=judge_http_client(),
        # OBS-008: pinned, no fallback off Cerebras — see LEARNINGS.md / the
        # asymmetry comment in agents/conversation_agent.py.
        extra_body={"provider": {"order": ["cerebras"], "allow_fallbacks": False}},
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
        result, usage, response_metadata = unwrap_structured_output(await llm.ainvoke(prompt))
        record_generation_output(span, result.model_dump_json(), usage, response_metadata)
    return result
