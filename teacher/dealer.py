"""The exercise dealer for Clara's interactive-exercise loop (CLARA-17,
"the exercise factory, round 1").

Before this round, ``[[ÜBUNG: <id>]]`` always meant one thing: a random
catalog item for that pattern (``teacher/registry.py::pick_random_item``),
served as-is. That is now exactly ONE of three formats this module can deal:

1. **pool** — the pre-CLARA-17 behavior: a catalog item from one of the six
   drill adapters (``teacher/registry.py``), now LEVEL-FILTERED via
   ``drills/leveling.py::apply_level`` before the random pick.
2. **produce** — a fresh, live-generated production task SEEDED BY THE
   TAXONOMY ENTRY for the pattern (not free text — that's the dev-only
   ``ÜBUNG-NEU`` forge, untouched by this round), pitched at the learner's
   level. Costs two LLM calls (draft + verify), so it's gated behind the
   shared drill throttle (``security.drill_try_admit``).
3. **redo** — built DETERMINISTICALLY, no LLM at all, from the learner's own
   ``user_errors`` ledger: their own past wrong sentence, shown back to them
   to correct.

Owner's design decisions for round 1 (binding, not this module's call to
revisit): Clara's ``[[ÜBUNG: <id>]]`` marker sends only the pattern — the
SERVER owns the format roll, not the agent; the roll is uniform among
whichever formats are actually servable this deal; the same format never
deals twice in a row for one user (when avoidable); and a deal must NEVER
404 or otherwise fail just because level-narrowing or the ledger happened to
come up empty for one format — it drops that format from the roll instead.

============================================================================
CLARA'S ROOM STAYS ABSOLUTELY WRITE-FREE. This module reads three things —
the taxonomy (in-process cache), the learner's level + ledger row (plain
SELECTs via the ``db`` session the route now opens with ``Depends(get_db)``),
and the shared in-process drill throttle — and writes NOTHING: no
``db.commit()`` anywhere below, no ``record_grammar_error``, no
``credit_pattern_success``, no coins. The two module-level dicts below
(``_LAST_FORMAT`` and ``teacher/forge.py``'s own item store) are process-
local caches, not persistence — see the exception note on ``_LAST_FORMAT``.
Adding the ``db`` session to ``GET /teacher/exercise`` for this round changes
nothing about that invariant: it is read-only by construction, the same way
``GET /teacher/starters`` and ``GET /teacher/balance`` already open a session
to read without ever writing through it.
============================================================================
"""

import random

from fastapi import HTTPException
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from database.orm import UserError
from database.repository import load_user_level
from drills.leveling import apply_level
from grammar.loader import load_taxonomy
from security import drill_try_admit
from teacher.forge import build_redo_item, forge_item_for_pattern, store_item
from teacher.registry import get_adapter, pool_candidates

__all__ = ["deal"]

# Anti-repeat memory: user_id -> the last format actually SERVED to them
# ("pool" | "produce" | "redo" — the abstract format, not a pool item's
# concrete drill name). Process-local, DELIBERATE, DOCUMENTED exception to
# the "no module-level singletons" rule in CLAUDE.md — same rationale as
# ``teacher/forge.py``'s in-memory item store: losing this on a restart only
# risks one repeated format for one learner, never a learner-facing loss,
# and there is no multi-worker deployment for this backend today.
_LAST_FORMAT: dict[str, str] = {}


def _roll_format(valid_formats: set[str], last_format: str | None) -> str:
    """Pure, DB-free anti-repeat roll — split out of :func:`deal` so it is
    unit-testable on its own (see the CLARA-17 verification steps).

    Uniform among ``valid_formats``, excluding ``last_format`` whenever
    doing so still leaves at least one candidate. If ``valid_formats`` is
    exactly ``{last_format}`` — the only servable format this deal is also
    the one just served — excluding it would leave nothing to roll, so the
    repeat is allowed instead.
    """
    candidates = valid_formats - {last_format} if last_format is not None else set(valid_formats)
    if not candidates:
        candidates = valid_formats
    return random.choice(sorted(candidates))


def _qualifying_examples(row: UserError | None) -> list[dict]:
    """The subset of ``row.examples`` a redo item can be built from: both
    ``sentence`` and ``corrected`` present and non-empty, and NOT the
    give-up sentinel (``sentence == "(gave up)"``, always paired with
    ``corrected: None`` — see ``sprechen/routes.py``'s give-up path). A
    give-up has no wrong sentence to show back, only the fact that the
    learner skipped it, which redo has no format for.
    """
    if row is None or not row.examples:
        return []
    return [
        e
        for e in row.examples
        if e.get("sentence") and e.get("corrected") and e["sentence"] != "(gave up)"
    ]


def _build_pool_payload(candidates: list[tuple[str, dict]]) -> dict:
    """Exactly today's pool shape — byte-identical to pre-CLARA-17
    ``GET /teacher/exercise`` plus the CLARA-17 ``format`` key (an extra
    top-level field the frontend's typed read simply ignores) — via the
    winning adapter's own ``serve``."""
    adapter_name, item = random.choice(candidates)
    adapter = get_adapter(adapter_name)
    native_item = adapter.serve(item)
    return {
        "drill": adapter_name,
        "format": "pool",
        "itemId": item["id"],
        "patternId": item["pattern_id"],
        "item": native_item,
    }


def _forged_payload(format_: str, entry: dict, pattern: str, item: dict) -> dict:
    """Shared served shape for produce and redo — both are items that went
    through ``teacher/forge.py::store_item`` and get the SAME pre-attempt
    projection ``POST /teacher/exercise/forge`` already uses: the answer
    (``item["example"]``) withheld until after the attempt.

    ``drill`` is ALWAYS the literal ``"produce"`` for both formats — that is
    the frontend contract: TeacherChat's ``Exercise`` union has exactly one
    generated-item member (``drill: "produce"`` -> ProduceCard), and the
    attempts routes grade exactly one generated drill value. A redo differs
    from a produce only in how the ITEM was built (ledger vs LLM), which the
    task text itself carries; the abstract format rides separately in
    ``format`` (trace attribute + an ignored extra field client-side).
    """
    return {
        "drill": "produce",
        "format": format_,
        "itemId": item["id"],
        "patternId": pattern,
        "topic": entry["label"],
        "item": {
            "id": item["id"],
            "task": item["task"],
            "target": item["target"],
            "hint": item["rule_note"],
        },
    }


def _build_redo_payload(entry: dict, examples: list[dict], level: str | None, pattern: str) -> dict:
    # Picked at random among the qualifying examples so repeated redo deals
    # for the same pattern don't always surface the same old slip.
    example = random.choice(examples)
    item = build_redo_item(entry, example, level)
    store_item(item)
    return _forged_payload("redo", entry, pattern, item)


async def deal(db: AsyncSession, *, user_id: str, pattern: str) -> dict | None:
    """Deal one exercise for ``pattern`` to ``user_id`` — the full
    ``GET /teacher/exercise`` response payload, or ``None`` when ``pattern``
    isn't a real taxonomy id (the caller 404s — a hallucinated/stale id from
    Clara keeps today's fail-closed contract; this is the ONLY ``None``
    case, never a throttle or a generation failure).

    Mechanics: determine which of {pool, produce, redo} are actually
    servable this deal, roll uniformly among them (excluding the user's
    last-served format when possible), then build that format's payload —
    falling back to another still-valid format if a live produce generation
    fails, and raising an HTTPException only when NOTHING is left to serve
    (429 for an exhausted throttle with no pool/redo fallback, 502 for a
    produce failure with no pool/redo fallback).
    """
    taxonomy = load_taxonomy()
    entry = taxonomy.get(pattern)
    if entry is None:
        return None

    # Fetched once and handed to both generators below — apply_level (pool
    # narrowing) reads its own copy internally, which is a second DB hit for
    # the same value; the spec calls that fine rather than threading `level`
    # through apply_level's signature, so it's left as-is.
    level = await load_user_level(db, user_id=user_id)

    # ---- pool: catalog candidates across all six adapters, level-narrowed
    pool_items = pool_candidates(pattern)
    pool_valid = bool(pool_items)
    pool_serve_from = pool_items
    if pool_valid:
        narrowed = await apply_level(
            db,
            user_id=user_id,
            items=pool_items,
            drill="teacher",
            pattern_of=lambda t: t[1]["pattern_id"],
        )
        # A deal must never 404 (or silently narrow to nothing) because of
        # leveling — if narrowing empties the per-pattern set, fall back to
        # the unfiltered candidates rather than dropping pool entirely.
        pool_serve_from = narrowed if narrowed else pool_items

    # ---- redo: the learner's own qualifying ledger examples, read-only
    error_row = await db.get(UserError, (user_id, pattern))
    qualifying_examples = _qualifying_examples(error_row)
    redo_valid = bool(qualifying_examples)

    # ---- produce: always taxonomy-valid, gated behind the shared drill
    # throttle (two LLM calls to generate). Called once per deal regardless
    # of which format ends up rolled — same "gate the whole request once"
    # convention every other drill-throttled route in this repo already
    # follows (see teacher/routes.py's own drill_try_admit call sites).
    produce_admitted = drill_try_admit(user_id)

    valid_formats: set[str] = set()
    if pool_valid:
        valid_formats.add("pool")
    if redo_valid:
        valid_formats.add("redo")
    if produce_admitted:
        valid_formats.add("produce")

    if not valid_formats:
        # Only reachable when pool AND redo are both unservable (no catalog
        # coverage, no qualifying ledger example) and produce was the only
        # option — and the throttle took it. Never 429 a deal a pool card or
        # redo could still serve.
        logger.warning(f"[DEAL] pattern='{pattern}' throttled with no fallback format")
        raise HTTPException(
            status_code=429,
            detail="You're going very fast — take a short break and try again in a few minutes.",
        )

    rolled = _roll_format(valid_formats, _LAST_FORMAT.get(user_id))

    served_format = rolled
    if rolled == "pool":
        payload = _build_pool_payload(pool_serve_from)
    elif rolled == "redo":
        payload = _build_redo_payload(entry, qualifying_examples, level, pattern)
    else:  # "produce"
        try:
            item = await forge_item_for_pattern(entry, level)
        except Exception:
            logger.exception(f"[DEAL] produce forge failed for pattern='{pattern}' — falling back")
            if pool_valid:
                served_format = "pool"
                payload = _build_pool_payload(pool_serve_from)
            elif redo_valid:
                served_format = "redo"
                payload = _build_redo_payload(entry, qualifying_examples, level, pattern)
            else:
                raise HTTPException(
                    status_code=502,
                    detail="Couldn't build that exercise — try again in a moment.",
                )
        else:
            store_item(item)
            payload = _forged_payload("produce", entry, pattern, item)

    _LAST_FORMAT[user_id] = served_format
    logger.info(
        "[DEAL] pattern='{}' format='{}' drill='{}' item='{}'",
        pattern, payload["format"], payload["drill"], payload["itemId"],
    )
    return payload
