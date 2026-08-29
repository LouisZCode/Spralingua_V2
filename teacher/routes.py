"""HTTP routes for Clara's interactive-exercise loop (AGENT-00X backend
half, rebuilt for CLARA-13 — "Clara mounts the real drill trainers"), plus
the cold-start topic-screen endpoint:

- ``GET /teacher/starters`` — three curated starter topics for a learner
  whose ``user_errors`` ledger is still empty (see ``teacher/starters.py``).
- ``GET /teacher/exercise?pattern=<taxonomy id>`` — one random item for that
  pattern, served in that drill's NATIVE round-item shape (exactly what
  ``GET /<drill>/round`` would emit for it) so the frontend mounts the same
  trainer component Flow does, dealt Flow-style with a round of one.
- ``POST /teacher/exercise/attempts`` — grade one typed/typed-order answer
  using that drill's own deterministic check + judge, returning that drill's
  NATIVE verdict shape (see teacher/registry.py).

All three sit behind the same session-JWT dependency every drill router
uses; the POST also shares the drills' per-user rate limiter (the judge calls
it may fire are exactly as expensive as a normal drill attempt).
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from pydantic import BaseModel, Field

from agents.observability import (
    get_trace_session,
    mark_span_error,
    propagate_trace_context,
    tracer,
)
from auth.deps import get_current_user_id
from security import drill_try_admit
from teacher.registry import get_adapter, pick_random_item
from teacher.starters import starters_for_level
from drills.copy import JUDGE_UNAVAILABLE

router = APIRouter(prefix="/teacher", tags=["teacher"])


@router.get("/starters")
async def teacher_starters(user_id: str = Depends(get_current_user_id)):
    """Cold-start starter topics for the topic screen (AGENT-00X follow-up).

    Read-only: no writes, no coin/gate interaction — same three ids the
    factory falls back to when a learner's ``user_errors`` ledger is empty,
    so what's offered here always matches what Clara can actually deal."""
    from database.connection import get_sessionmaker
    from database.repository import load_user_level

    async with get_sessionmaker()() as db:
        level = await load_user_level(db, user_id=user_id)
    return {"starters": starters_for_level(level), "seeded": True}


@router.get("/balance")
async def teacher_balance(user_id: str = Depends(get_current_user_id)):
    """Daily Clara talk allowance for the picker header.

    free 0/day, basic 1/day, premium 3/day, developer ∞. Window is the coin
    day (05:00 local via coins/engine). Counts activity_session rows with
    lesson_id='teacher' in [day_start, nextResetAt)."""
    from datetime import timedelta as _td
    from sqlalchemy import func as _func, select as _select

    from coins.engine import next_reset_at as _nra
    from database.connection import get_sessionmaker
    from database.orm import ActivitySession as _AS, User as _User

    async with get_sessionmaker()() as db:
        user = await db.scalar(_select(_User).where(_User.id == user_id))
        if user is None:
            return {"tier": "free", "limit": 0, "used": 0, "remaining": 0, "nextResetAt": _nra(None).isoformat()}
        if (user.role or "") == "developer":
            nxt = _nra(user.timezone)
            return {"tier": user.tier or "free", "limit": 99, "used": 0, "remaining": 99, "nextResetAt": nxt.isoformat(), "bypass": True}
        tier = user.tier or "free"
        limit = {"free": 0, "basic": 1, "premium": 3}.get(tier, 0)
        nxt = _nra(user.timezone)
        day_start = nxt - _td(days=1)
        ds_naive = day_start.replace(tzinfo=None)
        nr_naive = nxt.replace(tzinfo=None)
        cnt = await db.scalar(
            _select(_func.count()).select_from(_AS).where(
                _AS.user_id == user_id,
                _AS.lesson_id == "teacher",
                _AS.started_at >= ds_naive,
                _AS.started_at < nr_naive,
            )
        )
        used = int(cnt or 0)
        return {"tier": tier, "limit": limit, "used": used, "remaining": max(0, limit - used), "nextResetAt": nxt.isoformat()}

@router.get("/exercise")
async def get_exercise(
    pattern: str,
    user_id: str = Depends(get_current_user_id),
):
    """One random item for ``pattern``, served in that drill's NATIVE
    round-item shape (CLARA-13) — the frontend mounts the same trainer
    component Flow does, so the item must be indistinguishable from a
    Flow-dealt one. 404 when no covered drill targets this pattern —
    Clara's own prompt is told to only ever name an id from her rendered
    focus list, but a stale or hallucinated id must still fail closed here,
    not 500."""
    # Joins Clara's live conversation Session in Langfuse (server-side lookup;
    # the frontend carries no session id). One trace per dealt exercise =
    # Clara's deal rate is countable per session/user. A 404 is stamped as an
    # ERROR trace deliberately: it means Clara named a pattern her exercise
    # catalog doesn't cover — an agent-quality signal, not client noise.
    session_id = get_trace_session(user_id)
    with tracer.start_as_current_span("teacher-exercise-deal") as span:
        span.set_attribute("user.id", user_id)
        if session_id:
            span.set_attribute("langfuse.session.id", session_id)
        span.set_attribute("langfuse.observation.input", pattern)
        span.set_attribute("langfuse.observation.metadata.pattern", pattern)
        picked = pick_random_item(pattern)
        if picked is None:
            mark_span_error(span, "no exercise for this pattern (stale/hallucinated id?)")
            raise HTTPException(status_code=404, detail="no exercise for this pattern")
        drill_name, item = picked
        adapter = get_adapter(drill_name)
        native_item = adapter.serve(item)
        span.set_attribute("drill", drill_name)
        span.set_attribute("item_id", item["id"])
        span.set_attribute("langfuse.observation.output", f"{drill_name}:{item['id']}")
        return {
            "drill": drill_name,
            "itemId": item["id"],
            "patternId": item["pattern_id"],
            "item": native_item,
        }


class AttemptIn(BaseModel):
    drill: str
    itemId: str
    give_up: bool = False
    answer: str | None = Field(default=None, max_length=2000)  # text drills
    order: list[str] | None = None  # satzbau


@router.post("/exercise/attempts")
async def submit_exercise_attempt(
    body: AttemptIn,
    user_id: str = Depends(get_current_user_id),
):
    """Grade one attempt with that drill's own deterministic check + judge,
    returning that drill's NATIVE verdict shape (CLARA-13) — byte-compatible
    with what ``POST /<drill>/attempts`` returns for the same item+answer.

    ============================================================================
    ABSOLUTE INVARIANT — THIS ROUTE WRITES NOTHING, EVER.
    No `record_grammar_error`, no `credit_pattern_success`, no
    `record_drill_attempt`, no daily-mode credit, no DB session at all. This is
    the approved design decision for v1 ("stay silent") — Clara's room is
    deliberately exempt from every evaluator (see ARCHITECTURE.md's teacher
    branch), and a practice item handed out INSIDE that room must not become a
    side-channel into the same learning-state tables the exemption exists to
    keep her out of. If a future version wants these writes, that's a new,
    deliberate decision — not a bug fix to this comment.
    ============================================================================
    """
    if not drill_try_admit(user_id):
        raise HTTPException(
            status_code=429,
            detail="You're going very fast — take a short break and try again in a few minutes.",
        )
    adapter = get_adapter(body.drill)
    if adapter is None:
        raise HTTPException(status_code=404, detail="Unknown item.")
    item = adapter.load_items().get(body.itemId)
    if item is None:
        raise HTTPException(status_code=404, detail="Unknown item.")

    # satzbau grades an `order: list[str]`; the other four grade a typed
    # `answer: str` — the request carries whichever field applies (D3),
    # validated 422 when it's missing and this isn't a give-up.
    if adapter.uses_order:
        if not body.give_up and body.order is None:
            raise HTTPException(status_code=422, detail="Place the chips first.")
        payload: Any = body.order if body.order is not None else []
    else:
        if not body.give_up and body.answer is None:
            raise HTTPException(status_code=422, detail="Type your answer first.")
        payload = " ".join((body.answer or "").split())

    if not body.give_up:
        reason = adapter.validate(item, payload)
        if reason:
            raise HTTPException(status_code=422, detail=reason)

    # Same tracing shape as every drill's /attempts: a root span carrying the
    # answer as root-observation input and the verdict as output, wrapped in
    # propagate_trace_context so the judge generation the grade may fire
    # nests under it WITH user/session (it was an orphan before this span
    # existed). `langfuse.session.id` joins Clara's live conversation Session
    # server-side. Tracing only — the WRITES-NOTHING invariant above is
    # untouched; nothing here goes near the ledger.
    session_id = get_trace_session(user_id)
    with propagate_trace_context(user_id=user_id, session_id=session_id), \
            tracer.start_as_current_span("teacher-exercise-attempt") as span:
        span.set_attribute("user.id", user_id)
        if session_id:
            span.set_attribute("langfuse.session.id", session_id)
        span.set_attribute("drill", body.drill)
        span.set_attribute("item_id", item["id"])
        span.set_attribute(
            "langfuse.observation.input",
            " ".join(payload) if adapter.uses_order else payload,
        )
        # Filterable metadata (langfuse.observation.metadata.*) — the fields
        # OBS-009-style evaluation will want to slice by.
        span.set_attribute("langfuse.observation.metadata.pattern", item["pattern_id"])
        if body.give_up:
            span.set_attribute("gave_up", True)
        try:
            verdict, _extra = await adapter.grade(item, payload, give_up=body.give_up)
        except Exception:
            logger.exception(
                "Teacher exercise judge call failed (drill {} item {})", body.drill, item["id"]
            )
            mark_span_error(span, "judge unavailable")
            raise HTTPException(
                status_code=502,
                detail=JUDGE_UNAVAILABLE,
            )
        correct = verdict["correct"]
        note = verdict.get("note")
        span.set_attribute("langfuse.observation.metadata.correct", str(correct))
        span.set_attribute(
            "langfuse.observation.output",
            f"correct={correct} expected={verdict.get('expected')}"
            + (f" note={note}" if note else ""),
        )
        return verdict
