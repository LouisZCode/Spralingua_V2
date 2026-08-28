"""HTTP routes for Feste Verbindungen (GRAM-002, Exercise D: complete the
fixed verb chunk — reflexive pronoun, fixed preposition, governed case —
with reflexive and non-reflexive verbs MIXED so nothing is predictable).

Same shape as ``bauteil/routes.py``: ledger-weighted round, deterministic
check first, one diagnosis call on misses, non-fatal ledger feedback, OBS-007
tracing under the frontend-minted practice-session id.
"""

import asyncio
import random
import re
from collections import Counter

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agents.observability import propagate_trace_context, tracer
from auth.deps import get_current_user_id
from database.connection import get_db
from database.orm import UserDrillItem
from database.repository import (
    credit_pattern_success,
    load_grammar_focus,
    record_drill_attempt,
    record_grammar_error,
)
from drills import apply_level
from drills.forge import backfill_missing
from grammar import expand_contractions
from security import drill_try_admit
from verbindungen.content import TARGET_PATTERNS, load_items
from verbindungen.judge import judge_chunk
from drills.copy import JUDGE_UNAVAILABLE

from coins.gate import admit_coins_or_402
from coins.prices import SATZ_ATTEMPT

router = APIRouter(prefix="/verbindungen", tags=["verbindungen"])

ROUND_SIZE = 10
# Hot patterns lead but never fill the round — the reflexive/non-reflexive
# MIX is the drill's whole mechanism, so decoys must always be present.
MAX_HOT = 6
# CONT-002: the personal half of a 10-item round.
PERSONAL_MAX = 5

# THE INTERLEAVING RULE (GRAM-002 widen, borrowed verbatim from
# faelle/routes.py): with 3 patterns MAX_HOT alone was enough — a
# ledger-weighted round could serve at most 6 of one pattern out of 10,
# leaving 4 decoy/other-pattern slots. With 8 patterns that headroom is no
# longer a real mix: 6 hot + a handful of the SAME rest pattern can still
# legitimately dominate a round. These two knobs apply ONLY to the generic
# catalog selection below — CONT-002 personal-forged items are never capped
# or dropped, only combined in afterward (see get_round).
MAX_PER_PATTERN = 3
MIN_PATTERNS = 4


def _cap_and_diversify(chosen: list[dict], pool: list[dict]) -> list[dict]:
    """Enforce MAX_PER_PATTERN and MIN_PATTERNS on a selected round, topping
    up from ``pool`` (the full generic catalog) when the initial
    ledger-weighted selection over-indexes on too few patterns.

    Identical to ``faelle/routes.py``'s helper of the same name — see there
    for the full rationale. The cap is enforced on every single insertion
    below — including during top-up — not just on the initial selection.
    """
    chosen_ids = {i["id"] for i in chosen}
    counts: Counter = Counter()
    kept: list[dict] = []
    kept_ids: set[str] = set()
    for item in chosen:
        pid = item["pattern_id"]
        if counts[pid] < MAX_PER_PATTERN:
            kept.append(item)
            kept_ids.add(item["id"])
            counts[pid] += 1

    target = len(chosen)
    reserve = [i for i in pool if i["id"] not in chosen_ids]
    random.shuffle(reserve)

    def _fill(candidates: list[dict]) -> None:
        for c in candidates:
            if len(kept) >= target:
                return
            pid = c["pattern_id"]
            if c["id"] in kept_ids or counts[pid] >= MAX_PER_PATTERN:
                continue
            kept.append(c)
            kept_ids.add(c["id"])
            counts[pid] += 1

    # Prefer patterns not yet represented first (pushes toward
    # MIN_PATTERNS without needing the swap loop below), then anything else
    # still under cap — a smaller/uneven catalog may still land short of
    # `target`, which beats silently breaking the cap to hit the count.
    _fill([i for i in reserve if counts[i["pattern_id"]] == 0])
    _fill(reserve)

    # Still short of MIN_PATTERNS distinct ids (small/uneven catalog): swap
    # one item out of the currently-fattest pattern for an unrepresented
    # one, repeating until the floor is met or candidates run out.
    distinct = {p for p, n in counts.items() if n > 0}
    if len(distinct) < MIN_PATTERNS:
        candidates = [
            i for i in pool if i["pattern_id"] not in distinct and i["id"] not in kept_ids
        ]
        random.shuffle(candidates)
        for c in candidates:
            if len(distinct) >= MIN_PATTERNS:
                break
            fattest = counts.most_common(1)[0][0]
            for idx, i in enumerate(kept):
                if i["pattern_id"] == fattest:
                    kept[idx] = c
                    kept_ids.discard(i["id"])
                    kept_ids.add(c["id"])
                    counts[fattest] -= 1
                    counts[c["pattern_id"]] += 1
                    distinct.add(c["pattern_id"])
                    break
    return kept


def _order_no_adjacent_repeats(items: list[dict]) -> list[dict]:
    """Greedy reorder so no two consecutive items share a pattern_id,
    whenever the multiset allows it.

    Identical to ``faelle/routes.py``'s helper of the same name. Run over
    the generic-catalog items AND the CONT-002 personal items together
    (both carry ``pattern_id`` internally — see ``drills/forge.py``): the
    cap above only ever touches the generic selection, but a well-mixed
    final ORDER benefits from seeing personal items too, so a learner never
    hits e.g. three reflexive-chunk items in a row just because two of them
    happened to be personal.
    """
    buckets: dict[str, list[dict]] = {}
    for item in items:
        buckets.setdefault(item["pattern_id"], []).append(item)
    for bucket in buckets.values():
        random.shuffle(bucket)

    ordered: list[dict] = []
    last_pattern: str | None = None
    while any(buckets.values()):
        candidates = [p for p, b in buckets.items() if b and p != last_pattern]
        if not candidates:
            # Only the just-placed pattern has items left — an unavoidable
            # repeat, kept as a safety net for a small/uneven catalog.
            candidates = [p for p, b in buckets.items() if b]
        # Most-common remaining pattern first, so a big bucket never gets
        # stranded at the end where it has no choice but to repeat itself.
        candidates.sort(key=lambda p: len(buckets[p]), reverse=True)
        chosen_pattern = candidates[0]
        ordered.append(buckets[chosen_pattern].pop())
        last_pattern = chosen_pattern
    return ordered


@router.get("/round")
async def get_round(
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """One practice round; answers and chunks stay server-side (the chunk
    line would answer the item, so it only ships with the verdict).

    CONT-002: half the round (up to ``PERSONAL_MAX``) is the learner's own
    forged items; the generic catalog logic below fills whatever's left.
    """
    try:
        personal_rows = (
            await db.execute(
                select(UserDrillItem.item).where(
                    UserDrillItem.user_id == user_id,
                    UserDrillItem.exercise == "verbindungen",
                    UserDrillItem.item.is_not(None),
                )
            )
        ).scalars().all()
        # Defense in depth: the SQL filter above misses legacy rows whose
        # tombstone landed as JSON 'null' (pre-none_as_null=True); those come
        # back as Python None and must never reach the response builder.
        personal = [item for item in personal_rows if item]
        random.shuffle(personal)
        personal = personal[:PERSONAL_MAX]
    except Exception:
        # A personalisation outage must never break the round.
        logger.exception("Verbindungen personal-item read failed — serving a generic-only round")
        personal = []

    # LEVEL-001: narrow to what this learner's level warrants BEFORE the
    # ledger weighting below — weighting a pool that shouldn't be served in
    # the first place just picks the least-bad wrong item. Personal (forged)
    # items get the same ceiling: they're built from the learner's own gaps,
    # but a gap in a B1 pattern still isn't A1 practice.
    items = await apply_level(
        db, user_id=user_id, items=list(load_items().values()), drill="verbindungen"
    )
    personal = await apply_level(
        db, user_id=user_id, items=personal, drill="verbindungen/personal"
    )
    generic_size = ROUND_SIZE - len(personal)
    try:
        focus = await load_grammar_focus(db, user_id=user_id, limit=10)
        hot = {f["pattern_id"] for f in focus} & set(TARGET_PATTERNS)
    except Exception:
        logger.exception("Verbindungen focus read failed — serving an unweighted round")
        hot = set()

    hot_items = [i for i in items if i["pattern_id"] in hot]
    rest = [i for i in items if i["pattern_id"] not in hot]
    random.shuffle(hot_items)
    random.shuffle(rest)
    chosen = (hot_items[:MAX_HOT] + rest)[:generic_size]
    if len(chosen) < generic_size:
        chosen += hot_items[MAX_HOT : MAX_HOT + generic_size - len(chosen)]

    # THE INTERLEAVING RULE (see MAX_PER_PATTERN above): cap+diversify the
    # GENERIC selection only — personal items are never capped or dropped,
    # they combine in afterward exactly as many as the ledger read found
    # (up to PERSONAL_MAX). Then order the FULL round (generic + personal)
    # so the mix holds by POSITION too, not just by count — this replaces
    # the old blind `random.shuffle(result)`, which would have scrambled
    # the cap's careful composition right back into clumps.
    chosen = _cap_and_diversify(chosen, items)
    combined = chosen + personal
    ordered = _order_no_adjacent_repeats(combined)

    result = [{"id": i["id"], "frame": i["frame"], "hint": i["hint"]} for i in ordered]

    # CONT-002: top up any missing personal items in the background — never
    # blocks or fails the round it rode in on.
    try:
        asyncio.create_task(backfill_missing(user_id, "verbindungen"))
    except Exception:
        logger.exception("Verbindungen backfill scheduling failed")

    return {"items": result}


_MAX_ANSWER_CHARS = 120

# Same deterministic-match contract as bauteil/routes.py (kept local — each
# exercise module is self-contained like satz/ and bauteil/ are).
_EDGE_PUNCT = " .,!?;:…\"'"


def _normalize(s: str) -> str:
    # BUG-011: contractions expand to their two-word form, so "beim" and
    # "bei dem" compare equal. The case distinction survives (im -> in dem
    # vs. ins -> in das), so this cannot green a wrong case.
    return expand_contractions(" ".join(s.split()).strip(_EDGE_PUNCT).lower())


def _matches(typed: str, expected: str, frame: str) -> bool:
    """Exact match, or the answer embedded in the typed-out sentence — but
    ONLY frame words may surround it. Plain containment would defeat the
    decoys: "mich auf" contains the decoy answer "auf", yet the extra
    pronoun is exactly the error the mix exists to catch — it must reach
    the judge, never green deterministically."""
    t, e = _normalize(typed), _normalize(expected)
    if t == e:
        return True
    m = re.search(rf"(?<!\w){re.escape(e)}(?!\w)", t)
    if m is None:
        return False
    frame_words = set(_normalize(frame.replace("___", " ")).split())
    leftover = (t[: m.start()] + " " + t[m.end():]).split()
    return all(w in frame_words for w in leftover)


class AttemptIn(BaseModel):
    item_id: str
    answer: str
    # OBS-007 practice-sitting id — same contract as the sibling drills.
    session_id: str | None = Field(None, max_length=64)
    # FLOW-002: the deliberate "give up" escape (Flow mode only) — skips
    # validation/judging below and grades as a real, distinguishable miss.
    give_up: bool = False


@router.post("/attempts")
async def submit_attempt(
    body: AttemptIn,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Judge one typed chunk completion, feed the ledger, return the verdict
    + the canonical chunk to memorize (only now — it would answer the item)."""
    if not drill_try_admit(user_id):
        raise HTTPException(
            status_code=429,
            detail="You're going very fast — take a short break and try again in a few minutes.",
        )
    item = load_items().get(body.item_id)
    if item is None:
        row = await db.scalar(
            select(UserDrillItem).where(
                UserDrillItem.id == body.item_id,
                UserDrillItem.user_id == user_id,
            )
        )
        item = row.item if row is not None and row.item is not None else None
    if item is None:
        raise HTTPException(status_code=404, detail="Unknown item.")
    # PAY-002: AFTER the 404 (stale id must not burn coins) but BEFORE the judge/forged lookup finishes.
    try:
        await admit_coins_or_402(db, user_id=user_id, price=SATZ_ATTEMPT, kind="spend_attempt")
    except HTTPException as _e:
        if _e.status_code == 402:
            raise
        raise HTTPException(status_code=503, detail="billing temporarily unavailable")
    answer = " ".join(body.answer.split())
    # give_up skips validation entirely — there's nothing to type, the
    # learner is conceding the item.
    if not body.give_up:
        if not answer:
            raise HTTPException(status_code=422, detail="Type your answer first.")
        if len(answer) > _MAX_ANSWER_CHARS:
            raise HTTPException(
                status_code=422,
                detail="Keep it to the missing words — that looks like a paragraph.",
            )

    with propagate_trace_context(user_id=user_id, session_id=body.session_id), tracer.start_as_current_span("verbindungen-attempt") as attempt_span:
        attempt_span.set_attribute("user.id", user_id)
        attempt_span.set_attribute("item_id", item["id"])
        if body.session_id:
            attempt_span.set_attribute("langfuse.session.id", body.session_id)
        attempt_span.set_attribute("langfuse.observation.input", answer)

        if body.give_up:
            # FLOW-002 escape hatch: skip the judge entirely, grade as a
            # miss, and reveal the canonical chunk through the exact same
            # response shape a judged miss returns — no new frontend branch.
            attempt_span.set_attribute("gave_up", True)
            correct, note = False, "Gave up — here's the chunk to learn."
        elif _matches(answer, item["answer"], item["frame"]):
            correct, note = True, None
            # TASK 2: a deterministic green never calls the LLM judge — flag
            # that explicitly so Langfuse can tell "judged correct" apart
            # from "matched without a judge call" (same contract as bauteil).
            attempt_span.set_attribute("judge_skipped", True)
        else:
            try:
                diag = await judge_chunk(item, answer)
            except Exception as exc:
                attempt_span.record_exception(exc)
                logger.exception("Verbindungen judge call failed (item {})", item["id"])
                raise HTTPException(
                    status_code=502,
                    detail=JUDGE_UNAVAILABLE,
                )
            correct, note = diag.correct, diag.note

        attempt_span.set_attribute(
            "langfuse.observation.output",
            f"correct={correct}" + (f" — {note}" if note else ""),
        )
        # Structured verdict attribute so Langfuse can filter without
        # string-parsing the free-text trace.output above.
        attempt_span.set_attribute("verdict.correct", bool(correct))

        # Feed the ledger (design rule 4) — non-fatal, same self-correcting
        # contract as bauteil: a drill-retired pattern that still breaks in
        # speech gets reopened by the spoken harvesters.
        try:
            if correct:
                await credit_pattern_success(
                    db,
                    user_id=user_id,
                    pattern_id=item["pattern_id"],
                    session_id=body.session_id,
                    source="verbindungen",
                )
            else:
                await record_grammar_error(
                    db,
                    user_id=user_id,
                    pattern_id=item["pattern_id"],
                    # A give-up never typed a chunk — leave the gap unfilled
                    # rather than substitute an empty string; the literal
                    # "___" is the give-up sentinel in the ledger.
                    sentence=(
                        item["frame"]
                        if body.give_up
                        else item["frame"].replace("___", answer)
                    ),
                    corrected=item["frame"].replace("___", item["answer"]),
                    note=note,
                    source="verbindungen",
                    session_id=body.session_id,
                )
        except Exception:
            logger.exception(
                "Verbindungen ledger write failed (pattern {})", item["pattern_id"]
            )

        # Append to the cross-drill attempt log (DATA-004) — its own commit,
        # non-fatal like the ledger write above, same shape as bauteil.
        try:
            await record_drill_attempt(
                db,
                user_id=user_id,
                exercise="verbindungen",
                # ":giveup" suffix marks a concession in DATA-004 without a
                # new column — same convention genus uses for its beats.
                item_ref=item["id"] + (":giveup" if body.give_up else ""),
                pattern_id=item["pattern_id"],
                correct=correct,
                modality="written",
                session_id=body.session_id,
            )
        except Exception:
            logger.exception("Drill-attempt log write failed (item {})", item["id"])

        return {
            "correct": correct,
            "expected": item["answer"],
            "chunk": item["chunk"],
            "note": note,
        }
