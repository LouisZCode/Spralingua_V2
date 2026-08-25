"""HTTP routes for the coin system (PAY-002).

``GET /coins/balance`` and ``PUT /coins/timezone`` — both behind the same
session-JWT dependency every other authenticated route uses (``auth.deps.
get_current_user_id``). The frontend calls ``GET /coins/balance`` on mount
and after every priced action to refresh its coin pill, and ``PUT /coins/
timezone`` once with ``Intl.DateTimeFormat().resolvedOptions().timeZone``
so the daily 5am reset tracks the learner's wall-clock day rather than UTC.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from zoneinfo import ZoneInfo

from auth.deps import get_current_user_id
from database.connection import get_db
from database.orm import User

router = APIRouter(prefix="/coins", tags=["coins"])


@router.get("/balance")
async def get_balance(
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Balance snapshot for the coin pill + countdown.

    Shape: ``{"tier", "balance", "allowanceRemaining", "purchasedCoins",
    "dailyAllowance", "nextResetAt", "timezone"}`` — see ``coins/engine.py::
    balance`` for field semantics. ``nextResetAt`` is an ISO UTC string for
    the frontend's "coins return at …" countdown; ``timezone`` is the stored
    IANA name (or null).
    """
    # Import lazily so main.py's import-time router registration doesn't
    # require the coins package to be importable before the DB is.
    from coins.engine import balance  # noqa: PLC0415

    data = await balance(db, user_id=user_id)
    # Flush any lazy refresh without committing — the caller's get_db session
    # owns commit; we just ensure the day roll survives if this was the first
    # read of a new coin day.
    try:
        await db.commit()
    except Exception:
        await db.rollback()
    return data


class TimezoneBody(BaseModel):
    timezone: str


@router.put("/timezone")
async def put_timezone(
    body: TimezoneBody,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Store the learner's IANA timezone (idempotent).

    ``PUT`` with ``{"timezone": "Europe/Berlin"}``. Validated via
    ``zoneinfo.ZoneInfo`` — invalid → 400 with a clear message. The stored
    value is what ``coins/engine.py::today_key`` / ``next_reset_at`` read to
    align the daily 5am bucket with the learner's wall-clock day. NULL means
    UTC (the default before the frontend reports).
    """
    tz_name = (body.timezone or "").strip()
    if not tz_name:
        raise HTTPException(status_code=400, detail="timezone is required")
    try:
        ZoneInfo(tz_name)
    except Exception:
        raise HTTPException(status_code=400, detail=f"Unknown timezone: {tz_name!r}")
    user = await db.get(User, user_id)
    if user is None:
        # Valid JWT but no users row (DB wipe that outlived the token) — same
        # fail-soft as GET /balance for an unknown user: create the row
        # idempotently so PUT doesn't 404 on a legitimate first-time learner.
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        await db.execute(
            pg_insert(User).values(id=user_id, timezone=tz_name).on_conflict_do_nothing(index_elements=["id"])
        )
        # Re-read for the case where the row already existed — the insert was
        # a no-op and the timezone wasn't updated.
        user2 = await db.get(User, user_id)
        if user2 is not None and user2.timezone != tz_name:
            user2.timezone = tz_name
        try:
            await db.commit()
        except Exception:
            await db.rollback()
            raise
        return {"timezone": tz_name}
    user.timezone = tz_name
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    return {"timezone": tz_name}
