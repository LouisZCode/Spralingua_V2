"""Written-attempt diagnosis for Feste Verbindungen (GRAM-002, Exercise D).

Only MISSES reach this module (the route's deterministic check green-lights
exact/embedded matches at zero LLM cost). Verbindungen now spans two
different KINDS of decision, and the judge must never blur them:

- the original chunk patterns (``reflexivpronomen``,
  ``verben-mit-praepositionen``, ``da-wo-komposita``) — the axes are the
  chunk's ELEMENTS: the reflexive pronoun (missing, extra, or wrong), the
  fixed preposition, the case it governs, or the da-/wo-compound.
- the A1/A2 hygiene batch (``subjekt-verb-endung``, ``sein-vs-haben``,
  ``nicht-vs-kein``, ``am-um-im-zeit``, ``komparativ-form``) — each is its
  own single decision (a verb ending, a fixed-pair verb, a negator, a time
  preposition, a comparative form) that has NOTHING to do with reflexivity
  or prepositions. Handed a ``nicht-vs-kein`` item, a prompt written only
  around chunk language will "diagnose" nonsense like a missing reflexive
  pronoun — worse than no feedback, because it teaches a category error.

The prompt below carries the item's ``pattern_id`` + taxonomy label/
description so the judge always knows which decision it is grading, and
picks per-family diagnosis guidance accordingly. Same Cerebras
``gpt-oss-120b`` wiring as the sibling judges.
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

# The three original lexicon-chunk patterns — their diagnosis guidance is
# kept VERBATIM below (see _CHUNK_GUIDANCE); those items are in production
# and must not regress.
_CHUNK_PATTERNS = {"reflexivpronomen", "verben-mit-praepositionen", "da-wo-komposita"}

# VERBATIM from the pre-widening judge's `note` field/PROMPT text — do not
# reword a single clause of this. The new-pattern lines below follow the
# same "name exactly what broke and why" spirit, one per pattern (GRAM-002
# widen), but this block is byte-for-byte what shipped before the widening.
_CHUNK_GUIDANCE = (
    "`note` — when correct=false: ONE short English line (AT MOST 14 words) "
    "naming exactly WHICH element broke and teaching the why: the reflexive "
    "pronoun (missing where the verb demands one, added where it doesn't, "
    "or the wrong person), the fixed preposition (wrong one), the case the "
    "preposition governs (wrong article), or the da-/wo-compound (malformed "
    "or split). The correct chunk is shown to the learner separately — "
    "never restate the full fix. Good notes: \"'warten' isn't reflexive — "
    "no pronoun needed.\" / \"'sich freuen auf' — the fixed preposition is "
    "'auf', not 'über'.\" / \"'vor' after 'Angst haben' takes the dative.\" "
    "/ \"Preposition + 'es' fuses into one word: da + auf.\" When "
    "correct=true: null."
)

_NEW_PATTERN_GUIDANCE = {
    "nicht-vs-kein": (
        "This item tests nicht vs. kein. `note` must name which negator "
        "was needed and why: kein negates a bare noun phrase (no article "
        "yet); nicht negates a verb, an adjective, or a noun phrase that "
        "already has an article or possessive in front of it."
    ),
    "sein-vs-haben": (
        "This item tests sein vs. haben in a fixed pair. `note` must name "
        "which verb the fixed pair uses (Hunger/Durst/Angst/Recht → haben; "
        "müde/an age → sein) and the correctly conjugated form for the "
        "subject."
    ),
    "subjekt-verb-endung": (
        "This item tests the present-tense verb ending. `note` must name "
        "which grammatical person's ending was needed (ich -e, du -st, "
        "er/sie/es -t, wir/sie/Sie -en, ihr -t) and, if the verb is a "
        "stem-changer or a -t/-d stem, what changed."
    ),
    "am-um-im-zeit": (
        "This item tests the time preposition. `note` must name which one "
        "fits: am for a day or date, um for a clock time, im for a month "
        "or season."
    ),
    "komparativ-form": (
        "This item tests the comparative form. `note` must name the "
        "correct form: adjective + -er (with an umlaut where one applies), "
        "or the correct irregular (besser/mehr/lieber/höher) — and that "
        "'mehr + adjective' is never how German builds a comparative."
    ),
}


def _diagnosis_guidance(pattern_id: str) -> str:
    if pattern_id in _CHUNK_PATTERNS:
        return _CHUNK_GUIDANCE
    guidance = _NEW_PATTERN_GUIDANCE.get(pattern_id)
    if guidance is None:
        raise ValueError(f"verbindungen/judge.py: no diagnosis guidance for pattern {pattern_id!r}")
    return guidance


class Diagnosis(BaseModel):
    """Verdict on a mismatched gap-fill attempt."""

    correct: bool = Field(
        description=(
            "True when the filled sentence is correct German AND the gap "
            "words exercise the pattern being tested the way the reference "
            "solution does. The reference is ONE correct answer, not the "
            "only one — a different determiner or a contraction can be "
            "equally right. False when the German is wrong, or when the "
            "answer sidesteps the pattern instead of demonstrating it"
        )
    )
    note: Optional[str] = Field(
        default=None,
        description=(
            "One short English line (AT MOST 14 words) naming exactly which "
            "element broke and why, scoped to the ONE decision this item's "
            "pattern tests; null when correct"
        ),
    )


PROMPT = """# Role
You diagnose one WRITTEN attempt in a German gap-fill drill ("Feste Verbindungen"). The learner saw a sentence with a gap and had to type the missing word(s). Everything outside the gap is correct German the learner was NOT tested on and is not in question.

# The item
- pattern being tested: {pattern_id} — {label}. {description}
- sentence: "{frame}"
- the chunk/rule being tested: {chunk}
- ONE correct solution for the gap (a reference, NOT the only right answer): "{expected}"

# What the learner typed
"{typed}"

# STEP 1 — isolate the gap, then forget the rest
The learner often retypes the whole sentence instead of just the missing words. When they do, line their text up against the sentence above, take ONLY the words standing where the `___` is, and grade those. Everything else they typed is a copy of text WE gave them — it is not their work and it is not what this drill tests.

## This is where graders go wrong
A typo, a missing umlaut, or a wrong ending in a FRAME word is NOT an error in this drill and must NEVER make `correct` false. Marking a learner wrong for mistyping a word they were handed teaches them nothing about the pattern being tested and punishes them for something else entirely.

- pattern nicht-vs-kein · expected "kein" · typed "Ich habe kein Autoo." → **correct: true**. "Autoo" is a typo in a given word; the gap holds "kein", which is right.
- pattern sein-vs-haben · expected "habe" · typed "Ich habe Hunge." → **correct: true**. "Hunge" is a typo in a given word; the gap holds "habe", which is right.
- pattern reflexivpronomen · expected "mich auf" · typed "Ich freue mich auf das Wochenened." → **correct: true**. "Wochenened" is a typo in a given word; the gap holds "mich auf", which is right.
- pattern nicht-vs-kein · expected "kein" · typed "Ich habe nicht Auto." → **correct: false**. THIS is a real error — the wrong negator landed IN the gap.

# STEP 2 — the reference solution is not an answer key
The solution above is ONE right answer. German usually allows several. Ask TWO questions, and mark correct=true only if BOTH pass:

1. **Is the filled sentence correct German?** Read the frame with the learner's words in the gap and judge that sentence.
2. **Does it exercise the pattern being tested?** The pattern names ONE decision — the reflexive pronoun, the fixed preposition, the case it governs, the negator, the verb ending. The learner must get THAT decision right. Everything the pattern does not pin is theirs to choose.

## This is where graders go wrong — a different word is not a wrong word
Whatever the pattern does not pin is free. A **possessive instead of a definite article** (or the reverse) is free unless the pattern is about article type. A **contraction** is free: "beim" IS "bei dem", "zum" IS "zu dem", "ins" IS "in das" — same grammar, same case, and the contraction is usually the more natural German. Rejecting a learner for these teaches them nothing and is simply wrong.

- pattern verben-mit-praepositionen · reference "an die" · typed "an meine" (frame "Ich denke oft ___ Zeit in Spanien.") → **correct: true**. "denken an" is there and the case is still Akkusativ; definite vs. possessive is not what this pattern tests.
- pattern verben-mit-praepositionen · reference "bei dem" · typed "beim" → **correct: true**. Same words, fused. Never fail a learner for writing the contraction.
- pattern verben-mit-praepositionen · reference "an die" · typed "an der" → **correct: false**. CONTROL: dative where the chunk governs accusative — the case IS the pattern.
- pattern verben-mit-praepositionen · reference "an die" · typed "über die" → **correct: false**. CONTROL: correct-looking German, but it swaps the fixed preposition, so it never demonstrates "denken an".
- pattern nicht-vs-kein · reference "kein" · typed "ein" → **correct: false**. CONTROL: "Ich habe ein Auto." is fine German, but the item tests NEGATION and this answer sidesteps it. Prong 1 alone would wrongly pass this — prong 2 is what catches it.
- pattern reflexivpronomen · reference "mich auf" · typed "auf" → **correct: false**. CONTROL: the missing reflexive pronoun IS the pattern.

# STEP 3 — grade
- `correct` — apply the two prongs above. Capitalization, stray punctuation, and typos OUTSIDE the gap never matter. A wrong ENDING, a wrong CASE, a wrong fixed PREPOSITION or a missing/extra REFLEXIVE PRONOUN inside the gap always matters.
- `note` — REQUIRED whenever correct=false: ONE short English line (AT MOST 14 words) naming exactly what broke, scoped to the ONE decision this item's pattern tests (see below). Describe the learner's actual mistake, NOT the distance from the reference — "that's dative, the chunk needs accusative" teaches; "should be 'an die'" does not. The correct answer/chunk is shown to the learner separately — never restate the full fix. Null only when correct.

## How to diagnose — this pattern's decision
{diagnosis_guidance}
"""


async def judge_chunk(item: dict, typed: str) -> Diagnosis:
    """One structured-output diagnosis call over the item + typed answer."""
    pattern = load_taxonomy()[item["pattern_id"]]
    # Cerebras-direct primary + OpenRouter fallback with 12s/leg deadline —
    # see agents/openrouter_llm.structured_judge_llm.
    # temperature=0: this is a verdict, not prose — the same answer must
    # always get the same grade (see structured_judge_llm).
    llm = structured_judge_llm(Diagnosis, temperature=0)
    prompt = (
        PROMPT.replace("{pattern_id}", item["pattern_id"])
        .replace("{label}", pattern["label"])
        .replace("{description}", pattern["description"])
        .replace("{frame}", item["frame"])
        .replace("{chunk}", item["chunk"])
        .replace("{expected}", item["answer"])
        .replace("{typed}", typed)
        .replace("{diagnosis_guidance}", _diagnosis_guidance(item["pattern_id"]))
    )
    # OBS-007: the `verbindungen-judge` child of the route's verbindungen-attempt trace.
    with generation_span("verbindungen-judge", model=JUDGE_MODEL, input_text=prompt) as span:
        result, usage, response_metadata = unwrap_structured_output(await llm.ainvoke(prompt))
        record_generation_output(span, result.model_dump_json(), usage, response_metadata)
    if result.correct:
        result.note = None
    return result
