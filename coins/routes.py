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


@router.get("/topup/{checkout_session_id}")
async def get_topup_status(
    checkout_session_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Has THIS checkout session's top-up been credited yet? (PAY-002)

    ``/pricing/success?topup=1`` polls this rather than watching the balance
    rise. The webhook frequently lands BEFORE the browser finishes redirecting
    back from Stripe, so a baseline-then-rise check can never observe the rise
    and would report a false timeout on a perfectly successful purchase — and
    the naive fix (treat any non-zero ``purchasedCoins`` as proof) confirms for
    every user alive, since everyone carries the 100-coin signup grant. The
    ledger row ``(user_id, 'topup', ref=<checkout session id>)`` written by
    ``coins/engine.py::credit`` IS the fact, so ask for it directly.

    Scoped to the caller's own ``user_id``: a guessed session id reveals
    nothing about anyone else's purchases, only whether the CALLER was
    credited for it.
    """
    from sqlalchemy import select as _select  # noqa: PLC0415

    from database.orm import CoinLedger  # noqa: PLC0415

    row = await db.scalar(
        _select(CoinLedger.id).where(
            CoinLedger.user_id == user_id,
            CoinLedger.kind == "topup",
            CoinLedger.ref == checkout_session_id,
        )
    )
    return {"credited": row is not None}


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
        # SEC-006 side finding: a valid JWT with no users row used to get a
        # row minted right here, with no signup grant and no ledger row —
        # unlike every other path that creates a user (upsert_user on
        # sign-in, credit()'s ON CONFLICT DO NOTHING fallback for a webhook
        # that beats the first login). That silently orphaned a user with a
        # 0-coin purchased bucket. Match auth/routes.py's ``GET /auth/me`` /
        # ``PUT /auth/level`` pattern instead: a JWT is only ever issued
        # after ``upsert_user`` has already created the row, so no row here
        # means something is actually wrong (e.g. a DB wipe that outlived
        # the token) — 404 rather than silently minting an under-provisioned
        # account.
        raise HTTPException(status_code=404, detail="User not found.")
    user.timezone = tz_name
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    return {"timezone": tz_name}
