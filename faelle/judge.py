"""Written-attempt diagnosis for Fälle (GRAM-006 Proposal-1, "the case
cluster").

Only MISSES reach this module (the route's deterministic check green-lights
exact/embedded matches at zero LLM cost). Where the chunk drill's judge names
which ELEMENT broke, this judge grades ONE thing only — the case decision the
gap tests — and never comments on the rest of the sentence, spelling, or
style outside the gap; all of that was given to the learner untested.

Its third field, ``means_instead``, is the highest-value feedback in the
whole case system: a wrong case is very often still grammatical German that
simply means something else ("auf dem Tisch" instead of "auf den Tisch"
isn't broken — it says the vase was already there). Same Cerebras
``gpt-oss-120b`` wiring as every sibling judge.
"""

from typing import Optional

from agents.openrouter_llm import structured_judge_llm
from pydantic import BaseModel, Field

from agents.observability import (
    generation_span,
    record_generation_output,
    unwrap_structured_output,
)

JUDGE_MODEL = "openai/gpt-oss-120b"


class CaseDiagnosis(BaseModel):
    """Verdict on one gap-fill in a German case drill. Grades ONLY the case
    decision the gap tests — never the rest of the sentence, spelling
    outside the gap, or style."""

    correct: bool = Field(
        description=(
            "True when the words FILLING THE GAP are the expected case "
            "form. Judge the gap alone — typos, missing umlauts and wrong "
            "endings anywhere ELSE in the typed sentence are irrelevant "
            "and never make this false. A different case, wrong-gender "
            "article, or wrong preposition IN THE GAP is never trivial"
        )
    )
    note: Optional[str] = Field(
        default=None,
        description=(
            "REQUIRED whenever correct is false — AT MOST 14 words naming "
            "WHICH case decision broke and why. Null only when correct"
        ),
    )
    means_instead: Optional[str] = Field(
        default=None,
        description=(
            "Set ONLY when the typed answer is itself grammatical German "
            "that a native speaker reads as meaning something DIFFERENT — "
            "most often the other case of the same two-way preposition "
            "(location instead of direction, or vice versa). ONE short "
            "English line stating what the learner's version actually "
            "says. Null when the answer is simply ungrammatical with no "
            "coherent alternate meaning, or when correct"
        ),
    )


PROMPT = """# Role
You grade ONE gap in a German case drill ("Fälle"). The learner saw a full, correct German sentence with one gap and had to type the words a case decision governs — an article, a possessive, a pronoun, or a preposition + article. Everything outside the gap was given to them and is not in question.

# The item
- sentence: "{frame}"
- correct answer for the gap: "{expected}"
- the rule being tested: {rule}

# What the learner typed
"{typed}"

# STEP 1 — isolate the gap, then forget the rest
The learner often retypes the whole sentence instead of just the missing words. When they do, line their text up against the sentence above, take ONLY the words standing where the `___` is, and grade those. Everything else they typed is a copy of text WE gave them — it is not their work and it is not what this drill tests.

## This is where graders go wrong
A typo, a missing umlaut, or a wrong ending in a frame word is NOT a case error and must NEVER make `correct` false. Marking a learner wrong for mistyping a word they were handed teaches them nothing about case and punishes them for something else entirely.

- expected "meinem" · typed "Ich wohne mit meinem Brudr zusammen." → **correct: true**. "Brudr" is a typo in a given word; the gap holds "meinem", which is right.
- expected "meinen" · typed "Das Geschenk ist fur meinen Vater." → **correct: true**. "fur" is missing its umlaut, but "für" was given to them — the gap holds "meinen", which is right.
- expected "meiner" · typed "Ich helfe meiner Muter im Haushalt." → **correct: true**. Same story: the gap is right.
- expected "auf den" · typed "Ich stelle die Vase auf dem Tisch." → **correct: false**. THIS is a case error — it is in the gap.

# STEP 2 — grade
- `correct` — true when the gap words are the expected case form, allowing capitalization and stray punctuation. A different case, a wrong-gender article, or a wrong preposition IN THE GAP is never trivial.
- `note` — REQUIRED whenever correct=false; never leave it null on a wrong verdict. ONE line, AT MOST 14 words, naming exactly which case decision broke ("that's dative, not accusative here" / "die Kinder is plural, not feminine dative"). The correct answer is shown separately — never restate it. Null only when correct.
- `means_instead` — the highest-value field. Set it ONLY when the typed answer is itself grammatical, well-formed German that a native speaker would read as meaning something DIFFERENT from the intended sentence — most often the OTHER case of the same preposition (dative instead of accusative on a two-way preposition, or vice versa), which flips location vs. direction. When that's true, write ONE short English line stating what the learner's version actually says. Leave it null whenever the typed answer is simply wrong or ungrammatical with no coherent alternate meaning, or when correct=true.
"""


async def judge_case(item: dict, typed: str) -> CaseDiagnosis:
    """One structured-output diagnosis call over the item + typed answer."""
    # Cerebras-direct primary + OpenRouter fallback with 12s/leg deadline —
    # see agents/openrouter_llm.structured_judge_llm.
    # temperature=0: this is a verdict, not prose — the same answer
    # must always get the same grade (see structured_judge_llm).
    llm = structured_judge_llm(CaseDiagnosis, temperature=0)
    prompt = (
        PROMPT.replace("{frame}", item["frame"])
        .replace("{expected}", item["answer"])
        .replace("{rule}", item["rule"])
        .replace("{typed}", typed)
    )
    # OBS-007: the `faelle-judge` child of the route's faelle-attempt trace.
    with generation_span("faelle-judge", model=JUDGE_MODEL, input_text=prompt) as span:
        result, usage, response_metadata = unwrap_structured_output(await llm.ainvoke(prompt))
        record_generation_output(span, result.model_dump_json(), usage, response_metadata)
    if result.correct:
        result.note = None
        result.means_instead = None
    return result
