"""Pattern -> exercise registry for Clara's interactive-exercise loop
(AGENT-00X, "the teacher can hand out a real item").

Clara's room has never had a written drill; this is the bridge. It maps
``grammar/taxonomy.yaml`` pattern ids onto items drawn from FIVE existing
typed-answer drill catalogs — faelle, satzbau, zeitfaerbung, verbindungen,
bauteil — and normalizes each into ONE generic card shape the frontend can
render without knowing which drill it came from. Grading reuses each drill's
own deterministic matcher (and judge LLM, where that drill has one) by
IMPORTING its module-level functions — nothing here reimplements a check or
duplicates an ``items.yaml``. No drill module is edited to make this work.

Coverage is v1-complete: all five target drills turned out to be reusable
as-is (generic catalog only — CONT-002 personal/forged items are skipped;
those live per-user in ``user_drill_items`` and add nothing a random generic
item doesn't already give this room). If a future drill can't be reused
without refactoring the drill itself, drop it here and record why — do not
edit the drill to fit.

Every catalog loader below is the drill's own ``lru_cache``d ``load_items``,
so this module builds nothing at import beyond a few closures; the actual
YAML parse happens (and is cached) the first time any route touches it.
"""

import random
from dataclasses import dataclass
from typing import Awaitable, Callable

from bauteil.content import TARGET_PATTERNS as _BAUTEIL_PATTERNS, load_items as _load_bauteil_items
from bauteil.judge import judge_attempt as _judge_bauteil
from bauteil.routes import _matches as _bauteil_matches

from faelle.content import TARGET_PATTERNS as _FAELLE_PATTERNS, load_items as _load_faelle_items
from faelle.judge import judge_case as _judge_faelle
from faelle.routes import _matches as _faelle_matches

from satzbau.content import TARGET_PATTERNS as _SATZBAU_PATTERNS, load_items as _load_satzbau_items
from satzbau.judge import judge_clause as _judge_satzbau
from satzbau.routes import _matches as _satzbau_matches, _shuffle_chips as _satzbau_shuffle_chips

from verbindungen.content import (
    TARGET_PATTERNS as _VERBINDUNGEN_PATTERNS,
    load_items as _load_verbindungen_items,
)
from verbindungen.judge import judge_chunk as _judge_verbindungen
from verbindungen.routes import _matches as _verbindungen_matches

from zeitfaerbung.content import (
    DOPPELDEUTIG_GROUPS as _ZF_DOPPEL_GROUPS,
    FORM_TO_FAMILY as _ZF_FORM_TO_FAMILY,
    load_items as _load_zeitfaerbung_items,
)
from zeitfaerbung.routes import _recognized_tokens as _zf_recognized_tokens

# Normalized verdict every ``grade`` callable returns: (correct, expected, note).
Verdict = tuple[bool, str, "str | None"]


# --------------------------------------------------------------------------
# Card builders — "what would that drill's own UI show as the task?" in one
# short instruction line + the exercise text itself. Answers/rules never
# appear here (same server-side-secrecy contract every drill's /round obeys).
# --------------------------------------------------------------------------

def _faelle_card(item: dict) -> dict:
    return {
        "instruction": "Fill in the missing word(s) to complete the German sentence.",
        "prompt": item["frame"],
        "expected_shape": "a word or short phrase",
    }


def _satzbau_card(item: dict) -> dict:
    shuffled = _satzbau_shuffle_chips(item["chips"], item["answer"])
    lines = [item["task"]]
    if item["given"]:
        lines.append(f'Start: "{item["given"]}"')
    lines.append(f"Arrange these words to finish it: {', '.join(shuffled)}")
    return {
        "instruction": "Put the words in the correct order to build the sentence.",
        "prompt": "\n".join(lines),
        "expected_shape": "the words in order, separated by spaces",
    }


def _verbindungen_card(item: dict) -> dict:
    return {
        "instruction": "Complete the fixed phrase — type the missing word(s).",
        "prompt": item["frame"],
        "expected_shape": "a word or short phrase",
    }


def _bauteil_card(item: dict) -> dict:
    return {
        "instruction": "Build the correctly inflected phrase from these parts, then type it into the gap.",
        "prompt": f'Parts: {" · ".join(item["parts"])}\nSentence: {item["frame"]}',
        "expected_shape": "a short phrase",
    }


def _zeitfaerbung_card(item: dict) -> dict:
    return {
        "instruction": "Fill the gap with the correct form of war, wurde, or blieb.",
        "prompt": item["frame"],
        "expected_shape": "one verb form",
    }


# --------------------------------------------------------------------------
# Grading — deterministic check first (zero LLM cost), judge only on a miss,
# exactly the same checks/judges each drill's own POST /attempts uses. Every
# function collapses that drill's richer verdict down to (correct, expected,
# note) per the normalized card contract.
# --------------------------------------------------------------------------

async def _grade_faelle(item: dict, answer: str) -> Verdict:
    if _faelle_matches(answer, item["answer"], item["frame"]):
        return True, item["answer"], None
    diag = await _judge_faelle(item, answer)
    note = diag.note
    if diag.means_instead:
        note = f"{note} ({diag.means_instead})" if note else diag.means_instead
    return diag.correct, item["answer"], note


async def _grade_satzbau(item: dict, answer: str) -> Verdict:
    order = answer.split()
    expected = " ".join(item["answer"])
    if _satzbau_matches(order, item):
        return True, expected, None
    diag = await _judge_satzbau(item, order)
    # `variant` only ever accompanies a TRUE verdict, `note` only a FALSE
    # one (see satzbau/judge.py) — never both, so this can't clobber either.
    note = diag.note if not diag.correct else diag.variant
    return diag.correct, expected, note


async def _grade_verbindungen(item: dict, answer: str) -> Verdict:
    if _verbindungen_matches(answer, item["answer"], item["frame"]):
        return True, item["answer"], None
    diag = await _judge_verbindungen(item, answer)
    return diag.correct, item["answer"], diag.note


async def _grade_bauteil(item: dict, answer: str) -> Verdict:
    if _bauteil_matches(answer, item["answer"], item["frame"]):
        return True, item["answer"], None
    diag = await _judge_bauteil(item, answer)
    return diag.correct, item["answer"], diag.note


async def _grade_zeitfaerbung(item: dict, answer: str) -> Verdict:
    # No judge LLM for this drill (see zeitfaerbung/routes.py) — the closed
    # war/wurde/blieb form set is deterministic end to end, same as the
    # drill's own /attempts. Reuses that module's own token recognizer so
    # this can never drift from what the real drill accepts.
    accepted = item["answers"]
    is_doppel = item["group"] in _ZF_DOPPEL_GROUPS

    recognized = _zf_recognized_tokens(answer)
    if len(recognized) > 1:
        remaining = list(recognized)
        for frame_form in _zf_recognized_tokens(item["frame"].replace("___", " ")):
            if frame_form in remaining:
                remaining.remove(frame_form)
        if len(remaining) == 1:
            recognized = remaining

    if len(recognized) == 0:
        return False, accepted[0], "Answer with a form of war, wurde, or blieb."
    if len(recognized) > 1:
        return False, accepted[0], "One verb form only, please."

    token = recognized[0]
    if token in accepted:
        if is_doppel:
            other = next(a for a in accepted if a != token)
            note = f"{item['readings'][token]} (the other valid form, {other}, would mean: {item['readings'][other]})"
        else:
            note = item.get("why")
        return True, token, note

    token_family = _ZF_FORM_TO_FAMILY.get(token)
    family_match = next((a for a in accepted if _ZF_FORM_TO_FAMILY.get(a) == token_family), None)
    if family_match is not None:
        return False, family_match, f"Right verb, wrong form for this subject: {family_match}."
    return False, accepted[0], item.get("why")


@dataclass(frozen=True)
class DrillAdapter:
    name: str
    load_items: Callable[[], dict[str, dict]]
    patterns: Callable[[], frozenset[str]]
    build_card: Callable[[dict], dict]
    grade: Callable[[dict, str], Awaitable[Verdict]]


_ADAPTERS: dict[str, DrillAdapter] = {
    "faelle": DrillAdapter(
        name="faelle",
        load_items=_load_faelle_items,
        patterns=lambda: frozenset(_FAELLE_PATTERNS),
        build_card=_faelle_card,
        grade=_grade_faelle,
    ),
    "satzbau": DrillAdapter(
        name="satzbau",
        load_items=_load_satzbau_items,
        patterns=lambda: frozenset(_SATZBAU_PATTERNS),
        build_card=_satzbau_card,
        grade=_grade_satzbau,
    ),
    "verbindungen": DrillAdapter(
        name="verbindungen",
        load_items=_load_verbindungen_items,
        patterns=lambda: frozenset(_VERBINDUNGEN_PATTERNS),
        build_card=_verbindungen_card,
        grade=_grade_verbindungen,
    ),
    "bauteil": DrillAdapter(
        name="bauteil",
        load_items=_load_bauteil_items,
        patterns=lambda: frozenset(_BAUTEIL_PATTERNS),
        build_card=_bauteil_card,
        grade=_grade_bauteil,
    ),
    # No TARGET_PATTERNS constant exists on zeitfaerbung/content.py (its
    # pattern_id is derived per-group, not declared as a flat tuple — see
    # that module's `_pattern_for_group`) — derive the covered set straight
    # from the validated catalog instead of reaching for that module's
    # private constants.
    "zeitfaerbung": DrillAdapter(
        name="zeitfaerbung",
        load_items=_load_zeitfaerbung_items,
        patterns=lambda: frozenset(i["pattern_id"] for i in _load_zeitfaerbung_items().values()),
        build_card=_zeitfaerbung_card,
        grade=_grade_zeitfaerbung,
    ),
}


def get_adapter(drill: str) -> DrillAdapter | None:
    return _ADAPTERS.get(drill)


def pick_random_item(pattern_id: str) -> tuple[str, dict] | None:
    """One random ``(drill_name, item)`` covering ``pattern_id``, or ``None``
    when no covered drill targets it (uncovered pattern — 404 at the route)."""
    candidates: list[tuple[str, dict]] = []
    for adapter in _ADAPTERS.values():
        if pattern_id not in adapter.patterns():
            continue
        candidates.extend(
            (adapter.name, item)
            for item in adapter.load_items().values()
            if item["pattern_id"] == pattern_id
        )
    if not candidates:
        return None
    return random.choice(candidates)


def coverage() -> dict[str, list[str]]:
    """``{drill: sorted pattern ids}`` — used by the startup/verification
    check, not by any route."""
    return {name: sorted(adapter.patterns()) for name, adapter in _ADAPTERS.items()}
