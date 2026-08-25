"""Shared HTTP coin gate for PAY-002 drill/voice routes.

Charge order on every graded attempt endpoint: rate-limit check FIRST (free),
then coins (charge). The charge must happen BEFORE the route fires STT/judge
LLM work — a caller who can't pay should never burn provider budget.

On success the route MAY echo the new balance; on insufficiency it MUST
raise 402 with the exact detail shape the frontend maps to its "out of coins"
state. Developer role bypasses every gate (no charge, no lockout).

DB errors propagate — coin gates fail CLOSED (the route should 503, never
admit for free). A helper that catches ``SQLAlchemyError`` and returns 503 is
left to the route's own try/except so the gate itself stays a pure domain
function.
"""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from database.orm import User

from .engine import try_spend


async def admit_coins_or_402(
    db: AsyncSession,
    *,
    user_id: str,
    price: int,
    kind: str,
    ref: str | None = None,
) -> dict | None:
    """Charge ``price`` coins for ``kind`` or raise 402.

    Returns a dict ``{"spent": price, "available": M}`` on success for routes
    that want to echo the new balance, or None when developer bypass admitted
    without charging (the route can ignore that). On insufficiency raises:

        HTTPException 402 — detail ``{"code": "insufficient_coins", "needed": N,
        "available": M, "detail": "Not enough coins"}``

    The ``price``/``kind`` pair is the audit trail: every spend's ``kind``
    names the surface (``spend_attempt``, ``spend_letter``, etc.) so
    ``coin_ledger`` stays human-readable without joining another table.

    Callers MUST run their rate-limit check (``drill_try_admit``) BEFORE this
    helper — this function does not re-check rate limits, it charges only.

    Example::

        if not drill_try_admit(user_id):
            raise HTTPException(429, ...)
        try:
            await admit_coins_or_402(db, user_id=user_id, price=SATZ_ATTEMPT,
                                     kind="spend_attempt")
        except HTTPException as e:
            if e.status_code == 402:
                raise
            # SQLAlchemyError from try_spend propagated here — 503, not 402
            raise HTTPException(503, "billing temporarily unavailable")
        # ... STT/judge work below, only reached when paid or bypassed
    """
    result = await try_spend(db, user_id=user_id, amount=price, kind=kind, ref=ref)
    if result is not None:
        # Developer role is bypassed inside try_spend, which returns a result
        # without mutating, so the gate's db.commit() is skipped via the early
        # return None below.
        user_check = await db.scalar(select(User).where(User.id == user_id))
        if user_check is not None and (user_check.role or "") == "developer":
            return None
        # PAY-002: the gate is the transaction boundary for the charge —
        # "pay, then play". Downstream STT/LLM/validation failures (e.g. satz's
        # 422 "We couldn't hear anything") must NOT refund the charge — provider
        # cost is already incurred (carnival semantics). Committing here makes
        # every one of the ~15 call sites durable without each having to
        # remember. A mid-request commit is safe: SQLAlchemy starts a fresh
        # transaction for any later writes the route makes (satz's schedule
        # writes etc. still commit at their own site). On commit failure, fail
        # closed — never admit for free.
        try:
            await db.commit()
        except SQLAlchemyError:
            await db.rollback()
            raise HTTPException(status_code=503, detail="billing temporarily unavailable")
        return {"spent": price, "available": result.available}
    # Insufficient (or unknown user). Re-read for the 402 diagnostic.
    # Do NOT commit this path — nothing to persist; the lazy-refresh flush
    # rolls back harmlessly and re-runs on the next read.
    user2 = await db.scalar(select(User).where(User.id == user_id))
    if user2 is None:
        available = 0
    else:
        from .engine import today_key
        from .prices import DAILY_ALLOWANCE

        key = today_key(user2.timezone)
        if user2.allowance_day != key:
            allowance = DAILY_ALLOWANCE.get(user2.tier or "free", 0)
        else:
            allowance = user2.allowance_remaining or 0
        purchased = user2.purchased_coins or 0
        available = allowance + purchased
    raise HTTPException(
        status_code=402,
        detail={
            "code": "insufficient_coins",
            "needed": price,
            "available": available,
            "detail": "Not enough coins",
        },
    )
