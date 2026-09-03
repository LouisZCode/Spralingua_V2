"""Coin engine (PAY-002): day math + two-bucket ledger writes.

Buckets: ``allowance`` (daily, resets at 5am local, lazy — no cron) and
``purchased_coins`` (persistent: signup grant + top-ups). Spending drains
allowance first, purchased second; a day's unused allowance never rolls over.

Day key: the "coin day" a timestamp belongs to is ``(local_now - 5h).date()``:
a wall-clock day that starts at 05:00 local instead of midnight. The same
formula drives both ``today_key(timezone)`` and ``next_reset_at(timezone)``
(the next 05:00 local as an aware UTC datetime for the UI's "coins return
at …" countdown).

Every write respects ``SPRALINGUA_TEST_GUARD`` (config.settings.
test_guard_enabled / database.repository._assert_test_user): when the guard
is on, a non-``test-*`` user_id is refused before any DB statement is built.
Read callers (balance/next_reset_at/today_key) are unguarded — same pattern
as the read helpers in ``database/repository.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from datetime import timezone as _dt_timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

from loguru import logger
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import test_guard_enabled
from database.orm import CoinLedger, User

from .prices import DAILY_ALLOWANCE

# PAY-002: 5am local — the daily boundary. Chosen per the pricing doc
# (cost_price.md) so a learner's "day" aligns with their morning, not UTC.
_RESET_HOUR = 5


def _zone(timezone: str | None) -> ZoneInfo:
    """IANA timezone from ``users.timezone``, falling back to UTC for NULL."""
    name = (timezone or "").strip() or "UTC"
    try:
        return ZoneInfo(name)
    except Exception:
        # An invalid stored value (a typo'd PUT) should not 500 the balance
        # read — fall back visibly via the returned "UTC" rather than crash.
        logger.warning(f"Invalid timezone {name!r} — falling back to UTC")
        return ZoneInfo("UTC")


def today_key(timezone_str: str | None, now: datetime | None = None) -> date:
    """The user-local "coin day" that ``now`` belongs to.

    ``(local_now - 5h).date()`` — a 05:00-starting day. ``now`` defaults to
    ``datetime.now(timezone.utc)``; an explicit aware or naive ``now`` is
    interpreted as UTC when naive (matching the DB columns' convention).
    """
    if now is None:
        now = datetime.now(_dt_timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=_dt_timezone.utc)
    local = now.astimezone(_zone(timezone_str))
    return (local - timedelta(hours=_RESET_HOUR)).date()
def next_reset_at(timezone_str: str | None, now: datetime | None = None) -> datetime:
    """Next 05:00 in ``timezone`` as an aware UTC datetime (for the UI).

    If ``now`` is already past today's 05:00 local, the next reset is
    tomorrow's 05:00; otherwise it is today's. Always strictly in the future
    relative to ``now`` (or equal when ``now`` IS 05:00:00.000 exactly, in
    which case we return the *next* day's — the day has already rolled).
    """
    if now is None:
        now = datetime.now(_dt_timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=_dt_timezone.utc)
    tz = _zone(timezone_str)
    local = now.astimezone(tz)
    # Today's 05:00 in this timezone, as an aware datetime.
    today_reset_local = datetime.combine(local.date(), time(_RESET_HOUR, 0), tzinfo=tz)
    if local < today_reset_local:
        nxt = today_reset_local
    else:
        nxt = today_reset_local + timedelta(days=1)
    return nxt.astimezone(_dt_timezone.utc)


_TEST_PREFIX = "test-"


def _assert_test_user(user_id: str) -> None:
    """Same guard as ``database.repository._assert_test_user`` (TEST-001).

    Any NEW write helper added to a repository-style path must follow this
    pattern — refuse a non-``test-*`` id when the guard is on, before any DB
    statement is built, so a stray write to ``0001`` fails loudly instead of
    silently mutating real learning data. Read helpers are deliberately
    unguarded (same as ``database/repository.py``'s reads).
    """
    if test_guard_enabled and not user_id.startswith(_TEST_PREFIX):
        raise RuntimeError(f"test guard: refusing write for non-test user {user_id!r}")


async def refresh_allowance(db: AsyncSession, user: User) -> None:
    """Lazily refresh the daily allowance bucket if its day is stale.

    In-transaction, no commit — the caller owns the transaction (either the
    surrounding ``try_spend`` FOR UPDATE block or the balance read's own
    session). No cron: a user who doesn't access the app for a week simply
    keeps a stale ``allowance_day`` until their next read/spend, at which
    point it snaps to today's ``DAILY_ALLOWANCE[tier]``. Unused prior-day
    coins are discarded (they do not roll over) — overwriting is correct.

    SEC-006: the comparison is deliberately ``key > user.allowance_day``
    (monotonic), NOT ``!=``. The old ``!=`` refilled on ANY key change in
    EITHER direction, and ``PUT /coins/timezone`` accepts any zoneinfo name,
    unlimited times, with no ledger row — a learner could bounce between two
    zones whose ``today_key`` disagrees (they only need to straddle the
    05:00-local boundary from opposite sides) and re-mint the full daily
    allowance on every ``GET /coins/balance`` in between. Since PAY-004
    (2026-09-03) the free tier carries a 75-coin allowance too, so this was
    exploitable on every account, not just paid ones. A strictly later key
    still refills normally (the ordinary next-day case, or ``allowance_day
    is None`` for a never-refreshed row); a key that goes backward or stays
    put is left untouched, so hopping to an "earlier" zone can only shrink
    what ``today_key`` reports, never bump the stored ``allowance_day``
    forward — no second refill.

    A westward-relocation carve-out (also refill when the stored key is MORE
    THAN a day ahead of the current one, i.e. a learner who genuinely flew
    from a far-east zone to a far-west one) was considered but dropped after
    checking ``today_key`` across the extremes: ``Pacific/Kiritimati``
    (UTC+14) and ``Pacific/Midway``/``Etc/GMT+12`` (UTC-12) span 26 hours,
    so at some instants their keys differ by TWO calendar days, not one —
    the same pair an attacker would use to fake a "relocation" and trip the
    carve-out for a second same-day refill. Strict ``>`` with no exception
    is the whole fix; see the SEC-006 write-up for the verification.
    Accepted cost for a genuine westward move: the stored key can sit AHEAD of
    the new zone's date, so the next refill is delayed — measured worst case
    Berlin → Los Angeles is 33 h (the 05:00-local boundaries are 9 h apart),
    never indefinite, and no coins are lost. An eastward move gets one extra
    refill on arrival, once.
    """
    key = today_key(user.timezone)
    if user.allowance_day is None or key > user.allowance_day:
        user.allowance_day = key
        user.allowance_remaining = DAILY_ALLOWANCE.get(user.tier or "free", 0)


@dataclass(frozen=True)
class SpendResult:
    """Successful spend — new balances after the deduction."""

    allowance_remaining: int
    purchased_coins: int
    available: int  # allowance + purchased after the spend


async def try_spend(
    db: AsyncSession,
    *,
    user_id: str,
    amount: int,
    kind: str,
    ref: str | None = None,
) -> SpendResult | None:
    """Attempt to spend ``amount`` coins for ``user_id``.

    Locks the ``users`` row (SELECT … FOR UPDATE), refreshes a stale daily
    bucket, then either deducts ``amount`` (allowance first, purchased second)
    and writes a ``coin_ledger`` row, or returns ``None`` when funds are
    insufficient. Developer role (``users.role == "developer"``) bypasses the
    gate entirely: no deduction, no ledger row, log at debug.

    Insufficiency returns ``None`` — the caller (``admit_coins_or_402``) turns
    that into a 402 with ``needed``/``available``. DB errors propagate — coin
    gates fail CLOSED (the route should 503, never admit for free).

    ``amount`` must be positive; zero/negative is a caller bug and raises
    ``ValueError`` before touching the DB.
    """
    _assert_test_user(user_id)
    if amount <= 0:
        raise ValueError(f"try_spend amount must be positive, got {amount}")
    # FOR UPDATE: two concurrent spends for the same user serialize on the row
    # lock so allowance/purchased math is never raced (lost update). The
    # refresh check inside the lock is what makes the lazy day reset correct
    # under concurrency — whichever transaction wins recomputes the bucket first.
    user: User | None = await db.scalar(
        select(User).where(User.id == user_id).with_for_update()
    )
    if user is None:
        # No users row yet (a token that outlived a DB wipe, or a synthetic
        # test id). Treat as insufficient rather than crash — the route will
        # 402, which is the right UX for "no billable account".
        return None

    # Developer bypass: every coin gate is open, no charge, no ledger row.
    # Must be checked AFTER the row is locked/fetched but BEFORE the refresh
    # or deduction — a developer's allowance bucket is not even refreshed.
    if (user.role or "") == "developer":
        logger.debug(f"coin bypass: developer {user_id} admitted for {kind} without charge")
        # Return a synthetic "success" with current balances (no mutation).
        # Refresh for display only — don't persist it, don't consume it.
        key = today_key(user.timezone)
        if user.allowance_day != key:
            allowance = DAILY_ALLOWANCE.get(user.tier or "free", 0)
        else:
            allowance = user.allowance_remaining or 0
        purchased = user.purchased_coins or 0
        return SpendResult(
            allowance_remaining=allowance,
            purchased_coins=purchased,
            available=allowance + purchased,
        )

    await refresh_allowance(db, user)
    allowance = user.allowance_remaining or 0
    purchased = user.purchased_coins or 0
    available = allowance + purchased
    if available < amount:
        return None

    # Deduct: allowance first, purchased second.
    take_allowance = min(allowance, amount)
    take_purchased = amount - take_allowance
    user.allowance_remaining = allowance - take_allowance
    user.purchased_coins = purchased - take_purchased

    db.add(
        CoinLedger(
            id=uuid4().hex,
            user_id=user_id,
            kind=kind,
            ref=ref,
            delta_allowance=-take_allowance,
            delta_purchased=-take_purchased,
        )
    )
    await db.flush()
    new_available = (user.allowance_remaining or 0) + (user.purchased_coins or 0)
    return SpendResult(
        allowance_remaining=user.allowance_remaining or 0,
        purchased_coins=user.purchased_coins or 0,
        available=new_available,
    )


async def credit(
    db: AsyncSession,
    *,
    user_id: str,
    amount: int,
    kind: str,
    ref: str,
) -> bool:
    """Add ``amount`` coins to ``user_id``'s purchased bucket, idempotently.

    ``kind``/``ref`` together are the idempotency key (the UNIQUE on
    ``(user_id, kind, ref)``): a top-up's Stripe session id rides as ``ref``,
    so a crash after crediting but before ``record_stripe_event`` — followed
    by Stripe's redelivery — makes the second credit a cheap no-op
    (``ON CONFLICT DO NOTHING``), not a double-grant. This is the property
    that lets the webhook's check→process→record ordering stay safely
    retryable (see ``payments/webhook.py::handle_stripe_event``).

    Returns True if the credit actually landed, False if it was a duplicate
    (the UNIQUE suppressed it). ``amount`` must be positive.
    """
    _assert_test_user(user_id)
    if amount <= 0:
        raise ValueError(f"credit amount must be positive, got {amount}")
    if not ref:
        raise ValueError("credit ref must be non-empty (idempotency key)")
    # Insert the ledger row first with ON CONFLICT DO NOTHING — if this is a
    # duplicate (same user_id/kind/ref), the insert is suppressed and we must
    # NOT bump purchased_coins a second time. Doing the user bump first would
    # double-credit on a redelivery.
    result = await db.execute(
        pg_insert(CoinLedger)
        .values(
            id=uuid4().hex,
            user_id=user_id,
            kind=kind,
            ref=ref,
            delta_allowance=0,
            delta_purchased=amount,
        )
        .on_conflict_do_nothing(index_elements=["user_id", "kind", "ref"])
    )
    if result.rowcount == 0:
        # Duplicate — the ledger says this grant already happened, so the
        # purchased bucket must already carry it; do not bump again.
        return False
    # First time: bump the persistent bucket. The users row may not exist yet
    # in a race (a webhook beating the first login's upsert) — create it
    # idempotently if needed, mirroring create_session_row's user upsert.
    await db.execute(
        pg_insert(User)
        .values(id=user_id)
        .on_conflict_do_nothing(index_elements=["id"])
    )
    # Re-fetch for update to apply the bump atomically against concurrent
    # spends/credits for the same user.
    user = await db.scalar(select(User).where(User.id == user_id).with_for_update())
    if user is not None:
        user.purchased_coins = (user.purchased_coins or 0) + amount
        await db.flush()
    return True


async def balance(db: AsyncSession, *, user_id: str) -> dict:
    """Balance snapshot for ``GET /coins/balance`` (PAY-002).

    Refreshes a stale daily bucket WITHOUT FOR UPDATE (read path — no spend
    is happening, so racing a concurrent spend here only means the UI briefly
    shows a stale total before the next read). Returns the shape the frontend
    builds its coin pill + countdown against.
    """
    user: User | None = await db.scalar(select(User).where(User.id == user_id))
    if user is None:
        # No row yet — treat as a free user with nothing yet granted (the
        # signup grant lands via upsert_user's path, not here). Show zeros so
        # the UI doesn't 404 on first load after auth.
        return {
            "tier": "free",
            "balance": 0,
            "allowanceRemaining": 0,
            "purchasedCoins": 0,
            "dailyAllowance": DAILY_ALLOWANCE.get("free", 0),
            "nextResetAt": next_reset_at(None).isoformat(),
            "timezone": None,
        }
    # Lazy refresh — persists if the day rolled, so the next read is cheap.
    # No FOR UPDATE: this is the read path (see docstring above). A
    # concurrent spend's lock will serialize correctly — we may briefly show
    # a pre-spend total, which the next poll corrects.
    await refresh_allowance(db, user)
    # Persist the refresh if one happened (the mutation above is on the ORM
    # instance; flush it so the allowance_day/remaining survive). No commit
    # here — the caller owns commit/rollback (get_db yields without committing;
    # the route's try/except owns it). A flush without commit is enough to
    # keep the row consistent for the SELECT … FOR UPDATE in try_spend.
    try:
        await db.flush()
    except Exception:
        # A flush failure on the read path should not 500 the balance call —
        # the next read will simply re-refresh. Log and keep serving the
        # in-memory (correct) values below.
        logger.warning("coin balance flush failed (non-fatal) for user {}", user_id)

    allowance = user.allowance_remaining or 0
    purchased = user.purchased_coins or 0
    tier = user.tier or "free"
    return {
        "tier": tier,
        "balance": allowance + purchased,
        "allowanceRemaining": allowance,
        "purchasedCoins": purchased,
        "dailyAllowance": DAILY_ALLOWANCE.get(tier, 0),
        "nextResetAt": next_reset_at(user.timezone).isoformat(),
        "timezone": user.timezone,
    }


async def spend_capped(
    db: AsyncSession,
    *,
    user_id: str,
    owed: int,
    kind: str,
    ref: str | None = None,
) -> tuple[int, SpendResult | None]:
    """Spend ``owed`` coins but cap at what's actually available (voice path).

    Unlike ``try_spend`` which fails closed on insufficiency, the voice
    disconnect path must NEVER go negative — a multi-tab adversary can open
    3 concurrent sessions (LEARN_MAX_CONCURRENT_PER_USER) each admitted
    against the same pre-session balance, then disconnect all three at once.
    The accept gate bounded the worst-case exposure (each session's bundle was
    checked up front), but the disconnect charge must still be best-effort:
    spend ``min(owed, available)``. If ``available < owed`` log a warning
    (telemetry for that abuse pattern) and spend what's left; if nothing is
    left, return ``(0, None)``.

    Developer bypass is the same as ``try_spend``: no charge at all.

    Returns ``(actually_spent, SpendResult|None)`` where ``actually_spent``
    is the number of coins deducted (0 when nothing was available) and the
    SpendResult is None when nothing was spent (indistinguishable from the
    insufficient-funds case in ``try_spend``, which is fine — the caller
    never needs a 402 here).
    """
    _assert_test_user(user_id)
    if owed <= 0:
        return 0, None
    user: User | None = await db.scalar(
        select(User).where(User.id == user_id).with_for_update()
    )
    if user is None:
        return 0, None
    if (user.role or "") == "developer":
        logger.debug(f"coin bypass: developer {user_id} voice disconnect without charge")
        key = today_key(user.timezone)
        allowance = DAILY_ALLOWANCE.get(user.tier or "free", 0) if user.allowance_day != key else (user.allowance_remaining or 0)
        purchased = user.purchased_coins or 0
        return 0, SpendResult(allowance_remaining=allowance, purchased_coins=purchased, available=allowance + purchased)

    await refresh_allowance(db, user)
    allowance = user.allowance_remaining or 0
    purchased = user.purchased_coins or 0
    available = allowance + purchased
    if available <= 0:
        return 0, None
    to_spend = min(owed, available)
    if to_spend < owed:
        logger.warning(
            f"coin spend_capped: user {user_id} owed {owed} but only {available} available — "
            f"spending {to_spend} (multi-tab abuse? max 3 concurrent)"
        )
    take_allowance = min(allowance, to_spend)
    take_purchased = to_spend - take_allowance
    user.allowance_remaining = allowance - take_allowance
    user.purchased_coins = purchased - take_purchased
    db.add(
        CoinLedger(
            id=uuid4().hex,
            user_id=user_id,
            kind=kind,
            ref=ref,
            delta_allowance=-take_allowance,
            delta_purchased=-take_purchased,
        )
    )
    await db.flush()
    return to_spend, SpendResult(
        allowance_remaining=user.allowance_remaining or 0,
        purchased_coins=user.purchased_coins or 0,
        available=(user.allowance_remaining or 0) + (user.purchased_coins or 0),
    )
