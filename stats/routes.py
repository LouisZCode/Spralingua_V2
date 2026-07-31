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

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from auth.deps import get_current_user_id
from database.connection import get_db
from database.repository import (
    count_active_days,
    count_sessions_since,
    load_attempt_series,
    load_focus_with_recency,
    load_period_summary,
    load_recent_sessions,
    load_retired_patterns,
    load_top_errors,
    satz_word_recall,
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

    return {
        "week": week,
        "prevWeek": prev_week,
        "today": today,
        "focus": focus,
        "retired": retired,
        "series": series,
    }


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


# ── REC-001: the weekly observe→recommend loop ────────────────────────────
# Mon–Sun calendar week (UTC, same convention as everything above): the
# learner's first ~3 active days gather data; from then on the practice menu
# may show ONE recommended pillar. Rules are deliberately few and ordered by
# severity — words gate everything, grammar next, free production last.
# "No clear signal" is a first-class answer: recommendation stays null and
# the menu shows nothing.
_REC_ACTIVE_DAYS = 3  # gate: active days this week before recommending
_REC_SATZ_MIN = 10  # graded satz attempts needed before recall counts
_REC_SATZ_FLOOR = 0.75  # weekly word-recall below this → vocabulary practice
_REC_PATTERN_MIN = 3  # 7-day slips on one grammar pattern → Flow


@router.get("/me/recommendation")
async def get_my_recommendation(
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    now = datetime.now(timezone.utc)
    monday = now.date() - timedelta(days=now.weekday())
    week_start = datetime.combine(monday, time.min, tzinfo=timezone.utc)
    week_start_naive = datetime.combine(monday, time.min)

    active_days = await count_active_days(
        db,
        user_id=user_id,
        drill_start=week_start,
        session_start=week_start_naive,
    )
    if active_days < _REC_ACTIVE_DAYS:
        return {"recommendation": None, "activeDays": active_days}

    # 1) Weak word recall → Vocabulary Practice. Same metric as the dosing
    # throttle (COALESCE(word_ok, correct)) so the two never disagree.
    attempts, recall = await satz_word_recall(db, user_id=user_id, start=week_start)
    if attempts >= _REC_SATZ_MIN and recall < _REC_SATZ_FLOOR:
        return {
            "recommendation": {
                "pillar": "satz",
                "reason": (
                    f"Your word recall is at {round(recall * 100)}% this week — "
                    "a focused vocabulary round pays off most today."
                ),
            },
            "activeDays": active_days,
        }

    # 2) A hot grammar pattern → Flow (it chases the pattern across every
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

    # 3) Drilling all week without one real conversation → Tandem.
    tandem_sessions = await count_sessions_since(
        db, user_id=user_id, lesson_id="tandem", start=week_start_naive
    )
    if tandem_sessions == 0:
        return {
            "recommendation": {
                "pillar": "tandem",
                "reason": (
                    "You've been practicing all week but haven't had a real "
                    "conversation yet — talk to Lena today."
                ),
            },
            "activeDays": active_days,
        }

    return {"recommendation": None, "activeDays": active_days}
