"""Pattern -> exercise registry for Clara's interactive-exercise loop
(AGENT-00X, "the teacher can hand out a real item"; rebuilt for CLARA-13,
"Clara mounts the real drill trainers").

Clara's room has never had a written drill; this is the bridge. It maps
``grammar/taxonomy.yaml`` pattern ids onto items drawn from FIVE existing
typed-answer drill catalogs — faelle, satzbau, zeitfaerbung, verbindungen,
bauteil — and now serves each drill's item and grades each attempt in that
drill's own NATIVE shape, so the frontend mounts the exact same trainer
component Flow does, dealt Flow-style with a round of one. Nothing here
flattens a card or collapses a verdict anymore (that generic-card design was
CLARA-13's predecessor, AGENT-00X) — this module is pure plumbing: item
lookup/pattern-matching (``pick_random_item``) plus two per-drill functions
per adapter, ``serve`` and ``grade``, both IMPORTED from that drill's own
``<drill>/grading.py`` (grading) and ``<drill>/routes.py`` (serve-time
transforms, e.g. satzbau's chip shuffle) — nothing here reimplements a check,
a serve-time transform, or duplicates an ``items.yaml``. No drill module is
edited to make this work.

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
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from bauteil import grading as _bauteil_grading
from bauteil.content import TARGET_PATTERNS as _BAUTEIL_PATTERNS, load_items as _load_bauteil_items

from faelle import grading as _faelle_grading
from faelle.content import TARGET_PATTERNS as _FAELLE_PATTERNS, load_items as _load_faelle_items

from satzbau import grading as _satzbau_grading
from satzbau.content import TARGET_PATTERNS as _SATZBAU_PATTERNS, load_items as _load_satzbau_items
from satzbau.routes import _shuffle_chips as _satzbau_shuffle_chips

from verbindungen import grading as _verbindungen_grading
from verbindungen.content import (
    TARGET_PATTERNS as _VERBINDUNGEN_PATTERNS,
    load_items as _load_verbindungen_items,
)

from zeitfaerbung import grading as _zeitfaerbung_grading
from zeitfaerbung.content import (
    DOPPELDEUTIG_GROUPS as _ZF_DOPPEL_GROUPS,
    load_items as _load_zeitfaerbung_items,
)


# --------------------------------------------------------------------------
# Serve — "what does that drill's own GET /round emit for one item?", byte-
# for-byte, so a Clara-dealt item is indistinguishable from a Flow-dealt one.
# Mirrors the list-comprehension entry each drill's own round route builds —
# the file:line noted per function is what this must never drift from.
# --------------------------------------------------------------------------

def _serve_faelle(item: dict) -> dict:
    # Mirrors faelle/routes.py:196 (GET /round's per-item entry).
    return {"id": item["id"], "frame": item["frame"], "hint": item["hint"]}


def _serve_satzbau(item: dict) -> dict:
    # Mirrors satzbau/routes.py:214-223 (GET /round's per-item entry) —
    # chips are re-shuffled per serve via the drill's OWN _shuffle_chips,
    # imported above rather than reimplemented, so a re-roll here always
    # matches what /satzbau/round would have shuffled.
    return {
        "id": item["id"],
        "given": item["given"],
        "task": item["task"],
        "chips": _satzbau_shuffle_chips(item["chips"], item["answer"]),
        "hint": item["hint"],
    }


def _serve_verbindungen(item: dict) -> dict:
    # Mirrors verbindungen/routes.py:235 (GET /round's per-item entry).
    return {"id": item["id"], "frame": item["frame"], "hint": item["hint"]}


def _serve_bauteil(item: dict) -> dict:
    # Mirrors bauteil/routes.py:117-123 (GET /round's per-item entry).
    return {"id": item["id"], "parts": item["parts"], "frame": item["frame"], "hint": item["hint"]}


def _serve_zeitfaerbung(item: dict) -> dict:
    # Mirrors zeitfaerbung/routes.py:95-100 (GET /round's per-item entry) —
    # ``hint`` is omitted ENTIRELY for doppeldeutig items (an English hint
    # would force one reading and spoil the ambiguity that's the point of
    # that group). Reuses the drill's own DOPPELDEUTIG_GROUPS constant
    # (imported above) — never redefined here.
    entry = {"id": item["id"], "frame": item["frame"]}
    if item["group"] not in _ZF_DOPPEL_GROUPS:
        entry["hint"] = item["hint"]
    return entry


@dataclass(frozen=True)
class DrillAdapter:
    name: str
    load_items: Callable[[], dict[str, dict]]
    patterns: Callable[[], frozenset[str]]
    serve: Callable[[dict], dict]
    # validate(item, answer_or_order) -> str | None — the 422 reason, or None.
    validate: Callable[[dict, Any], "str | None"]
    # grade(item, answer_or_order, *, give_up=False) -> (verdict: dict, extra)
    # — ``verdict`` is that drill's NATIVE verdict shape; ``extra`` is a
    # per-drill implementation detail (judge_skipped for the four judge-
    # backed drills, the ledger sentinel for zeitfaerbung) callers of THIS
    # module never need to interpret, only optionally discard.
    grade: Callable[..., Awaitable[tuple[dict, Any]]]
    # True for satzbau only: its attempt payload is `order: list[str]`, not
    # `answer: str` — teacher/routes.py uses this to pick which AttemptIn
    # field to hand to validate()/grade().
    uses_order: bool = field(default=False)


_ADAPTERS: dict[str, DrillAdapter] = {
    "faelle": DrillAdapter(
        name="faelle",
        load_items=_load_faelle_items,
        patterns=lambda: frozenset(_FAELLE_PATTERNS),
        serve=_serve_faelle,
        validate=_faelle_grading.validate,
        grade=_faelle_grading.grade,
    ),
    "satzbau": DrillAdapter(
        name="satzbau",
        load_items=_load_satzbau_items,
        patterns=lambda: frozenset(_SATZBAU_PATTERNS),
        serve=_serve_satzbau,
        validate=_satzbau_grading.validate,
        grade=_satzbau_grading.grade,
        uses_order=True,
    ),
    "verbindungen": DrillAdapter(
        name="verbindungen",
        load_items=_load_verbindungen_items,
        patterns=lambda: frozenset(_VERBINDUNGEN_PATTERNS),
        serve=_serve_verbindungen,
        validate=_verbindungen_grading.validate,
        grade=_verbindungen_grading.grade,
    ),
    "bauteil": DrillAdapter(
        name="bauteil",
        load_items=_load_bauteil_items,
        patterns=lambda: frozenset(_BAUTEIL_PATTERNS),
        serve=_serve_bauteil,
        validate=_bauteil_grading.validate,
        grade=_bauteil_grading.grade,
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
        serve=_serve_zeitfaerbung,
        validate=_zeitfaerbung_grading.validate,
        grade=_zeitfaerbung_grading.grade,
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
