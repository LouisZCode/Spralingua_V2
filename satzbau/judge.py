"""Structural diagnosis for Satzbau (clause-construction drill: order chips
into a correct German subordinate clause or question).

Only MISSES reach this module (the route's deterministic check green-lights
exact/``accepts``-listed matches at zero LLM cost). Unlike a typed gap-fill,
there is no vocabulary or spelling surface here at all — every chip IS a
correct word; the only thing the learner controls is ORDER. So the judge's
whole job is narrower than its siblings': does this order still perform the
required structure? German Mittelfeld order is flexible, so an order that
differs from the canonical ``answer`` is very often still correct German —
and ``items.yaml``'s ``accepts`` lists are deliberately left empty wherever
the item's author wasn't fully certain a second order was natural, so most
genuinely-valid-but-uncommon orders land here rather than on the
deterministic fast path. This judge, not ``accepts``, is the authority on
correctness — see the STEP 1 guard below for how it stays scoped to the ONE
structural decision each item tests and never drifts into grading anything
else (there is nothing else TO grade: no typos, no word choice, both were
fixed by the chip set).
"""

from typing import Optional

from agents.openrouter_llm import structured_judge_llm
from pydantic import BaseModel, Field

from agents.observability import (
    generation_span,
    record_generation_output,
    unwrap_structured_output,
)
from grammar import load_taxonomy

JUDGE_MODEL = "openai/gpt-oss-120b"


class ClauseDiagnosis(BaseModel):
    """Verdict on one chip ordering in a German clause-construction drill.
    Grades ONLY whether the order performs the required structure — never
    vocabulary (every chip is a fixed, correct word) and never style."""

    correct: bool = Field(
        description=(
            "True when the learner's chip order is valid, natural German "
            "performing the required structure — conjugated verb where the "
            "pattern demands it (clause-final for a relative/indirect-"
            "question/damit clause; zu attached correctly, including "
            "INSIDE a separable verb like anzurufen; verb-first or "
            "verb-second for a direct question) — even when the Mittelfeld "
            "order differs from the canonical answer, as long as that "
            "order is itself grammatical. A provided match list is only a "
            "shortcut; you are the final authority on correctness"
        )
    )
    note: Optional[str] = Field(
        default=None,
        description=(
            "REQUIRED whenever correct is false — AT MOST 14 words naming "
            "exactly where the structure broke. Never restate the full "
            "answer (it is shown separately). Null only when correct"
        ),
    )
    variant: Optional[str] = Field(
        default=None,
        description=(
            "Set ONLY when correct is true AND the built order differs "
            "from the canonical answer — ONE short English line on how the "
            "two read differently, or which order a German would more "
            "likely use. Null when the order matches the canonical answer, "
            "or when correct is false"
        ),
    )


PROMPT = """# Role
You grade ONE word-order decision in a German clause-building drill ("Satzbau"). The learner saw a fixed lead-in and an English task naming the meaning to build, then tapped a fixed set of word chips into order — nothing to type, nothing to misspell. Every chip is a correct German word; the ONLY thing the learner controlled is the SEQUENCE they placed them in.

# The item
- construction being tested: {label} — {description}
- lead-in (fixed, given to the learner): "{given}"
- task (the meaning the clause must express): "{task}"
- the canonical answer: "{full_answer}"

# What the learner built
"{full_typed}"

# STEP 1 — isolate the ONE structural decision, then forget everything else
This drill tests exactly one thing per item: where German puts the verb (and, for relative clauses / indirect questions / purpose clauses, which connector or pronoun form) — nothing else. There is no vocabulary error possible (every word was handed to the learner) and no spelling error possible (they only rearranged existing chips). Do not comment on word choice, style, or which Mittelfeld order you personally find most natural — grade the GRAMMATICALITY of the structure only.

## The Mittelfeld is FLEXIBLE — do not invent rules to reject it
Once the verb sits where the structure demands, the order of what comes before it is largely free, and several orders are equally correct German. Rejecting a valid order is far worse than accepting a merely unusual one: it teaches the learner a rule that does not exist. When in doubt about a Mittelfeld order, mark it CORRECT and use `variant` to say the canonical version reads more naturally.

Two real German rules you must not get backwards:
- **A pronoun object comes BEFORE a full noun-phrase subject**, not after. "damit es die Kinder nicht hören" is correct German — arguably more idiomatic than "damit die Kinder es nicht hören". Both are correct. There is NO rule that a subject must precede an object pronoun.
- **Unstressed pronouns sit as far left in the Mittelfeld as possible**, ahead of adverbials: "dass ich dich morgen sehe" is more natural than "dass ich morgen dich sehe", but both are grammatical.

Only mark `correct: false` when the VERB (or `zu`, or the connector, or the relative pronoun's case/gender) is genuinely wrong — never for a Mittelfeld rearrangement alone.

One exception to that Mittelfeld freedom: if the task names the exact word the sentence must open with ("Start with …" / "Continue with …"), an order that fronts something else instead — even the bare subject — is wrong, regardless of how grammatical the resulting V2 clause otherwise is.

# STEP 2 — grade
- `correct`: true when the built order is valid, grammatical German performing the required structure (verb in the position the pattern demands; zu attached correctly, including INSIDE a separable verb). German Mittelfeld order is flexible — an order that differs from the canonical answer above is very often STILL correct German that simply reads slightly differently. Judge the learner's actual sentence on its own grammatical merits, not by string-matching the canonical answer.
- `note`: REQUIRED whenever correct is false. ONE line, AT MOST 14 words, naming exactly which structural rule broke ("the conjugated verb has to be last in a relative clause" / "zu belongs inside anzurufen, not in front of it" / "ob replaces the question word in a yes/no question"). The correct answer is shown separately — never restate it. Null only when correct.
- `variant`: set ONLY when correct is true AND the built order is not identical to the canonical answer above. ONE short English line saying how the two versions read differently, or which one a German speaker would more likely say. Null when the order matches the canonical answer, or when correct is false.
"""


async def judge_clause(item: dict, order: list[str]) -> ClauseDiagnosis:
    """One structured-output diagnosis call over the item + the learner's
    built order."""
    pattern = load_taxonomy()[item["pattern_id"]]
    full_answer = " ".join([item["given"], *item["answer"]]).strip()
    full_typed = " ".join([item["given"], *order]).strip()
    # Cerebras-direct primary + OpenRouter fallback with 12s/leg deadline —
    # see agents/openrouter_llm.structured_judge_llm.
    # temperature=0: this is a verdict, not prose — the same answer
    # must always get the same grade (see structured_judge_llm).
    llm = structured_judge_llm(ClauseDiagnosis, temperature=0)
    prompt = (
        PROMPT.replace("{label}", pattern["label"])
        .replace("{description}", pattern["description"])
        .replace("{given}", item["given"])
        .replace("{task}", item["task"])
        .replace("{full_answer}", full_answer)
        .replace("{full_typed}", full_typed)
    )
    # OBS-007: the `satzbau-judge` child of the route's satzbau-attempt trace.
    with generation_span("satzbau-judge", model=JUDGE_MODEL, input_text=prompt) as span:
        result, usage, response_metadata = unwrap_structured_output(await llm.ainvoke(prompt))
        record_generation_output(span, result.model_dump_json(), usage, response_metadata)
    if result.correct:
        result.note = None
    else:
        result.variant = None
    return result
