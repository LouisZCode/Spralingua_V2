"""Coin system (PAY-002) — re-exports for routers + gates."""

from .gate import admit_coins_or_402

from .engine import (
    SpendResult,
    balance,
    credit,
    next_reset_at,
    refresh_allowance,
    today_key,
    try_spend,
)
from .prices import (
    DAILY_ALLOWANCE,
    INTERVIEW_ANSWER,
    LETTER,
    SATZ_ATTEMPT,
    SIGNUP_GRANT,
    TOPUP_COINS,
    VOICE_EXCHANGE,
)

__all__ = [
    "DAILY_ALLOWANCE",
    "INTERVIEW_ANSWER",
    "LETTER",
    "SATZ_ATTEMPT",
    "SIGNUP_GRANT",
    "TOPUP_COINS",
    "VOICE_EXCHANGE",
    "SpendResult",
    "admit_coins_or_402",
    "balance",
    "credit",
    "next_reset_at",
    "refresh_allowance",
    "today_key",
    "try_spend",
]
