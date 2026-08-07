"""Written-attempt diagnosis for Bauteil-Sätze (GRAM-002, Exercise A).

Only MISSES reach this module — the route's deterministic check already
green-lights an exact (or sentence-embedded) match at zero LLM cost. The
judge exists for the feedback the drill's pedagogy hangs on (design rule 6:
"separate wrong row from wrong column"): it names which of two independent
axes broke — the CASE the learner aimed at vs. the CARRIER, i.e. whether
each ending sits on the right element and is well-formed. Same Cerebras
``gpt-oss-120b`` structured-output wiring as ``satz/examiner.py``.
"""

import re
from typing import Optional

from agents.openrouter_llm import structured_judge_llm
from pydantic import BaseModel, Field

from agents.observability import (
    generation_span,
    record_generation_output,
    unwrap_structured_output,
)

JUDGE_MODEL = "openai/gpt-oss-120b"

_GENDER_LABEL = {"m": "masculine", "f": "feminine", "n": "neuter", "pl": "plural"}


class Diagnosis(BaseModel):
    """The two-axis verdict on a mismatched typed phrase."""

    correct: bool = Field(
        description=(
            "True ONLY when the learner's phrase is a fully correct solution "
            "that differs from the expected one trivially — the whole sentence "
            "typed around it, punctuation, capitalization, and a spelling slip "
            "in the noun/adjective STEM (handed to them verbatim in the raw "
            "parts — not graded). A changed, added or missing ENDING letter "
            "or umlaut is NEVER trivial"
        )
    )
    case_ok: bool = Field(
        description=(
            "The ROW: the learner's endings aim at the case the gap requires "
            "(false when the phrase is a well-formed phrase of a DIFFERENT case)"
        )
    )
    carrier_ok: bool = Field(
        description=(
            "The COLUMN: within the case the learner aimed at, every ending "
            "sits on the right element and is well-formed"
        )
    )
    note: Optional[str] = Field(
        default=None,
        description=(
            "One short English line (AT MOST 14 words) naming which axis broke "
            "and why; null when correct"
        ),
    )


PROMPT = """# Role
You diagnose one WRITTEN attempt in a German declension drill ("Bauteil-Sätze"). The learner saw a sentence with a gap and the raw, uninflected parts of a phrase, and had to type the phrase correctly inflected for that gap.

# The item
- sentence: "{frame}"
- raw parts (uninflected): {parts}
- the gap requires: {requirement}
- correct solution: "{expected}"

# What the learner typed
"{typed}"

# STEP 1 — the stem is not on trial
The raw parts above were handed to the learner verbatim — they only supply the ENDINGS. A spelling slip in the noun or adjective STEM itself (a doubled letter, a dropped umlaut, a typo) is not a declension error: diagnose the endings as if the stem were spelled correctly, and never let a stem slip flip any field below.

## This is where graders go wrong
- required nominative, expected "ein guter Job", typed "ein guter Jobb" → correct: true, case_ok: true, carrier_ok: true. "Jobb" is a stem typo; the ending "-er" on "guter" is exactly right.
- required accusative, expected "einen alten Freund", typed "einen alten Freund, mein bester Freundd" → correct: true, case_ok: true, carrier_ok: true. The graded phrase is right; "Freundd" only appears in the extra words wrapped around it.
- required dative, expected "einem netten Kunden", typed "einem nettem Kunden" → correct: false, case_ok: true, carrier_ok: false. THIS is a real ending error — "einem" already carries the case marking, so the adjective must take the weak "-en", not "-em".

# STEP 2 — diagnose the two axes, never conflated
A declined-phrase error has two independent axes. Work in two steps:

1. Identify the learner's AIM — with a charitable default: the aim is the REQUIRED case, unless their phrase is a clean, fully well-formed build of a DIFFERENT case (then that case is the aim). A phrase with any malformed or inconsistent ending is an execution problem within the required case — never evidence of a different aim. ("ein alter Freund" is a clean nominative → aim nominative; "ein gute Job" is a clean build of nothing → aim = the required case.)
2. Score the axes independently:
   - `case_ok` — the ROW: aim == required case. Nothing else counts here — a phrase full of broken endings can still clearly aim at the right case.
   - `carrier_ok` — the COLUMN: judged AGAINST THE AIM, not the required case. For the case the learner aimed at, is every ending well-formed and on the right element? The strong (case-marking) ending appears exactly once per phrase: on the article when it can carry it, on the adjective when the article is naked (ein/kein and the possessives carry no ending in masculine nominative and in neuter nominative/accusative). A weak (n-declension) noun's -(e)n belongs to this axis too.

Consequences you must respect:
- A perfectly built phrase of the WRONG case → case_ok=false, carrier_ok=TRUE (they can build the phrase; they misread what the sentence demands).
- The right case aimed at, with a broken or misplaced ending → case_ok=TRUE, carrier_ok=false.
- Both false only when the endings aim at no case consistently, or aim at a wrong case AND are broken within it.
- Capitalization and punctuation break neither axis.

Then:
- `correct` — true ONLY when the learner's phrase is a fully correct solution and differs from the expected one trivially: the whole sentence typed around it, punctuation, capitalization, and a stem spelling slip (see STEP 1). A changed, added or missing ENDING letter or umlaut is NEVER trivial.
- `note` — when correct=false: ONE short English line (AT MOST 14 words) naming which axis broke and teaching the why. Name the axes in PLAIN ENGLISH ONLY — "case" and "endings" — and NEVER write the internal field names `case_ok` or `carrier_ok` (the learner never sees those). The corrected phrase is shown to the learner separately — never restate it. When correct=true: null.

# Worked examples
- required accusative, expected "einen alten Freund", typed "ein alter Freund" → case_ok=false, carrier_ok=true, note: "A well-built nominative — but 'besuchen' takes a direct object: accusative."
- required nominative, expected "ein guter Job", typed "ein gute Job" → case_ok=true, carrier_ok=false, note: "Right case — but naked 'ein' hands the strong -er to the adjective."
- required dative, expected "einem netten Kunden", typed "einem netten Kunde" → case_ok=true, carrier_ok=false, note: "'Kunde' is a weak noun — it needs -n outside the nominative."
- required accusative, expected "den schwarzen Mantel", typed "ich nehme den schwarzen mantel" → correct=true, case_ok=true, carrier_ok=true, note: null.
"""


def _sanitize_note(note: str | None) -> str | None:
    """Belt-and-suspenders guard (TASK 7): the prompt already tells the model
    never to leak internal field names into the learner-facing note, but
    defend against drift anyway — swap `case_ok`/`carrier_ok` for their
    plain-English axis names, and reflow a leaked "<axis> broken: ..."
    lead-in into a natural "<Axis>: ..." opener (e.g. "endings broken: noun
    needs -n" -> "Endings: noun needs -n")."""
    if not note:
        return note
    cleaned = re.sub(r"\bcase_ok\b", "case", note, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bcarrier_ok\b", "endings", cleaned, flags=re.IGNORECASE)
    m = re.match(r"^(case|endings)\s+broken:\s*", cleaned, flags=re.IGNORECASE)
    if m:
        cleaned = f"{m.group(1).capitalize()}: {cleaned[m.end():]}"
    return cleaned


async def judge_attempt(item: dict, typed: str) -> Diagnosis:
    """One structured-output diagnosis call over the item + typed answer."""
    # Cerebras-direct primary + OpenRouter fallback with 12s/leg deadline —
    # see agents/openrouter_llm.structured_judge_llm.
    llm = structured_judge_llm(Diagnosis)
    requirement = f"{item['case']}, {_GENDER_LABEL[item['gender']]}"
    prompt = (
        PROMPT.replace("{frame}", item["frame"])
        .replace("{parts}", " · ".join(item["parts"]))
        .replace("{requirement}", requirement)
        .replace("{expected}", item["answer"])
        .replace("{typed}", typed)
    )
    # OBS-007: the `bauteil-judge` child of the route's bauteil-attempt trace —
    # opened as current span, so it nests (and inherits the practice session)
    # automatically.
    with generation_span("bauteil-judge", model=JUDGE_MODEL, input_text=prompt) as span:
        result, usage, response_metadata = unwrap_structured_output(await llm.ainvoke(prompt))
        record_generation_output(span, result.model_dump_json(), usage, response_metadata)
    result.note = _sanitize_note(result.note)
    if result.correct:
        # A named axis break on an accepted answer only confuses.
        result.case_ok = True
        result.carrier_ok = True
        result.note = None
    return result
