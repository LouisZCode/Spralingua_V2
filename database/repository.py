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

from datetime import datetime
from uuid import UUID

from loguru import logger
from sqlalchemy import func, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from .orm import ActivitySession, User, UserError


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
) -> str:
    """Insert or refresh a user from a verified Google sign-in (AUTH-001).

    Keyed on the Google ``sub`` (``user_id``). On a repeat sign-in we refresh the
    mutable profile fields (the user may have changed their Google name/avatar)
    and stamp ``last_login_at``. ``created_at`` keeps its first-insert value via
    the server default and is left untouched on update.

    ``role`` is deliberately NOT in the conflict update set — it's set out-of-band
    (SQL) and must survive re-logins — and we ``RETURNING`` it so the caller can
    embed it in the session JWT + sign-in response. New rows get the column
    default ("normal").
    """
    try:
        stmt = pg_insert(User).values(
            id=user_id,
            email=email,
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
        ).returning(User.role)
        result = await db.execute(stmt)
        role = result.scalar_one()
        await db.commit()
        return role
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
        row = await db.get(UserError, (user_id, pattern_id))
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
