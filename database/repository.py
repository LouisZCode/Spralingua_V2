"""Two named DB ops the WebSocket pipeline needs (DATA-001).

Both functions take an ``AsyncSession`` from the caller (no internal session
creation) so the caller owns the transaction scope. They re-raise on
``SQLAlchemyError``; ``pipeline/factory.py`` wraps each call in its own
``try/except`` for the non-fatal contract — a DB outage must not block
audio export, session logger close, or OTel flush.

We deliberately don't keep this module as ORM-only; the ``users`` upsert is
expressed via the Postgres ``ON CONFLICT DO NOTHING`` dialect helper so the
operation is idempotent across reconnects without a SELECT-then-INSERT race.
"""

from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from loguru import logger
from sqlalchemy import Text, cast, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import test_guard_enabled
from grammar import load_taxonomy

from .orm import (
    ActivitySession,
    DailyModeCompletion,
    DrillAttempt,
    StripeEvent,
    Subscription,
    User,
    UserCard,
    UserError,
    VocabCard,
)

# TEST-001: prefix every isolated fixture account must use (scripts/test_user.py
# enforces the same prefix on create/destroy).
_TEST_USER_PREFIX = "test-"


def _assert_test_user(user_id: str) -> None:
    """TEST-001 guard rail: with SPRALINGUA_TEST_GUARD=1 (config.settings.
    test_guard_enabled), every write helper below that takes a user_id calls
    this FIRST, before any DB statement is built. Off by default — a normal
    prod/dev run never sets the env var, so this is a no-op there. On, a
    non-``test-*`` id raises before a single row is touched, which is what
    would have caught the TEST-001 incident (a sim run silently retiring two
    real ``user_errors`` rows on ``0001``) at the source instead of by hand
    afterward. ``finalize_session_row`` is deliberately not guarded this way
    — it takes only a ``session_id``, not a ``user_id``."""
    if test_guard_enabled and not user_id.startswith(_TEST_USER_PREFIX):
        raise RuntimeError(
            f"test guard: refusing write for non-test user {user_id!r}"
        )


async def create_session_row(
    db: AsyncSession,
    *,
    session_id: str,
    user_id: str,
    lesson_id: str,
    voice: str | None,
    started_at: datetime,
    audio_path: str,
    lesson_snapshot: dict,
) -> None:
    """Upsert the user row + insert one ``activity_session`` row.

    ``session_id`` is the 32-char hex string from ``uuid4().hex`` at
    ``pipeline/factory.py:60``; we cast to ``UUID`` here so the column type
    stays native ``uuid``. ``activity_session.level`` and ``.situation``
    columns are left in the schema for now (nullable) and simply not
    written — they were the runtime knobs we removed in favor of YAML
    ``default_level``. Migration to drop them can come with the next batch.
    """
    _assert_test_user(user_id)
    try:
        # Idempotent user upsert — repeat connects with same user_id are a no-op.
        await db.execute(
            pg_insert(User)
            .values(id=user_id)
            .on_conflict_do_nothing(index_elements=["id"])
        )
        db.add(
            ActivitySession(
                id=UUID(session_id),
                user_id=user_id,
                lesson_id=lesson_id,
                voice=voice,
                started_at=started_at,
                audio_path=audio_path,
                lesson_snapshot=lesson_snapshot,
            )
        )
        await db.commit()
    except SQLAlchemyError:
        await db.rollback()
        raise


async def finalize_session_row(
    db: AsyncSession,
    *,
    session_id: str,
    ended_at: datetime,
    ended_by: str | None,
    transcript: str | None,
    goal_eval: dict | None,
    pron_eval: dict | None,
    error_eval: dict | None,
    passed: bool | None,
) -> None:
    """Patch the row inserted on connect with the post-session outcome."""
    try:
        result = await db.execute(
            update(ActivitySession)
            .where(ActivitySession.id == UUID(session_id))
            .values(
                ended_at=ended_at,
                ended_by=ended_by,
                transcript=transcript,
                goal_eval=goal_eval,
                pron_eval=pron_eval,
                error_eval=error_eval,
                passed=passed,
            )
        )
        if result.rowcount == 0:
            logger.warning(
                f"finalize_session_row: no row matched session_id={session_id} "
                f"(create_session_row likely never ran for this connect)"
            )
        await db.commit()
    except SQLAlchemyError:
        await db.rollback()
        raise


async def upsert_user(
    db: AsyncSession,
    *,
    user_id: str,
    email: str | None,
    name: str | None,
    picture: str | None,
) -> tuple[str, str]:
    """Insert or refresh a user from a verified Google sign-in (AUTH-001).

    Keyed on the Google ``sub`` (``user_id``). On a repeat sign-in we refresh the
    mutable profile fields (the user may have changed their Google name/avatar)
    and stamp ``last_login_at``. ``created_at`` keeps its first-insert value via
    the server default and is left untouched on update.

    ``role`` and ``tier`` (PAY-001) are deliberately NOT in the conflict update
    set — both are set out-of-band (SQL for role, the Stripe webhook for tier)
    and must survive re-logins — and we ``RETURNING`` both so the caller can
    embed the fresh values in the session JWT + sign-in response. New rows get
    the column defaults ("normal" / "free").

    CHORE-001: ``email`` is lowercased here (the write site) for case-insensitive
    uniqueness — Google itself is case-insensitive on the local part, so two
    sign-ins that differ only in case must land on the same row.

    Returns ``(role, tier)``.
    """
    _assert_test_user(user_id)
    try:
        stmt = pg_insert(User).values(
            id=user_id,
            email=email.lower() if email else None,
            name=name,
            picture=picture,
            last_login_at=func.now(),
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["id"],
            set_={
                "email": stmt.excluded.email,
                "name": stmt.excluded.name,
                "picture": stmt.excluded.picture,
                "last_login_at": stmt.excluded.last_login_at,
            },
        ).returning(User.role, User.tier)
        result = await db.execute(stmt)
        role, tier = result.one()
        await db.commit()
        return role, tier
    except SQLAlchemyError:
        await db.rollback()
        raise


# ── Stripe billing (PAY-001) ──────────────────────────────────────────────
# ``subscriptions`` is a local mirror of Stripe subscription state, one row
# per user; ``stripe_events`` is the webhook dedup ledger — Stripe delivery
# is at-least-once, so every webhook handler must check-then-skip a
# previously applied event id before mutating tier state.


async def record_stripe_event(
    db: AsyncSession, *, event_id: str, event_type: str
) -> bool:
    """Idempotency gate for the Stripe webhook handler (PAY-001).

    ``INSERT ... ON CONFLICT DO NOTHING`` keyed on the Stripe event id
    itself. Returns True the first time this event id is seen — the caller
    should process it — or False if it was already recorded, meaning this
    is a Stripe redelivery the caller should skip.

    Not user-scoped (the table carries no ``user_id``), so no test-guard
    check here — same reasoning as ``finalize_session_row``, which is also
    deliberately unguarded because it takes no ``user_id``.
    """
    try:
        result = await db.execute(
            pg_insert(StripeEvent)
            .values(id=event_id, type=event_type)
            .on_conflict_do_nothing(index_elements=["id"])
        )
        await db.commit()
        return result.rowcount > 0
    except SQLAlchemyError:
        await db.rollback()
        raise


async def upsert_subscription(
    db: AsyncSession,
    *,
    user_id: str,
    stripe_customer_id: str,
    stripe_subscription_id: str | None = None,
    stripe_price_id: str | None = None,
    tier: str,
    status: str,
    current_period_end: datetime | None = None,
) -> None:
    """Upsert the one ``subscriptions`` row for ``user_id`` (PAY-001).

    One row per user (``UNIQUE`` on ``user_id``): the first webhook seen for
    this user inserts a fresh row, minting its own ``uuid4().hex`` id; every
    later webhook updates that same row in place and bumps ``updated_at``.
    Same non-fatal contract as the other ledger ops: re-raises on
    ``SQLAlchemyError``, the caller owns the wrapping.
    """
    _assert_test_user(user_id)
    now = datetime.now()
    try:
        stmt = pg_insert(Subscription).values(
            id=uuid4().hex,
            user_id=user_id,
            stripe_customer_id=stripe_customer_id,
            stripe_subscription_id=stripe_subscription_id,
            stripe_price_id=stripe_price_id,
            tier=tier,
            status=status,
            current_period_end=current_period_end,
            updated_at=now,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["user_id"],
            set_={
                "stripe_customer_id": stmt.excluded.stripe_customer_id,
                "stripe_subscription_id": stmt.excluded.stripe_subscription_id,
                "stripe_price_id": stmt.excluded.stripe_price_id,
                "tier": stmt.excluded.tier,
                "status": stmt.excluded.status,
                "current_period_end": stmt.excluded.current_period_end,
                "updated_at": stmt.excluded.updated_at,
            },
        )
        await db.execute(stmt)
        await db.commit()
    except SQLAlchemyError:
        await db.rollback()
        raise


async def seen_stripe_event(db: AsyncSession, *, event_id: str) -> bool:
    """Read-only idempotency check for the Stripe webhook handler (PAY-001):
    has this event id already been recorded in ``stripe_events``?

    Style-matched to ``get_subscription`` — read-only, no commit, caller owns
    the transaction. The webhook handler calls this BEFORE processing an
    event (check -> process -> ``record_stripe_event``), not after — see
    ``payments/webhook.py::handle_stripe_event`` for why recording first
    would silently lose events on a mid-processing crash.
    """
    return (
        await db.scalar(select(StripeEvent.id).where(StripeEvent.id == event_id))
    ) is not None


async def get_subscription_by_stripe_id(
    db: AsyncSession, *, stripe_subscription_id: str
) -> Subscription | None:
    """Fallback lookup for a ``customer.subscription.*`` webhook whose payload
    metadata is missing ``user_id`` (PAY-001) — e.g. a subscription created by
    hand in the Dashboard, or an older event shape that never got our
    ``subscription_data.metadata`` stamp. Same read-only / no-commit /
    ``populate_existing`` contract as :func:`get_subscription`.
    """
    return await db.scalar(
        select(Subscription)
        .where(Subscription.stripe_subscription_id == stripe_subscription_id)
        .execution_options(populate_existing=True)
    )


async def get_subscription(db: AsyncSession, *, user_id: str) -> Subscription | None:
    """The user's ``subscriptions`` row, or None if they've never had one
    (PAY-001) — needed by the billing-portal endpoint. Read-only, same
    unguarded/no-commit contract as the other read helpers in this file
    (e.g. ``load_user_level``).

    ``populate_existing=True``: ``upsert_subscription`` writes via a Core
    ``ON CONFLICT`` statement, which does not refresh an already
    identity-mapped ORM instance. A caller that upserts then reads back in
    the same session (a natural webhook-handler shape) would otherwise see
    the pre-write attributes on this row; this option forces a fresh read
    from the row regardless of what's cached.
    """
    return await db.scalar(
        select(Subscription)
        .where(Subscription.user_id == user_id)
        .execution_options(populate_existing=True)
    )


# Tier values the app understands. Same content-as-data choice as
# ``STREAK_MODES``: the column itself carries no DB check constraint, so
# this set is the only thing standing between a typo and a silently broken
# tier read.
_VALID_TIERS = frozenset({"free", "basic", "premium"})


async def set_user_tier(db: AsyncSession, *, user_id: str, tier: str) -> None:
    """Write ``users.tier`` (PAY-001), called from the Stripe webhook
    handler once a subscription's tier is known. Validated against
    ``_VALID_TIERS`` in code before touching the row."""
    _assert_test_user(user_id)
    if tier not in _VALID_TIERS:
        raise ValueError(f"Unknown tier: {tier!r}")
    try:
        await db.execute(update(User).where(User.id == user_id).values(tier=tier))
        await db.commit()
    except SQLAlchemyError:
        await db.rollback()
        raise


# How many of the learner's own slips each ledger row keeps (ring buffer).
_MAX_LEDGER_EXAMPLES = 5


async def record_grammar_error(
    db: AsyncSession,
    *,
    user_id: str,
    pattern_id: str,
    sentence: str,
    corrected: str | None,
    note: str | None,
    source: str,
    session_id: str | None = None,
) -> None:
    """Upsert one classified slip into the grammar-error ledger (GRAM-001).

    One row per (user, pattern): a new pattern inserts with the column
    defaults (occurrences=1, status='open'); a recurrence bumps
    ``occurrences``, resets the retire ``streak``, REOPENS a retired pattern,
    and appends the learner's own sentence to the ``examples`` ring buffer
    (most recent last, capped at ``_MAX_LEDGER_EXAMPLES``).

    Same contract as the session-row ops: re-raises on ``SQLAlchemyError``,
    the caller owns the non-fatal wrapping — a ledger outage must never
    break the practice attempt it rides on.

    Read-modify-write, not ON CONFLICT: the ring-buffer append doesn't
    express cleanly in SQL, and one user's attempts are sequential — there
    is no concurrent writer for a given (user, pattern) in practice.
    """
    _assert_test_user(user_id)
    now = datetime.now()
    example: dict = {
        "sentence": sentence,
        "corrected": corrected,
        "note": note,
        "source": source,
        "at": now.isoformat(timespec="seconds"),
    }
    if session_id:
        example["session_id"] = session_id
    try:
        # SELECT ... FOR UPDATE: locks the row for this transaction so a
        # concurrent writer to the same (user_id, pattern_id) re-reads after
        # this commits, instead of racing on stale attributes (lost update).
        row = await db.get(UserError, (user_id, pattern_id), with_for_update=True)
        if row is None:
            db.add(
                UserError(
                    user_id=user_id,
                    pattern_id=pattern_id,
                    first_seen=now,
                    last_seen=now,
                    last_source=source,
                    last_session_id=session_id,
                    examples=[example],
                )
            )
        else:
            row.status = "open"
            row.streak = 0
            row.occurrences += 1
            row.last_seen = now
            row.last_source = source
            row.last_session_id = session_id
            row.examples = (row.examples or [])[-(_MAX_LEDGER_EXAMPLES - 1):] + [example]
        await db.commit()
    except SQLAlchemyError:
        await db.rollback()
        raise


async def record_drill_attempt(
    db: AsyncSession,
    *,
    user_id: str,
    exercise: str,
    item_ref: str | None = None,
    pattern_id: str | None = None,
    correct: bool | None = None,
    word_ok: bool | None = None,
    modality: str,
    session_id: str | None = None,
) -> None:
    """Append one row to the drill-attempt event log (DATA-004).

    Plain INSERT + commit, mirroring :func:`record_grammar_error`'s shape and
    contract: re-raises on ``SQLAlchemyError``, the caller owns the non-fatal
    wrapping — an attempt-log outage must never break the practice attempt it
    rides on. Unlike the ledger, there's no read-modify-write here: every
    attempt is its own row, never merged into an existing one, so there's no
    row to lock.

    ``correct=None`` is a first-class value, not a missing one — Szenario-
    Sparring's structure judge never emits a binary pass/fail, so its rows
    record NULL and still count toward attempt totals, just not accuracy.
    """
    _assert_test_user(user_id)
    try:
        db.add(
            DrillAttempt(
                user_id=user_id,
                exercise=exercise,
                item_ref=item_ref,
                pattern_id=pattern_id,
                correct=correct,
                word_ok=word_ok,
                modality=modality,
                session_id=session_id,
            )
        )
        await db.commit()
    except SQLAlchemyError:
        await db.rollback()
        raise
    # GAME-001 v2: a raw attempt no longer credits the day by itself — that
    # was v1's rule (any graded attempt = practiced day). Credit now comes
    # from complete_daily_mode() at each mode's own completion point (the
    # frontend end-panel POST for satz/flow, the disconnect path for tandem,
    # the second attempt for briefkasten), which requires 3 of the 4 modes
    # before the streak day is actually earned.


async def credit_streak_day(db: AsyncSession, *, user_id: str) -> None:
    """Credit today's UTC calendar day toward the daily streak (GAME-001).

    A no-op once today is already credited (the common case — most attempts
    in a day arrive after the first). Otherwise: a consecutive day (gap=1)
    extends the streak; exactly one missed day (gap=2) is forgiven by the
    automatic weekly grace — at most once per rolling 7 days — else the
    streak resets to 1; any bigger gap, or no prior credit at all, also
    resets to 1. ``longest_streak`` is a permanent PR and never decreases.
    Same contract as the other ledger ops: re-raises on ``SQLAlchemyError``,
    caller owns the non-fatal wrapping.
    """
    _assert_test_user(user_id)
    today = datetime.now(timezone.utc).date()
    try:
        user = await db.get(User, user_id)
        if user is None:
            return
        last = user.last_streak_day
        if last == today:
            return  # already credited today
        if last is None:
            new = 1
        else:
            gap = (today - last).days
            if gap == 1:
                new = user.current_streak + 1
            elif gap == 2:
                grace_available = (
                    user.streak_grace_used_on is None
                    or (today - user.streak_grace_used_on).days >= 7
                )
                if grace_available:
                    new = user.current_streak + 1
                    user.streak_grace_used_on = today - timedelta(days=1)
                else:
                    new = 1
            else:
                new = 1
        user.current_streak = new
        user.longest_streak = max(user.longest_streak, new)
        user.last_streak_day = today
        await db.commit()
    except SQLAlchemyError:
        await db.rollback()
        raise


# GAME-001 v2: the four learner-facing practice modes on /practice, and how
# many of them earn a streak day. Order here is the display order the UI
# renders "modes done today" pills in — load_modes_today sorts to match, so
# the frontend never has to re-sort an arbitrary DB return order.
STREAK_MODES = ("satz", "flow", "tandem", "briefkasten")
_STREAK_MODES_REQUIRED = 3


async def complete_daily_mode(db: AsyncSession, *, user_id: str, mode: str) -> None:
    """Record that ``mode`` was completed today, and credit the streak day
    once enough modes have been (GAME-001 v2).

    This is the "earned" half of the new rule: GAME-001 v1 let any single
    graded attempt touch the day via ``credit_streak_day`` directly. Now a
    day is only *earned* once the learner has completed
    ``_STREAK_MODES_REQUIRED`` (3) of the 4 ``STREAK_MODES`` — so this
    function first logs today's completion of ``mode``, then re-counts how
    many distinct modes today has, and only calls ``credit_streak_day`` once
    that count clears the bar. Below the bar it's a pure ledger write with
    no visible effect yet; the day quietly fills in as more modes land.

    Idempotent: the INSERT is ``ON CONFLICT DO NOTHING`` on the
    ``(user_id, day, mode)`` primary key, so completing the same mode twice
    in a day is a no-op the second time — and even if the count crosses the
    bar again on a later call, ``credit_streak_day`` itself already returns
    early once today is credited. Calling this twice for the same mode/day
    is safe and cheap.

    Same contract as the other ledger ops: re-raises on ``SQLAlchemyError``,
    caller owns the non-fatal wrapping.
    """
    _assert_test_user(user_id)
    if mode not in STREAK_MODES:
        raise ValueError(f"Unknown streak mode: {mode!r}")
    today = datetime.now(timezone.utc).date()
    try:
        await db.execute(
            pg_insert(DailyModeCompletion)
            .values(user_id=user_id, day=today, mode=mode)
            .on_conflict_do_nothing(
                index_elements=["user_id", "day", "mode"]
            )
        )
        await db.commit()
    except SQLAlchemyError:
        await db.rollback()
        raise
    distinct_modes = await db.scalar(
        select(func.count(func.distinct(DailyModeCompletion.mode))).where(
            DailyModeCompletion.user_id == user_id,
            DailyModeCompletion.day == today,
        )
    )
    if (distinct_modes or 0) >= _STREAK_MODES_REQUIRED:
        await credit_streak_day(db, user_id=user_id)


async def load_modes_today(db: AsyncSession, *, user_id: str) -> list[str]:
    """Modes completed today, in ``STREAK_MODES`` order (GAME-001 v2).

    Fixed display order rather than alphabetical or insertion order, so the
    "3 of 4 done today" UI never reshuffles its pills between renders.
    """
    today = datetime.now(timezone.utc).date()
    done = set(
        (
            await db.execute(
                select(DailyModeCompletion.mode).where(
                    DailyModeCompletion.user_id == user_id,
                    DailyModeCompletion.day == today,
                )
            )
        ).scalars().all()
    )
    return [mode for mode in STREAK_MODES if mode in done]


async def has_attempt_today(db: AsyncSession, *, user_id: str, mode: str) -> bool:
    """Anti-spoof check for ``POST /streak/mode`` (GAME-001 v2): has this
    user actually logged a matching ``drill_attempts`` row today, so the
    client's "I finished" ping has real work behind it?

    Only ``satz`` and ``flow`` are supported — ``tandem`` and
    ``briefkasten`` are credited server-side from their own completion
    paths (conversation disconnect, second letter attempt) and never go
    through this check at all.

    The two modes share the same underlying exercises but are told apart by
    ``session_id`` prefix, not by a dedicated column: every frontend surface
    mints a distinctly-prefixed session id, and ``Flow.tsx`` deliberately
    overrides VocabTrainer's own id with its own ``flow-`` prefixed one (see
    the FLOW-001 comment there) before handing a Satzschmiede/Verbformen
    round to the Flow. So:

    - ``satz``: ``exercise IN ('satz', 'verbformen')`` AND the session id is
      either absent or does NOT start with ``flow-`` — a standalone
      Satzschmiede/Verbformen sitting, not one embedded in a Flow round.
    - ``flow``: ``session_id LIKE 'flow-%'`` — any exercise, since a Flow
      round mixes drill types under one session id.
    """
    if mode not in ("satz", "flow"):
        raise ValueError(f"has_attempt_today only supports satz/flow, got {mode!r}")
    today_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    conditions = [
        DrillAttempt.user_id == user_id,
        DrillAttempt.created_at >= today_start,
    ]
    if mode == "satz":
        conditions.append(DrillAttempt.exercise.in_(("satz", "verbformen")))
        conditions.append(
            or_(
                DrillAttempt.session_id.is_(None),
                DrillAttempt.session_id.notlike("flow-%"),
            )
        )
    else:  # flow
        conditions.append(DrillAttempt.session_id.like("flow-%"))
    count = await db.scalar(
        select(func.count()).select_from(DrillAttempt).where(*conditions)
    )
    return bool(count)


# Consecutive correct spontaneous productions in tandem sessions before a
# pattern retires (GRAM-001: "retire on streak >= 2"). One correct use is
# encouraging; two is acquisition evidence.
_RETIRE_STREAK = 2


async def credit_pattern_success(
    db: AsyncSession,
    *,
    user_id: str,
    pattern_id: str,
    session_id: str | None = None,
    source: str = "tandem",
) -> str | None:
    """Credit a correct target-pattern production (tandem Phase 4; GRAM-002
    grammar exercises credit here too, each with its own ``source``).

    The success counterpart to :func:`record_grammar_error`: where a slip resets
    the streak and reopens the pattern, a clean spontaneous production bumps the
    retire ``streak`` and retires the pattern once it reaches ``_RETIRE_STREAK``.
    Only ``streak`` / ``status`` / ``last_seen`` move — ``occurrences`` (lifetime
    error count) and the ``examples`` ring buffer are untouched, since nothing
    went wrong.

    Returns the resulting ``status`` (``"open"`` | ``"retired"``) so the caller
    can flag a fresh retirement for the debrief modal, or ``None`` if the row is
    gone (the pattern was removed between connect and debrief). Same contract as
    the sibling ledger ops: re-raises on ``SQLAlchemyError``, caller owns the
    non-fatal wrapping.
    """
    _assert_test_user(user_id)
    try:
        # SELECT ... FOR UPDATE: locks the row for this transaction so a
        # concurrent writer to the same (user_id, pattern_id) re-reads after
        # this commits, instead of racing on stale attributes (lost update).
        row = await db.get(UserError, (user_id, pattern_id), with_for_update=True)
        if row is None:
            return None
        row.streak += 1
        row.last_seen = datetime.now()
        row.last_source = source
        if session_id:
            row.last_session_id = session_id
        if row.streak >= _RETIRE_STREAK:
            row.status = "retired"
        await db.commit()
        return row.status
    except SQLAlchemyError:
        await db.rollback()
        raise


# How many of the learner's own recent slips each focus pattern carries into
# the tandem prompt (from the tail of the ledger ring buffer).
_GRAMMAR_FOCUS_EXAMPLES = 2


async def load_grammar_focus(
    db: AsyncSession, *, user_id: str, limit: int = 3
) -> list[dict]:
    """Top open ledger patterns for the tandem grammar-focus layer (TANDEM-001)
    and bauteil/verbindungen's hot-round weighting (GRAM-002).

    Returns up to ``limit`` patterns ranked by ``(seen in the last 7 days DESC,
    occurrences DESC, last_seen DESC)`` — a pattern that slipped THIS WEEK
    outranks one with a bigger lifetime tally but no recent recurrence (DATA-004).
    The Tandem partner and the drill hot-rounds exist to chase *current*
    weaknesses, not to keep re-litigating something the learner fixed months
    ago and simply hasn't re-triggered since — occurrences/last_seen still
    break ties within each recency bucket, so a stale-but-massive pattern isn't
    erased, just deprioritized behind anything live.

    Each result is enriched from the taxonomy with ``label`` / ``description``
    / ``elicit`` and carries the learner's own most-recent examples
    ``[{sentence, corrected}]``. A pattern whose slug is no longer in the
    taxonomy (a removed catalog entry) is skipped.

    Read-only: no commit, no rollback — callers (``pipeline/factory.py``,
    ``bauteil``/``verbindungen`` routes) own the non-fatal wrapping, so an
    outage yields a less-personalised chat/round, never a failed connect.
    """
    # Naive, matching UserError.last_seen (plain TIMESTAMP, written via
    # ``datetime.now()`` in record_grammar_error/credit_pattern_success) — a
    # tz-aware cutoff here would raise at query time in asyncpg.
    recent_cutoff = datetime.now() - timedelta(days=7)
    recent_first = (UserError.last_seen >= recent_cutoff).desc()
    rows = (
        await db.execute(
            select(UserError.pattern_id, UserError.examples)
            .where(UserError.user_id == user_id, UserError.status == "open")
            .order_by(recent_first, UserError.occurrences.desc(), UserError.last_seen.desc())
            .limit(limit)
        )
    ).all()
    catalog = load_taxonomy()
    focus: list[dict] = []
    for pattern_id, examples in rows:
        pattern = catalog.get(pattern_id)
        if pattern is None:
            continue
        recent = [
            {"sentence": e.get("sentence"), "corrected": e.get("corrected")}
            for e in (examples or [])[-_GRAMMAR_FOCUS_EXAMPLES:]
        ]
        focus.append(
            {
                "pattern_id": pattern_id,
                "label": pattern["label"],
                "description": pattern["description"],
                "elicit": pattern["elicit"],
                "examples": recent,
            }
        )
    return focus


async def load_tandem_notes(
    db: AsyncSession, *, user_id: str, lesson_id: str = "tandem", limit: int = 5
) -> list[str]:
    """Recent tandem session notes for the long-term memory layer (TANDEM-001).

    Reads the ``session_note`` string the Phase-4 debrief writes into
    ``activity_session.error_eval`` on past tandem sessions, most recent first.
    Filtered by ``lesson_id`` (TAND-008): each partner remembers only their OWN
    sessions — Paul never knows what the learner told Lena, and vice versa.
    Returns ``[]`` until the debrief starts producing notes — Phase 3 wires the
    reader, Phase 4 fills it. Same read-only, caller-wrapped contract as
    ``load_grammar_focus``.
    """
    rows = (
        await db.execute(
            select(ActivitySession.error_eval)
            .where(
                ActivitySession.user_id == user_id,
                ActivitySession.lesson_id == lesson_id,
                ActivitySession.error_eval.isnot(None),
            )
            .order_by(ActivitySession.started_at.desc())
            .limit(limit)
        )
    ).scalars().all()
    notes: list[str] = []
    for error_eval in rows:
        note = error_eval.get("session_note") if isinstance(error_eval, dict) else None
        if note:
            notes.append(note)
    return notes


async def count_active_days(
    db: AsyncSession,
    *,
    user_id: str,
    drill_start: datetime,
    session_start: datetime,
) -> int:
    """Distinct UTC days with ANY practice since the period start — drill
    attempts or conversation sessions (REC-001's ≥3-active-days gate).

    Two starts because the columns disagree on tz-awareness:
    ``drill_attempts.created_at`` is TIMESTAMPTZ (compare aware),
    ``activity_session.started_at`` is naive-UTC TIMESTAMP (compare naive).
    """
    drill_days = (
        await db.execute(
            select(func.date(DrillAttempt.created_at))
            .where(
                DrillAttempt.user_id == user_id,
                DrillAttempt.created_at >= drill_start,
            )
            .distinct()
        )
    ).scalars().all()
    session_days = (
        await db.execute(
            select(func.date(ActivitySession.started_at))
            .where(
                ActivitySession.user_id == user_id,
                ActivitySession.started_at >= session_start,
            )
            .distinct()
        )
    ).scalars().all()
    return len(set(drill_days) | set(session_days))


async def satz_word_recall(
    db: AsyncSession, *, user_id: str, start: datetime
) -> tuple[int, float]:
    """(graded attempts, word-recall rate) over satz attempts since ``start``
    — the same COALESCE(word_ok, correct) metric the dosing throttle uses,
    so REC-001 and the throttle never disagree about what "weak recall" is.
    Rate is 0.0 when there are no attempts; gate on the count."""
    rows = (
        await db.execute(
            select(func.coalesce(DrillAttempt.word_ok, DrillAttempt.correct)).where(
                DrillAttempt.user_id == user_id,
                DrillAttempt.exercise == "satz",
                DrillAttempt.correct.is_not(None),
                DrillAttempt.created_at >= start,
            )
        )
    ).scalars().all()
    if not rows:
        return 0, 0.0
    return len(rows), sum(1 for r in rows if r) / len(rows)


async def count_sessions_since(
    db: AsyncSession, *, user_id: str, lesson_ids: Sequence[str], start: datetime
) -> int:
    """Conversation sessions of the given lessons since ``start`` (naive-UTC).

    Takes a collection (TAND-008): REC-001's "had a real conversation this
    week?" check counts a chat with ANY tandem partner.
    """
    return (
        await db.scalar(
            select(func.count())
            .select_from(ActivitySession)
            .where(
                ActivitySession.user_id == user_id,
                ActivitySession.lesson_id.in_(list(lesson_ids)),
                ActivitySession.started_at >= start,
            )
        )
    ) or 0


async def load_recent_attempt_item_ids(
    db: AsyncSession, *, user_id: str, exercise: str, since: datetime
) -> set[str]:
    """Distinct ``item_ref`` values this user has attempted in ``exercise``
    since ``since`` — the raw per-attempt log, not deduped by outcome.

    Built for server-side "seen" cooldowns (VARY-002): a caller unions this
    into whatever seen-id set it already has (e.g. a client-echoed list) so a
    recently-attempted item doesn't resurface, independent of what the client
    remembers. ``since`` must be tz-aware — ``drill_attempts.created_at`` is
    TIMESTAMPTZ (unlike ``user_errors``/``activity_session``'s naive
    columns), so a naive cutoff here would raise at query time in asyncpg.
    """
    rows = (
        await db.execute(
            select(DrillAttempt.item_ref)
            .where(
                DrillAttempt.user_id == user_id,
                DrillAttempt.exercise == exercise,
                DrillAttempt.item_ref.isnot(None),
                DrillAttempt.created_at >= since,
            )
            .distinct()
        )
    ).scalars().all()
    return set(rows)


def _as_utc_iso(dt: datetime) -> str:
    # activity_session timestamps are stored naive-UTC (TIMESTAMP without
    # timezone); stamp the offset so the frontend's Date parsing can't
    # reinterpret them as local time.
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).isoformat()


async def load_recent_sessions(
    db: AsyncSession, *, user_id: str, limit: int = 10
) -> list[dict]:
    """Recent FINISHED conversation sessions for the Development page's
    history block (BUG-010 / UI-005 minimal).

    Only rows whose finalize ran (``ended_at`` set) — an unfinished row has
    nothing readable yet. Tandem rows carry the full debrief dict so the
    learner can read notes they never saw in-session (user-ended tandem
    skips the modal entirely under BUG-010); other lessons' ``error_eval``
    holds the silent grammar harvest, which is not display content → null.
    """
    rows = (
        await db.execute(
            select(
                ActivitySession.id,
                ActivitySession.lesson_id,
                ActivitySession.started_at,
                ActivitySession.ended_at,
                ActivitySession.ended_by,
                ActivitySession.error_eval,
            )
            .where(
                ActivitySession.user_id == user_id,
                ActivitySession.ended_at.isnot(None),
            )
            .order_by(ActivitySession.started_at.desc())
            .limit(limit)
        )
    ).all()
    sessions: list[dict] = []
    for row in rows:
        debrief = (
            row.error_eval
            if isinstance(row.error_eval, dict)
            and row.error_eval.get("kind") == "tandem_debrief"
            else None
        )
        sessions.append(
            {
                "id": row.id.hex,
                "lessonId": row.lesson_id,
                "startedAt": _as_utc_iso(row.started_at),
                "endedAt": _as_utc_iso(row.ended_at),
                "endedBy": row.ended_by,
                "debrief": debrief,
            }
        )
    return sessions


# Cards at or above this interval rung are "mature" — hearing them again in
# conversation is nearly worthless, so the tandem vocab layer skips them.
# 16 is the 4th rung of the Satzschmiede ladder (satz/scheduler.py STEPS).
_VOCAB_MATURE_DAYS = 16


def _vocab_word_display(card: VocabCard) -> str:
    """The word as Lena should meet it: article for nouns, ``sich`` for
    reflexive verbs, the spoken past form for tense siblings."""
    if card.tense and card.tense_form:
        return card.tense_form
    if card.article:
        return f"{card.article} {card.target}"
    if card.reflexive:
        return f"sich {card.target}"
    return card.target


async def load_vocab_words(
    db: AsyncSession, *, user_id: str, limit: int = 10
) -> list[dict]:
    """Random sample of the learner's active-window vocab for the tandem
    vocab layer (TANDEM-001 extension): words worth hearing in conversation.

    The pool is every deck card the scheduler still considers in play — due
    now, or practiced but below the mature rung (``interval_days <
    _VOCAB_MATURE_DAYS``). ``ORDER BY random()`` inside that pool so each
    session surfaces different words. If the pool is thinner than ``limit``,
    tops up with untouched cards (``due_at IS NULL`` — priming: the learner
    hears a word before ever drilling it). Mature cards never appear.

    Verb tense siblings share a ``target``; the sample is deduped on it so
    Lena never gets the same verb twice (the result may then run short of
    ``limit`` — fine, it's a target not a quota).

    Each result is ``{"word": display, "gloss": gloss}`` — display carries
    the article / ``sich`` / spoken-past form so the prompt teaches the
    taught sense. Same read-only, caller-wrapped contract as
    ``load_grammar_focus``.
    """
    now = datetime.now()
    active = UserCard.due_at.isnot(None) & (
        (UserCard.due_at <= now)
        | (func.coalesce(UserCard.interval_days, 0) < _VOCAB_MATURE_DAYS)
    )
    rows = (
        (
            await db.execute(
                select(VocabCard)
                .join(UserCard, UserCard.card_id == VocabCard.id)
                .where(UserCard.user_id == user_id, active)
                .order_by(func.random())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    if len(rows) < limit:
        picked_ids = [c.id for c in rows]
        extra = (
            (
                await db.execute(
                    select(VocabCard)
                    .join(UserCard, UserCard.card_id == VocabCard.id)
                    .where(
                        UserCard.user_id == user_id,
                        UserCard.due_at.is_(None),
                        VocabCard.id.notin_(picked_ids),
                    )
                    .order_by(func.random())
                    .limit(limit - len(rows))
                )
            )
            .scalars()
            .all()
        )
        rows = [*rows, *extra]

    words: list[dict] = []
    seen_targets: set[str] = set()
    for card in rows:
        key = card.target.lower()
        if key in seen_targets:
            continue
        seen_targets.add(key)
        words.append({"word": _vocab_word_display(card), "gloss": card.gloss})
    return words


# ── Stats (DATA-004, GET /me/stats) ──────────────────────────────────────
# Read-only helpers over ``drill_attempts`` (the event log) and
# ``user_errors`` (the ledger). All SQL for the stats endpoint lives here;
# ``stats/routes.py`` only picks time windows and assembles the response.


async def load_streak(db: AsyncSession, *, user_id: str) -> dict:
    """The streak the stats endpoint should display (GAME-001).

    ``current`` mirrors the stored cache only while the chain is still
    provably alive: last credited today, yesterday, or exactly 2 days ago
    with the weekly grace still available (the same check
    :func:`credit_streak_day` uses at write time) — otherwise the chain has
    silently broken since the last write and ``current`` reads 0 without
    touching the stored row. ``longest`` always reflects the stored PR.
    Missing user row (shouldn't happen post-login) → all-zero dict.

    ``practicedToday`` is still exactly ``last_streak_day == today`` — the
    code hasn't changed since v1, but what it *means* has: under GAME-001 v2
    ``last_streak_day`` is only ever set by :func:`credit_streak_day`, which
    :func:`complete_daily_mode` now calls only once 3 of the 4
    ``STREAK_MODES`` are done. So a true ``practicedToday`` no longer means
    "did one graded thing today" — it means the day is *earned*.

    ``modesToday`` / ``modesRequired`` are the progress readout toward that:
    which of the 4 modes already have a row for today, and how many are
    needed. The UI uses these to show "2 of 3" before the day flips.
    """
    today = datetime.now(timezone.utc).date()
    modes_today = await load_modes_today(db, user_id=user_id)
    user = await db.get(User, user_id)
    if user is None:
        return {
            "current": 0,
            "longest": 0,
            "practicedToday": False,
            "modesToday": modes_today,
            "modesRequired": _STREAK_MODES_REQUIRED,
        }
    last = user.last_streak_day
    if last is None:
        alive = False
    else:
        gap = (today - last).days
        if gap in (0, 1):
            alive = True
        elif gap == 2:
            alive = (
                user.streak_grace_used_on is None
                or (today - user.streak_grace_used_on).days >= 7
            )
        else:
            alive = False
    return {
        "current": user.current_streak if alive else 0,
        "longest": user.longest_streak,
        "practicedToday": last == today,
        "modesToday": modes_today,
        "modesRequired": _STREAK_MODES_REQUIRED,
    }


async def load_period_summary(
    db: AsyncSession, *, user_id: str, start: datetime, end: datetime | None = None
) -> dict:
    """Attempt counts for one time window, grouped by exercise.

    ``accuracy`` is computed only over rows where ``correct IS NOT NULL`` —
    Szenario's coach rows (always NULL) count toward ``attempts`` but never
    toward ``correct``/``accuracy``; an exercise with only NULL-correct rows
    in the window gets ``accuracy: None`` rather than a fabricated 0 or 1.
    ``attemptsTotal`` is the sum across exercises, so a caller that only
    needs the total (``today``) can drop the ``exercises`` list.
    """
    conditions = [DrillAttempt.user_id == user_id, DrillAttempt.created_at >= start]
    if end is not None:
        conditions.append(DrillAttempt.created_at < end)
    rows = (
        await db.execute(
            select(
                DrillAttempt.exercise,
                func.count().label("attempts"),
                func.count().filter(DrillAttempt.correct.isnot(None)).label("graded"),
                func.count().filter(DrillAttempt.correct.is_(True)).label("correct"),
            )
            .where(*conditions)
            .group_by(DrillAttempt.exercise)
        )
    ).all()
    exercises: list[dict] = []
    total = 0
    for exercise, attempts, graded, correct in rows:
        total += attempts
        exercises.append(
            {
                "exercise": exercise,
                "attempts": attempts,
                "correct": correct,
                "accuracy": (correct / graded) if graded else None,
            }
        )
    return {"attemptsTotal": total, "exercises": exercises}


async def load_attempt_series(
    db: AsyncSession, *, user_id: str, start: datetime
) -> list[dict]:
    """Per-day practice series for the Development chart (DATA-005).

    ``attempts`` counts every row (coach rows included); ``mistakes`` counts
    graded misses; ``firstTryCorrect`` counts items whose FIRST graded
    attempt of that UTC day was correct — right without a warm-up. Item
    identity is (exercise, item_ref); rows with no item_ref each count as
    their own item. Days with no practice don't appear — the frontend
    fills the gaps with zeros.
    """
    # created_at is TIMESTAMPTZ: date_trunc on it follows the session
    # timezone, so convert to UTC first — the /me/stats contract is UTC days
    # regardless of where the Postgres server thinks it lives.
    day = func.date_trunc("day", func.timezone("UTC", DrillAttempt.created_at)).label("day")
    rows = (
        await db.execute(
            select(
                day,
                func.count().label("attempts"),
                func.count()
                .filter(DrillAttempt.correct.is_(False))
                .label("mistakes"),
            )
            .where(DrillAttempt.user_id == user_id, DrillAttempt.created_at >= start)
            .group_by(day)
            .order_by(day)
        )
    ).all()

    rn = (
        func.row_number()
        .over(
            partition_by=[
                DrillAttempt.exercise,
                func.coalesce(DrillAttempt.item_ref, cast(DrillAttempt.id, Text)),
                func.date_trunc("day", func.timezone("UTC", DrillAttempt.created_at)),
            ],
            order_by=DrillAttempt.created_at,
        )
        .label("rn")
    )
    graded = (
        select(
            func.date_trunc("day", func.timezone("UTC", DrillAttempt.created_at)).label("day"),
            DrillAttempt.correct.label("correct"),
            rn,
        )
        .where(
            DrillAttempt.user_id == user_id,
            DrillAttempt.created_at >= start,
            DrillAttempt.correct.isnot(None),
        )
        .subquery()
    )
    ft_rows = (
        await db.execute(
            select(graded.c.day, func.count().label("ft"))
            .where(graded.c.rn == 1, graded.c.correct.is_(True))
            .group_by(graded.c.day)
        )
    ).all()
    first_try = {d: ft for d, ft in ft_rows}
    return [
        {
            "date": d.date().isoformat(),
            "attempts": attempts,
            "mistakes": mistakes,
            "firstTryCorrect": first_try.get(d, 0),
        }
        for d, attempts, mistakes in rows
    ]


async def _ledger_examples_by_pattern(
    db: AsyncSession, *, user_id: str, pattern_ids: list[str]
) -> dict[str, dict]:
    """Batch lookup of each pattern's most recent example from the
    ``user_errors`` ring buffer (most recent = last in the list)."""
    if not pattern_ids:
        return {}
    rows = (
        await db.execute(
            select(UserError.pattern_id, UserError.examples).where(
                UserError.user_id == user_id, UserError.pattern_id.in_(pattern_ids)
            )
        )
    ).all()
    out: dict[str, dict] = {}
    for pattern_id, examples in rows:
        if examples:
            last = examples[-1]
            out[pattern_id] = {
                "sentence": last.get("sentence"),
                "corrected": last.get("corrected"),
            }
    return out


async def load_top_errors(
    db: AsyncSession,
    *,
    user_id: str,
    start: datetime,
    end: datetime | None = None,
    limit: int = 5,
) -> list[dict]:
    """Most-frequent missed patterns in a time window, taxonomy-enriched.

    Only rows with ``correct = false`` and a non-null ``pattern_id`` count —
    a wrong answer with no classifiable pattern (e.g. a dodge) isn't a
    "top error". Same skip-if-retired-from-catalog guard as
    ``load_grammar_focus``: a slug no longer in the taxonomy is dropped
    rather than surfaced label-less.
    """
    conditions = [
        DrillAttempt.user_id == user_id,
        DrillAttempt.correct.is_(False),
        DrillAttempt.pattern_id.isnot(None),
        DrillAttempt.created_at >= start,
    ]
    if end is not None:
        conditions.append(DrillAttempt.created_at < end)
    rows = (
        await db.execute(
            select(DrillAttempt.pattern_id, func.count().label("count"))
            .where(*conditions)
            .group_by(DrillAttempt.pattern_id)
            .order_by(func.count().desc())
            .limit(limit)
        )
    ).all()
    catalog = load_taxonomy()
    pattern_ids = [pid for pid, _ in rows if pid in catalog]
    examples = await _ledger_examples_by_pattern(
        db, user_id=user_id, pattern_ids=pattern_ids
    )
    out: list[dict] = []
    for pattern_id, count in rows:
        pattern = catalog.get(pattern_id)
        if pattern is None:
            continue
        out.append(
            {
                "patternId": pattern_id,
                "label": pattern["label"],
                "count": count,
                "example": examples.get(pattern_id),
            }
        )
    return out


async def load_focus_with_recency(
    db: AsyncSession, *, user_id: str, limit: int = 3
) -> list[dict]:
    """The stats-page ``focus`` list: the same recency-weighted top patterns
    ``load_grammar_focus`` picks for the Tandem partner, plus two counters
    that function doesn't carry — ``count7d`` (this week's drill-attempt
    misses for the pattern) and ``lifetime`` (the ledger's running
    ``occurrences`` tally). Delegates ranking to ``load_grammar_focus`` so
    the two surfaces (Tandem prompt, stats page) can never drift apart.
    """
    focus = await load_grammar_focus(db, user_id=user_id, limit=limit)
    if not focus:
        return []
    pattern_ids = [f["pattern_id"] for f in focus]
    cutoff = datetime.now() - timedelta(days=7)
    count_rows = (
        await db.execute(
            select(DrillAttempt.pattern_id, func.count())
            .where(
                DrillAttempt.user_id == user_id,
                DrillAttempt.pattern_id.in_(pattern_ids),
                DrillAttempt.correct.is_(False),
                DrillAttempt.created_at >= cutoff,
            )
            .group_by(DrillAttempt.pattern_id)
        )
    ).all()
    count7d_by_pattern = {pid: c for pid, c in count_rows}
    occ_rows = (
        await db.execute(
            select(UserError.pattern_id, UserError.occurrences).where(
                UserError.user_id == user_id, UserError.pattern_id.in_(pattern_ids)
            )
        )
    ).all()
    lifetime_by_pattern = {pid: occ for pid, occ in occ_rows}
    return [
        {
            "patternId": f["pattern_id"],
            "label": f["label"],
            "description": f["description"],
            "count7d": count7d_by_pattern.get(f["pattern_id"], 0),
            "lifetime": lifetime_by_pattern.get(f["pattern_id"], 0),
        }
        for f in focus
    ]


async def load_user_level(db: AsyncSession, *, user_id: str) -> str | None:
    """The learner's CEFR bucket, or None when they haven't been asked.

    None is a real answer, not a failure: ``grammar.levels.select()`` serves
    a None-level learner everything, which is exactly the pre-LEVEL-001
    behaviour every existing account should keep until it opts in.
    """
    return await db.scalar(select(User.level).where(User.id == user_id))


async def set_user_level(db: AsyncSession, *, user_id: str, level: str | None) -> None:
    """Write the learner's CEFR bucket. ``None`` clears it (back to
    "serve everything"), which is what the UI's "not sure" escape does."""
    _assert_test_user(user_id)
    await db.execute(update(User).where(User.id == user_id).values(level=level))
    await db.commit()


async def load_weak_patterns(db: AsyncSession, *, user_id: str) -> set[str]:
    """Pattern ids the learner demonstrably still gets wrong.

    This is the FLOOR half of the serving rule (grammar/levels.py): a
    below-level pattern comes back only if it is in here. ``status ==
    "open"`` is what makes that work — a pattern the learner has since
    answered correctly is retired by ``credit_pattern_success`` and stops
    being served, which is the drill quietly getting out of their way.
    """
    rows = (
        await db.execute(
            select(UserError.pattern_id).where(
                UserError.user_id == user_id, UserError.status == "open"
            )
        )
    ).scalars().all()
    return set(rows)


async def load_retired_patterns(
    db: AsyncSession, *, user_id: str, limit: int = 10
) -> list[dict]:
    """Retired ledger patterns, most recently retired first, cap ``limit``.

    Same taxonomy-membership guard as the other stats helpers — a slug
    dropped from the catalog since retirement is skipped rather than shown
    without a label.
    """
    rows = (
        await db.execute(
            select(UserError.pattern_id)
            .where(UserError.user_id == user_id, UserError.status == "retired")
            .order_by(UserError.last_seen.desc())
            .limit(limit)
        )
    ).scalars().all()
    catalog = load_taxonomy()
    out: list[dict] = []
    for pattern_id in rows:
        pattern = catalog.get(pattern_id)
        if pattern is None:
            continue
        out.append({"patternId": pattern_id, "label": pattern["label"]})
    return out
