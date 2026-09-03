"""HTTP routes for Bauteil-Sätze (GRAM-002, Exercise A: build the inflected
phrase from raw parts — a WRITTEN drill, no audio, no SRS).

Two endpoints behind the same session-JWT + per-request AsyncSession
dependencies as ``satz/routes.py``:

- ``GET /bauteil/round`` — ten items, weighted toward the learner's hot open
  ledger patterns (``load_grammar_focus``) but never monopolized by them:
  deciding *which* structure applies is part of the drill (design rule 1).
- ``POST /bauteil/attempts`` — judge one typed phrase. A deterministic string
  check green-lights exact (or sentence-embedded) matches at zero LLM cost;
  only misses pay for the diagnosis call. Every attempt then feeds the
  grammar-error ledger (design rule 4): a miss is a slip, a green credits the
  retire streak — both tagged ``source="bauteil"``, both non-fatal.
"""

import asyncio
import random

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agents.observability import propagate_trace_context, tracer
from auth.deps import get_current_user_id
from bauteil import grading
from bauteil.content import TARGET_PATTERNS, load_items
from drills.copy import JUDGE_UNAVAILABLE
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
from security import drill_try_admit

from coins.gate import admit_coins_or_402
from coins.prices import SATZ_ATTEMPT

router = APIRouter(prefix="/bauteil", tags=["bauteil"])

ROUND_SIZE = 10
# Hot ledger patterns lead the round but never fill it: at least 4 of 10 items
# come from outside the learner's weak spots so the mix keeps "which rule even
# applies here?" a live question instead of telegraphing the answer.
MAX_HOT = 6
# CONT-002: the personal half of a 10-item round.
PERSONAL_MAX = 5


@router.get("/round")
async def get_round(
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """One practice round. The answer never leaves the server — checking is
    the POST's job, so devtools can't leak the solution mid-drill.

    CONT-002: half the round (up to ``PERSONAL_MAX``) is the learner's own
    forged items; the generic catalog logic below fills whatever's left.
    """
    try:
        personal_rows = (
            await db.execute(
                select(UserDrillItem.item).where(
                    UserDrillItem.user_id == user_id,
                    UserDrillItem.exercise == "bauteil",
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
        logger.exception("Bauteil personal-item read failed — serving a generic-only round")
        personal = []

    # LEVEL-001: narrow before the ledger weighting below; the personal
    # (forged) items get the same ceiling. LEVEL-002: Bauteil only has
    # three patterns total (one A1, two B1) — letting the ceiling apply to
    # them collapses an A1/A2 round to a single pattern, so the drill's own
    # target set is exempted from its own ceiling (`always_allow`); other
    # drills' patterns in the ledger floor still obey it normally. Note the
    # practical effect: every catalog item's pattern is in TARGET_PATTERNS,
    # so `apply_level` no longer filters Bauteil's own pool at all (ceiling
    # AND floor) — accepted until A1/A2 content exists (LEVEL-002 Proposal-2).
    items = await apply_level(
        db,
        user_id=user_id,
        items=list(load_items().values()),
        drill="bauteil",
        always_allow=set(TARGET_PATTERNS),
    )
    personal = await apply_level(
        db,
        user_id=user_id,
        items=personal,
        drill="bauteil/personal",
        always_allow=set(TARGET_PATTERNS),
    )
    generic_size = ROUND_SIZE - len(personal)
    try:
        focus = await load_grammar_focus(db, user_id=user_id, limit=10)
        hot = {f["pattern_id"] for f in focus} & set(TARGET_PATTERNS)
    except Exception:
        # Ledger outage = a less personalised round, never a failed one.
        logger.exception("Bauteil focus read failed — serving an unweighted round")
        hot = set()

    hot_items = [i for i in items if i["pattern_id"] in hot]
    rest = [i for i in items if i["pattern_id"] not in hot]
    random.shuffle(hot_items)
    random.shuffle(rest)
    chosen = (hot_items[:MAX_HOT] + rest)[:generic_size]
    if len(chosen) < generic_size:
        # Every pattern is hot → `rest` ran short; pad back from the hot pool.
        chosen += hot_items[MAX_HOT : MAX_HOT + generic_size - len(chosen)]

    result = [
        {"id": i["id"], "parts": i["parts"], "frame": i["frame"], "hint": i["hint"]}
        for i in chosen
    ] + [
        {"id": p["id"], "parts": p["parts"], "frame": p["frame"], "hint": p["hint"]}
        for p in personal
    ]
    random.shuffle(result)

    # CONT-002: top up any missing personal items in the background — never
    # blocks or fails the round it rode in on.
    try:
        asyncio.create_task(backfill_missing(user_id, "bauteil"))
    except Exception:
        logger.exception("Bauteil backfill scheduling failed")

    return {"items": result}


class AttemptIn(BaseModel):
    item_id: str
    answer: str
    # OBS-007: frontend-minted practice-sitting id — one Langfuse Session per
    # trainer visit, same contract as /satz/attempts. Optional so curl works.
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
    """Judge one typed phrase, feed the ledger, return the two-axis verdict."""
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
    # PAY-002: AFTER the 404 (stale/forged id must not burn coins) but BEFORE the judge.
    try:
        await admit_coins_or_402(db, user_id=user_id, price=SATZ_ATTEMPT, kind="spend_attempt")
    except HTTPException as _e:
        if _e.status_code == 402:
            raise
        raise HTTPException(status_code=503, detail="billing temporarily unavailable")
    answer = grading.normalize_answer(body.answer)
    # give_up skips validation entirely — there's nothing to type, the
    # learner is conceding the item.
    if not body.give_up:
        reason = grading.validate(item, answer)
        if reason:
            raise HTTPException(status_code=422, detail=reason)

    # OBS-007: one Langfuse trace per judged attempt, grouped into the
    # practice sitting. Deterministic greens trace too (no `llm` child) —
    # a session should show every attempt, not just the ones that cost tokens.
    with propagate_trace_context(user_id=user_id, session_id=body.session_id), tracer.start_as_current_span("bauteil-attempt") as attempt_span:
        attempt_span.set_attribute("user.id", user_id)
        attempt_span.set_attribute("item_id", item["id"])
        if body.session_id:
            attempt_span.set_attribute("langfuse.session.id", body.session_id)
        attempt_span.set_attribute("langfuse.observation.input", answer)

        if body.give_up:
            # FLOW-002 escape hatch: skip the judge entirely, grade as a
            # miss, and reveal the gold phrase through the exact same
            # response shape a judged miss returns — the frontend needs no
            # new branch to show it.
            attempt_span.set_attribute("gave_up", True)
        try:
            verdict, judge_skipped = await grading.grade(item, answer, give_up=body.give_up)
        except Exception as exc:
            attempt_span.record_exception(exc)
            logger.exception("Bauteil judge call failed (item {})", item["id"])
            raise HTTPException(
                status_code=502,
                detail=JUDGE_UNAVAILABLE,
            )
        if judge_skipped:
            # TASK 2: a deterministic green never calls the LLM judge — flag
            # that explicitly so Langfuse can tell "judged correct" apart
            # from "matched without a judge call".
            attempt_span.set_attribute("judge_skipped", True)
        correct, case_ok, carrier_ok, note = (
            verdict["correct"],
            verdict["caseOk"],
            verdict["carrierOk"],
            verdict["note"],
        )

        attempt_span.set_attribute(
            "langfuse.observation.output",
            f"correct={correct} caseOk={case_ok} carrierOk={carrier_ok}"
            + (f" — {note}" if note else ""),
        )
        # Structured verdict attributes so Langfuse can filter without
        # string-parsing the free-text trace.output above.
        attempt_span.set_attribute("verdict.correct", bool(correct))
        attempt_span.set_attribute("verdict.case_ok", bool(case_ok))
        attempt_span.set_attribute("verdict.carrier_ok", bool(carrier_ok))

        # Feed the grammar-error ledger (design rule 4) — non-fatal, so a DB
        # outage can never break the attempt it rides on. A drilled green
        # credits the retire streak; if the pattern still breaks in speech,
        # the spoken harvesters reopen it — the ledger self-corrects.
        try:
            if correct:
                await credit_pattern_success(
                    db,
                    user_id=user_id,
                    pattern_id=item["pattern_id"],
                    session_id=body.session_id,
                    source="bauteil",
                )
            else:
                await record_grammar_error(
                    db,
                    user_id=user_id,
                    pattern_id=item["pattern_id"],
                    # A give-up never typed a phrase — leave the gap
                    # unfilled rather than substitute an empty string; the
                    # literal "___" is the give-up sentinel in the ledger.
                    sentence=(
                        item["frame"]
                        if body.give_up
                        else item["frame"].replace("___", answer)
                    ),
                    corrected=item["frame"].replace("___", item["answer"]),
                    note=note,
                    source="bauteil",
                    session_id=body.session_id,
                )
        except Exception:
            logger.exception(
                "Bauteil ledger write failed (pattern {})", item["pattern_id"]
            )

        # Append to the cross-drill attempt log (DATA-004) — its own commit,
        # non-fatal like the ledger write above. Deterministic greens count
        # as `correct=True` here too, same as the ledger credit above.
        try:
            await record_drill_attempt(
                db,
                user_id=user_id,
                exercise="bauteil",
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

        # camelCase like every other practice payload.
        return verdict
