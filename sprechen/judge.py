"""Spoken-attempt judge for Sprechen & transkribieren (GRAM-002, Exercise B).

The learner speaks against a constrained task; the judge answers two things
and ONLY two things: was the constraint followed (the task, counted), and did
the TARGET structure fire correctly wherever it was attempted. Other grammar
— articles, endings, vocabulary — is deliberately ignored (design rule 3:
the rule is the objective; other drills own the rest). Same Cerebras
``gpt-oss-120b`` structured-output wiring as ``satz/examiner.py``.
"""

from typing import Optional

from loguru import logger
from agents.openrouter_llm import structured_judge_llm
from pydantic import BaseModel, Field

from agents.observability import (
    generation_span,
    record_generation_output,
    unwrap_structured_output,
)
from grammar import load_taxonomy

JUDGE_MODEL = "openai/gpt-oss-120b"


class Slip(BaseModel):
    """One place the TARGET structure broke — the learner's words, repaired."""

    quote: str = Field(
        description="The learner's own words where it broke, VERBATIM from the transcript"
    )
    corrected: str = Field(description="The minimal repair of that quote, in German")
    note: str = Field(
        description=(
            "One short English line (AT MOST 10 words) naming the broken "
            "rule — never a transcription artifact"
        )
    )


class SpokenVerdict(BaseModel):
    """Constraint compliance + target-structure slips. Nothing else is judged
    — including how Deepgram transcribed it: a misspelling, a dropped
    filler, or a homophone swap in the transcript is never a slip."""

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
        description=(
            "How many times the TARGET structure was produced correctly — "
            "judge what was spoken, never how the transcript spells it"
        )
    )
    hit_quotes: list[str] = Field(
        default_factory=list,
        description=(
            "One entry per counted hit: the learner's own words, VERBATIM "
            "from the transcript, where the target structure fired "
            "correctly (same verbatim rule as slips — the UI anchors this "
            "exact span in the transcript to highlight it). Exactly `hits` "
            "entries; must never overlap any slip quote"
        ),
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

# STEP 1 — separate transcription noise from a real structural break
This is speech recognition, not something the learner typed — they never saw these words on a screen. A misspelling, a missing umlaut, a homophone Deepgram wrote instead of the word actually spoken ("das" for "dass", "seit" for "seid"), a dropped filler, or a missing sentence break is an ARTIFACT OF TRANSCRIPTION, not something the learner got wrong. None of that may cost a hit or create a slip — only the actual shape of what they said (word order, verb form, connector choice) is graded.

## This is where graders go wrong
- target: nebensatz-verbende, verb clause-final · transcript "ich bleibe heute zuhause weil ich mich nicht gut fühle ähm" → **hit**. "ähm" is a filler the recognizer kept in; the clause itself already has the verb last.
- target: nebensatz-verbende, verb clause-final · transcript "ich glaube das er heute kommt" → **hit**. Deepgram wrote "das" for "dass" — they sound identical, so this is a transcription spelling, not a wrong connector; the verb is still last, which is what's graded.
- target: v2-wortstellung, verb-second main clause · transcript "heute abend geh ich ins kino" → **hit**. "geh" for "gehe" is the recognizer trimming an ending, not the learner dropping it — the verb still sits in position 2.
- target: nebensatz-verbende, verb clause-final · transcript "ich bleibe zuhause weil ich bin müde" → **slip**. THIS is a real break: the verb sits in position 2 of the weil-clause instead of last. No transcription artifact explains that — it is the word order itself.

# STEP 2 — grade
- The transcript comes from speech recognition: IGNORE punctuation and capitalization, forgive obviously misheard small words and fillers ("ähm"), and judge the sentences the learner most plausibly said. Sentence boundaries may be missing — infer them.
- `constraint_met` — did the learner ATTEMPT the task in the required quantity? Count attempts, not quality: an element with broken word order still counts toward the constraint (its break is recorded as a slip instead). constraint_met=false ONLY when the elements the forces section names are missing or too few (said "weil" once when two were asked, skipped a required verb). Count ONLY the named elements — clauses, conjunctions, openers, verbs. NEVER count "sentences": speech recognition strips the boundaries, so two spoken sentences often arrive joined as one.
- `constraint_note` — when constraint_met=false: one short English line naming what's MISSING (never a grammar comment). null when met.
- CONSISTENCY — `constraint_met`/`constraint_note` must agree with the `hits`/`slips` you are about to report for the SAME attempt; never recount differently for the note than you counted for the hits. Worked example: task "start every sentence with something other than the subject... At least 3 sentences" (forces: at least 3 fronted-opener main clauses) · transcript "Leider habe ich nicht viel Zeit für meine Hobbys. Am Alltag arbeite ich und lerne ich Deutsch. Am Wochenende versuche ich mehr Zeit mit meiner Frau verbringen." → three opener clauses attempted, verb in position two in each → hits=3, slips=0 → constraint_met=**true**, constraint_note=null. Reporting hits=3 (three hit_quotes) and THEN writing constraint_note "only two required opener clauses, need three" is the exact contradiction to avoid — you already counted three when you listed the hit_quotes.
- `hits` — how many of the attempted elements produced the TARGET structure correctly. When the forces section names SEVERAL checks for one element (e.g. clause-internal verb position AND verb mood/form AND the word order of the following main clause), that element counts as a hit only when EVERY named check passes.
- `hit_quotes` — one entry per counted hit, in the order they occurred: the learner's own words, copied VERBATIM from the transcript — never paraphrase, trim, or fix them; the UI locates this exact span in the transcript to highlight it. Produce EXACTLY `hits` entries — no more, no fewer — and never quote a span that overlaps a `slips` quote (a span is either a hit or a slip, never both).
- `slips` — every ATTEMPTED element where the target structure broke: `quote` (the learner's own words, copied VERBATIM from the transcript — never paraphrase, trim, or fix them; the UI locates this exact span in the transcript to highlight it), `corrected` (the minimal repair, in German, keeping their words), `note` (ONE English line, AT MOST 10 words, naming the broken rule — the correction sits next to it, so name the WHY, never restate the fix). One element can therefore yield several slips when several named checks break — record each separately, and each note must name WHICH check broke (a correct word order with a wrong verb form is a form slip, not an order slip, and vice versa).
- An element that simply doesn't attempt the target (a subject-first sentence when the task asks for fronting, a sentence with no weil) is NOT a slip — it is grammatical German that dodged the task, and it counts only against the constraint.
- Judge ONLY the target structure. Wrong articles, adjective endings, vocabulary choices, or other unrelated grammar are NOT slips here and must be ignored — other drills own those. A sentence still counts as a hit when its only errors are unrelated to the target structure.
"""


async def judge_spoken(task: dict, transcript: str) -> SpokenVerdict:
    """One structured-output judgement call over the task + transcript."""
    # Cerebras-direct primary + OpenRouter fallback with 12s/leg deadline —
    # see agents/openrouter_llm.structured_judge_llm.
    llm = structured_judge_llm(SpokenVerdict)
    p = load_taxonomy()[task["pattern_id"]]
    pattern_line = f'{p["label"]} — "{p["wrong"]}" → "{p["right"]}"'
    prompt = (
        PROMPT.replace("{prompt}", task["prompt"])
        .replace("{forces}", task["forces"])
        .replace("{pattern_line}", pattern_line)
        .replace("{transcript}", transcript)
    )
    # OBS-007: the `sprechen-judge` child of the route's sprechen-attempt trace.
    with generation_span("sprechen-judge", model=JUDGE_MODEL, input_text=prompt) as span:
        result, usage, response_metadata = unwrap_structured_output(await llm.ainvoke(prompt))
        record_generation_output(span, result.model_dump_json(), usage, response_metadata)

    # GRAM-008: the LLM's own constraint_met/constraint_note can contradict
    # the hits/slips it just reported for the SAME attempt (hits=3, three
    # hit_quotes, then constraint_note "only two required opener clauses,
    # need three" — trace 7745ca97, task v2-freizeit). The prompt's own STEP
    # 2 rule already says an attempted element with broken structure still
    # counts toward the constraint ("count attempts, not quality"), so derive
    # constraint_met from the counts it already produced whenever that would
    # flip a false verdict true. Deliberately asymmetric: if the LLM said
    # constraint_met=True but attempted < min_elements, its verdict is kept
    # as-is — this only rescues false negatives, never manufactures a false
    # positive on top of one the model already gave.
    # Tasks that require N *different* elements (three different modals, three
    # named separable verbs — `distinct: true` in tasks.yaml) are exempt: the
    # counts can't tell "kann ×3" from "will/muss/kann", and the T3 tester
    # produced exactly that false pass on modal-drei before this exemption.
    min_elements = task.get("min_elements")
    attempted = result.hits + len(result.slips)
    if (
        min_elements is not None
        and not task.get("distinct")
        and not result.constraint_met
        and attempted >= min_elements
    ):
        logger.info(
            "sprechen.constraint_override task={} min_elements={} attempted={} "
            "(hits={} slips={}) — LLM said constraint_met=False, note={!r}",
            task.get("id"),
            min_elements,
            attempted,
            result.hits,
            len(result.slips),
            result.constraint_note,
        )
        result.constraint_met = True

    if result.constraint_met:
        result.constraint_note = None
    return result
