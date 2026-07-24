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

from datetime import datetime, timedelta
from uuid import UUID

from loguru import logger
from sqlalchemy import Text, cast, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from grammar import load_taxonomy

from .orm import ActivitySession, DrillAttempt, User, UserCard, UserError, VocabCard


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
    try:
        db.add(
            DrillAttempt(
                user_id=user_id,
                exercise=exercise,
                item_ref=item_ref,
                pattern_id=pattern_id,
                correct=correct,
                modality=modality,
                session_id=session_id,
            )
        )
        await db.commit()
    except SQLAlchemyError:
        await db.rollback()
        raise


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
    db: AsyncSession, *, user_id: str, limit: int = 5
) -> list[str]:
    """Recent tandem session notes for the long-term memory layer (TANDEM-001).

    Reads the ``session_note`` string the Phase-4 debrief writes into
    ``activity_session.error_eval`` on past tandem sessions, most recent first.
    Returns ``[]`` until the debrief starts producing notes — Phase 3 wires the
    reader, Phase 4 fills it. Same read-only, caller-wrapped contract as
    ``load_grammar_focus``.
    """
    rows = (
        await db.execute(
            select(ActivitySession.error_eval)
            .where(
                ActivitySession.user_id == user_id,
                ActivitySession.lesson_id == "tandem",
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
