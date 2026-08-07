"""HTTP route for cross-drill practice stats (DATA-004).

One read-only endpoint, ``GET /me/stats``. All SQL lives in
``database/repository.py`` — this module only picks the four time windows
and assembles them into the response contract the frontend builds against.

Time windows (all against ``drill_attempts.created_at``, which is
``TIMESTAMPTZ`` — Postgres stores/returns it as UTC regardless of session
timezone, so every cutoff here is a tz-aware UTC ``datetime``):

- ``week``     — rolling last 7 days (``now - 7d`` .. ``now``).
- ``prevWeek`` — the 7 days before that (``now - 14d`` .. ``now - 7d``).
- ``today``    — since UTC midnight. Not the caller's local midnight — there
  is no per-user timezone stored anywhere in this schema yet, and UTC
  midnight is a stable, server-side-computable boundary. A learner west of
  UTC will see "today" roll over a few hours into their evening; documented
  here rather than silently wrong.
"""

from datetime import datetime, time, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from agents.load_prompts import tandem_lesson_ids
from auth.deps import get_current_user_id
from database.connection import get_db
from database.repository import (
    complete_daily_mode,
    count_active_days,
    count_sessions_since,
    has_attempt_today,
    load_attempt_series,
    load_focus_with_recency,
    load_period_summary,
    load_recent_sessions,
    load_retired_patterns,
    load_streak,
    load_top_errors,
)

router = APIRouter(tags=["stats"])


@router.get("/me/stats")
async def get_my_stats(
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """This week / last week / today across all six drills, plus the
    recency-weighted grammar focus and recently retired patterns.

    Every list is empty and every total is 0 for a brand-new user — there's
    no "no data" error case, just an empty shape (200 always).
    """
    now = datetime.now(timezone.utc)
    week_start = now - timedelta(days=7)
    prev_week_start = now - timedelta(days=14)
    today_start = datetime.combine(now.date(), time.min, tzinfo=timezone.utc)

    week = await load_period_summary(db, user_id=user_id, start=week_start)
    week["topErrors"] = await load_top_errors(
        db, user_id=user_id, start=week_start, limit=5
    )

    prev_week = await load_period_summary(
        db, user_id=user_id, start=prev_week_start, end=week_start
    )
    prev_week["topErrors"] = await load_top_errors(
        db, user_id=user_id, start=prev_week_start, end=week_start, limit=5
    )

    today_summary = await load_period_summary(db, user_id=user_id, start=today_start)
    today = {
        "attemptsTotal": today_summary["attemptsTotal"],
        "topErrors": await load_top_errors(
            db, user_id=user_id, start=today_start, limit=3
        ),
    }

    focus = await load_focus_with_recency(db, user_id=user_id, limit=3)
    retired = await load_retired_patterns(db, user_id=user_id, limit=10)
    # DATA-005: 56 days of daily buckets — the frontend derives both the day
    # and the week view from this one series.
    series = await load_attempt_series(db, user_id=user_id, start=now - timedelta(days=56))
    # GAME-001: forgiving daily streak — O(1) read off the users cache.
    streak = await load_streak(db, user_id=user_id)

    return {
        "week": week,
        "prevWeek": prev_week,
        "today": today,
        "focus": focus,
        "retired": retired,
        "series": series,
        "streak": streak,
    }


class ModeCompleteBody(BaseModel):
    mode: Literal["satz", "flow"]


@router.post("/streak/mode")
async def complete_streak_mode(
    body: ModeCompleteBody,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Frontend "I finished a round" ping for the two client-detected modes
    (GAME-001 v2): Satzschmiede posts when its end panel shows, Flow posts
    when its round summary shows.

    Security note: only ``satz`` and ``flow`` are postable here at all — the
    ``Literal`` on ``ModeCompleteBody.mode`` rejects anything else at the
    request-validation layer, before this handler even runs. ``tandem`` and
    ``briefkasten`` are credited server-side from their own completion paths
    (conversation disconnect in ``pipeline/factory.py``, the second letter
    attempt in ``briefkasten/routes.py``) and must never be claimable by a
    client — a learner could otherwise POST a mode they never actually did.

    ``has_attempt_today`` is the anti-spoof check: a POST for a mode with no
    matching ``drill_attempts`` row logged today is silently NOT credited —
    this returns 200 with an unchanged streak, not an error. A Flow round
    that finished with zero graded items simply doesn't count; that's a
    normal outcome of how the learner played it, not a client error worth
    surfacing as a 4xx.

    Either way the response is the fresh :func:`load_streak` shape, so the
    caller never needs a second round-trip to see whether today just got
    earned.
    """
    if await has_attempt_today(db, user_id=user_id, mode=body.mode):
        await complete_daily_mode(db, user_id=user_id, mode=body.mode)
    return await load_streak(db, user_id=user_id)


@router.get("/me/sessions")
async def get_my_sessions(
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Recent finished conversation sessions, newest first (BUG-010 /
    UI-005 minimal). Tandem rows include the debrief dict — this is the
    surface where a user-ended tandem's notes become readable, since that
    flow no longer waits for the in-place modal. Empty list for a new user.
    """
    return {"sessions": await load_recent_sessions(db, user_id=user_id, limit=10)}


# ── REC-001: the rolling observe→recommend loop ───────────────────────────
# Rolling last-7-days window (UTC, same convention as everything above), not
# a Mon–Sun calendar week — a calendar week resets on Monday and blanks the
# banner for days until the 3-active-days gate re-fills. The learner's first
# ~3 active days in the window gather data; from then on the practice menu
# may show ONE recommended pillar. Rules are deliberately few: vocabulary
# recall is deliberately NOT one of them — forgetting words is chronic and
# expected in vocabulary learning, and Satzschmiede is a core daily exercise
# rather than a specialized fix, so a recall-based rule would dominate the
# banner forever. Recommendations point only at the grammar-focused Flow and
# at Tandem. "No clear signal" is a first-class answer: recommendation stays
# null and the menu shows nothing.
_REC_ACTIVE_DAYS = 3  # gate: active days in the window before recommending
_REC_PATTERN_MIN = 3  # 7-day slips on one grammar pattern → Flow


@router.get("/me/recommendation")
async def get_my_recommendation(
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    now = datetime.now(timezone.utc)
    week_start = now - timedelta(days=7)
    week_start_naive = week_start.replace(tzinfo=None)

    active_days = await count_active_days(
        db,
        user_id=user_id,
        drill_start=week_start,
        session_start=week_start_naive,
    )
    if active_days < _REC_ACTIVE_DAYS:
        return {"recommendation": None, "activeDays": active_days}

    # 1) A hot grammar pattern → Flow (it chases the pattern across every
    # exercise). Reuses the recency-weighted focus the coach already computes.
    focus = await load_focus_with_recency(db, user_id=user_id, limit=1)
    if focus and focus[0]["count7d"] >= _REC_PATTERN_MIN:
        label = focus[0]["label"]
        return {
            "recommendation": {
                "pillar": "flow",
                "reason": (
                    f"“{label}” tripped you {focus[0]['count7d']}× this week — "
                    "a Flow round chases it across every exercise."
                ),
                "patternLabel": label,
            },
            "activeDays": active_days,
        }

    # 2) Drilling all week without one real conversation → Tandem. A chat
    # with ANY partner counts (TAND-008: Lena or Paul).
    tandem_sessions = await count_sessions_since(
        db, user_id=user_id, lesson_ids=sorted(tandem_lesson_ids()), start=week_start_naive
    )
    if tandem_sessions == 0:
        return {
            "recommendation": {
                "pillar": "tandem",
                "reason": (
                    "You've been practicing all week but haven't had a real "
                    "conversation yet — your tandem partners are waiting."
                ),
            },
            "activeDays": active_days,
        }

    return {"recommendation": None, "activeDays": active_days}
