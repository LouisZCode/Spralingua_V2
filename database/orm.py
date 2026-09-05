"""SQLAlchemy 2.x declarative models for the Spralingua DB (DATA-001).

Two tables this iteration:

- ``users`` — keyed on a plain ``TEXT`` id that is the identity-provider
  subject id: the Google ``sub`` for authenticated users (AUTH-001), or the
  literal ``"demo"`` sentinel that anchors anonymous front-page demo sessions.
  Carries the OAuth profile (``email`` / ``name`` / ``picture``) and login
  timestamps; all profile fields are nullable so the ``"demo"`` row can exist
  without one.
- ``activity_session`` — one row per WebSocket connect. The session id is
  the same ``uuid4().hex`` minted in ``pipeline/factory.py`` at line 81 and
  used as the Langfuse session id, so trace ↔ DB correlation is implicit.
  The two evaluator results (goal eval and pronunciation eval) and a frozen
  snapshot of the lesson YAML at session start all live in JSONB columns.

Indexes match the two access patterns we already know we'll need:
- ``(user_id, started_at DESC)`` — "show my last N sessions"
- ``(user_id, lesson_id)`` — "has this user attempted/passed this lesson?"
"""

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    String,
    Text,
    TIMESTAMP,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Shared declarative base. Alembic reads ``Base.metadata`` in ``env.py``."""
    pass


class User(Base):
    __tablename__ = "users"

    # Identity-provider subject id: Google ``sub`` for authed users (AUTH-001),
    # or the ``"demo"`` sentinel for anonymous demo sessions.
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    # OAuth profile (AUTH-001). All nullable: the ``"demo"`` user carries none
    # of these, and Postgres treats multiple NULL emails as distinct, so the
    # unique-email constraint never collides on profile-less rows.
    email: Mapped[str | None] = mapped_column(Text, nullable=True)
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    picture: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP, nullable=False, server_default=text("now()")
    )
    last_login_at: Mapped[datetime | None] = mapped_column(TIMESTAMP, nullable=True)
    # Access tier: "normal" (default), "premium" (reserved), "developer" (unlocks
    # the internal dev tools in the UI). Set out-of-band via SQL; not reset on login.
    role: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'normal'")
    )
    # LEVEL-001: self-declared CEFR bucket — "A1" | "A2" | "B1" | "B2+", or
    # NULL for "not asked yet" (LEVEL-002 widened B1+ to B1/B2+; legacy "B1+"
    # rows are migrated to "B1" by 0023_level_buckets). Drives the round
    # serving rule (grammar/levels.py): the level caps what a learner is
    # served, the user_errors ledger decides which below-level patterns
    # still come back. NULL serves everything, so an account that never
    # answers the question keeps today's behaviour.
    level: Mapped[str | None] = mapped_column(Text, nullable=True)
    # GAME-001: denormalized streak cache, updated on write (repository.
    # credit_streak_day) — O(1) read and write, no hot-path scan. Days bucket
    # by UTC calendar day; for users west of UTC the day rolls over mid-evening
    # local time — known limitation pending a user timezone column.
    current_streak: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    longest_streak: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    last_streak_day: Mapped[date | None] = mapped_column(Date, nullable=True)
    # The one missed day the automatic weekly grace forgave, at most once per
    # rolling 7 days. Never purchasable (GAME-001: no repair economy).
    streak_grace_used_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    # PAY-001: billing tier — "free" (default) | "basic" | "premium".
    # Webhook-driven for paid tiers (database.repository.set_user_tier, called
    # from the Stripe webhook handler); every Google sign-up lands here via
    # the column default, no app-code write needed at signup time. Separate
    # from ``role`` above, which keeps its own dev-tools meaning (unlocking
    # internal tools in the UI) — a "developer" role user can still be on the
    # "free" billing tier and vice versa.
    tier: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'free'")
    )
    # PAY-002: coin system — two-bucket model.
    # ``timezone`` is the IANA name the frontend reports once via
    # ``PUT /coins/timezone`` (``Intl.DateTimeFormat().resolvedOptions().
    # timeZone``); NULL → UTC. ``allowance_day`` is the user-local "coin day"
    # the current daily bucket belongs to: ``(local_now - 5h).date()``; NULL
    # means never refreshed (every pre-migration row, new signups before their
    # first spend/read). ``allowance_remaining`` is today's remaining daily
    # coins (reset lazily — no cron); ``purchased_coins`` is the persistent
    # bucket: signup grant (100) + Stripe top-ups (500 each), never reset.
    timezone: Mapped[str | None] = mapped_column(Text, nullable=True)
    allowance_day: Mapped[date | None] = mapped_column(Date, nullable=True)
    allowance_remaining: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    purchased_coins: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )

    __table_args__ = (UniqueConstraint("email", name="uq_users_email"),)


class DailyModeCompletion(Base):
    """GAME-001 v2: per-mode day-earning ledger.

    One row per ``(user_id, day, mode)`` the learner actually completed —
    written by ``database.repository.complete_daily_mode`` from each mode's
    own completion point (frontend POST for satz/flow, server-side for
    tandem at disconnect and briefkasten at the second attempt). A day now
    only advances the ``users`` streak cache above once 3 of the 4 modes
    have a row for it; a single graded attempt is no longer enough (that
    was GAME-001 v1's rule, tightened here).

    ``mode`` is plain Text, not an enum type, deliberately — same
    content-as-data choice already made for ``UserError.pattern_id``: the
    four slugs (``satz``/``flow``/``tandem``/``briefkasten``) are validated
    in code (``database.repository.STREAK_MODES``), not by the schema.

    CASCADE on ``user_id`` like the other per-user learning-state tables
    (``user_cards``, ``user_errors``, ``drill_attempts``) — this is
    disposable derived state, not audit-of-record like ``activity_session``.
    """

    __tablename__ = "daily_mode_completions"

    user_id: Mapped[str] = mapped_column(
        Text, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    mode: Mapped[str] = mapped_column(Text, primary_key=True)

    # No extra index: the PK's leading columns (user_id, day) already serve
    # the "how many distinct modes did this user finish today" lookup that
    # complete_daily_mode runs on every write.


class ActivitySession(Base):
    __tablename__ = "activity_session"

    id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    lesson_id: Mapped[str] = mapped_column(Text, nullable=False)
    level: Mapped[str | None] = mapped_column(Text, nullable=True)
    situation: Mapped[str | None] = mapped_column(Text, nullable=True)
    voice: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(TIMESTAMP, nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(TIMESTAMP, nullable=True)
    # "user" | "agent" | "crash" | NULL (NULL = finalize never ran)
    ended_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    transcript: Mapped[str | None] = mapped_column(Text, nullable=True)
    audio_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Frozen copy of load_prompts(lesson_id) at session start, so future
    # history UI shows what the user actually saw (YAML may have changed since).
    lesson_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # EvaluationResult.model_dump() — NULL if the lesson has no goals.
    goal_eval: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # PronunciationResult.model_dump() — NULL if the lesson has no locale.
    pron_eval: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Grammar record; the shape depends on the mode. Drills store
    # ErrorExtraction.model_dump() (GRAM-001 Phase 2 — the silent harvest,
    # never shown in the drill modal); tandem sessions store the enriched
    # debrief dict (kind="tandem_debrief", Phase 4 — rendered by
    # TandemDebriefModal, session_note mined by load_tandem_notes). NULL when
    # neither ran (goal-less, non-tandem lessons). The durable cross-session
    # store is the user_errors ledger; this is the per-session record.
    error_eval: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Mirrors goal_eval["passed"] for fast indexed lookup. NULL if no eval ran.
    passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    __table_args__ = (
        Index(
            "ix_activity_session_user_started",
            "user_id",
            text("started_at DESC"),
        ),
        Index("ix_activity_session_user_lesson", "user_id", "lesson_id"),
    )


# ── Satzschmiede (SATZ-002) ──────────────────────────────────────────────
# Content/state split: ``cards`` is ONE shared canonical catalog (curated
# rows synced from satz/packs/*.yaml at startup, community rows added by the
# enricher later); ``user_cards`` is per-user state referencing it. Popularity
# = COUNT(DISTINCT user_id) per card_id — meaningful only because every pool
# points at the same canonical row.


class VocabCard(Base):
    __tablename__ = "cards"

    # Slug id from the pack YAML ("n-rechnung", "v-freuen"); community cards
    # get the same shape minted at insert time.
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    type: Mapped[str] = mapped_column(Text, nullable=False)  # noun|verb|phrase|adjective|preposition|adverb
    target: Mapped[str] = mapped_column(Text, nullable=False)
    article: Mapped[str | None] = mapped_column(Text, nullable=True)  # nouns
    # Verbs whose taught sense needs a reflexive pronoun — hidden on the clue,
    # required by the examiner (the "teach reflexivity by omission" rule).
    reflexive: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    gloss: Mapped[str] = mapped_column(Text, nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Verb tense siblings: NULL = present (the base card), "past" for the
    # spoken-past sibling forged alongside it — each its own card with its
    # own schedule, since past-tense recall is a separate skill.
    tense: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The natural SPOKEN past shown as the answer ("ist geflogen",
    # "dachte · hat gedacht", "war") — set exactly when tense is.
    tense_form: Mapped[str | None] = mapped_column(Text, nullable=True)
    example: Mapped[str | None] = mapped_column(Text, nullable=True)
    # SATZ-017: extra forged examples (list[str], ~3 at ranged difficulty)
    # so encounters rotate instead of re-reading `example` forever. NULL =
    # not forged yet (the deck-fetch backfill retries); written once per
    # card by satz/example_forge.py, shared across users like all card
    # content. Never assign Python None explicitly — the backfill filter
    # relies on SQL NULL (the CONT-002 none_as_null gotcha).
    examples: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    level: Mapped[str | None] = mapped_column(Text, nullable=True)  # CEFR hint
    # "curated" (from YAML, resynced on every boot) | "community" (user-added
    # via the enricher; never touched by the sync).
    source: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'curated'")
    )
    first_added_by: Mapped[str | None] = mapped_column(
        Text, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP, nullable=False, server_default=text("now()")
    )

    # Dedup seam for the add-a-word flow: one canonical row per (type, word,
    # tense) — a verb's present and past siblings share (type, target).
    __table_args__ = (
        Index(
            "uq_cards_type_target_lower",
            "type",
            text("lower(target)"),
            text("coalesce(tense, 'present')"),
            unique=True,
        ),
    )


class Pack(Base):
    __tablename__ = "packs"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)  # level|situation
    level: Mapped[str | None] = mapped_column(Text, nullable=True)  # display hint
    position: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )


class PackCard(Base):
    __tablename__ = "pack_cards"

    pack_id: Mapped[str] = mapped_column(
        Text, ForeignKey("packs.id", ondelete="CASCADE"), primary_key=True
    )
    card_id: Mapped[str] = mapped_column(
        Text, ForeignKey("cards.id", ondelete="CASCADE"), primary_key=True
    )
    # Card order within the pack (YAML order, rewritten on every sync).
    position: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )


class UserCard(Base):
    __tablename__ = "user_cards"

    # CASCADE (unlike activity_session's RESTRICT): pool rows are preference
    # state, not audit data — they should vanish with the user.
    user_id: Mapped[str] = mapped_column(
        Text, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    # RESTRICT: a canonical card that sits in someone's pool must not be deleted.
    card_id: Mapped[str] = mapped_column(
        Text, ForeignKey("cards.id", ondelete="RESTRICT"), primary_key=True
    )
    added_at: Mapped[datetime] = mapped_column(
        TIMESTAMP, nullable=False, server_default=text("now()")
    )
    # Which pack the card arrived from (NULL = added individually).
    source_pack: Mapped[str | None] = mapped_column(
        Text, ForeignKey("packs.id", ondelete="SET NULL"), nullable=True
    )
    # Scheduling state — columns exist from day one so the scheduler phase is
    # pure code, no second migration. NULL due_at = "new, never practiced".
    due_at: Mapped[datetime | None] = mapped_column(TIMESTAMP, nullable=True)
    interval_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reps: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    last_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Stamped at the card's FIRST graded attempt or reveal; drives the
    # 5-new-per-day allowance (NULL = still untouched).
    started_at: Mapped[datetime | None] = mapped_column(TIMESTAMP, nullable=True)
    # SATZ-013: gloss-path daily allowance — "gloss" when this link was made
    # via the hover/tap GLOSS popover's one-tap add, NULL for everything else
    # (manual add-word form, pack add, auto-forged verb-past sibling). The
    # only signal POST /satz/cards needs to count "gloss adds today" against
    # the hard cap of 3/day without touching the uncapped manual/pack paths.
    source: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Lifetime word-miss/reveal/gender-miss count — every call site that
    # already quarters interval_days also bumps this. Telemetry only: it used
    # to auto-bench a card past a leech threshold (SATZ P2), but that
    # behavior was removed (satz P3) — nothing reads this column's crossing
    # a threshold anymore.
    lapses: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    # Dead column (satz P3 removed leech benching and the "Schwere Wörter"
    # shelf; the 0020 migration cleared every existing bench). Left in place
    # rather than dropped — no code writes or reads it anymore, always NULL.
    benched_at: Mapped[datetime | None] = mapped_column(TIMESTAMP, nullable=True)

    # The deck query: "this user's cards that are due".
    __table_args__ = (Index("ix_user_cards_user_due", "user_id", "due_at"),)


# ── Grammatik-Tandem (GRAM-001) ──────────────────────────────────────────
# The error ledger: one row per (user, grammar pattern) — the ledger tracks
# PATTERNS, not individual slips. ``pattern_id`` is a slug from
# ``grammar/taxonomy.yaml``; deliberately not an FK — the taxonomy is
# content-as-data (like lesson YAMLs), validated against the loaded catalog
# at write time instead.


class UserError(Base):
    __tablename__ = "user_errors"

    # CASCADE like user_cards: ledger rows are learning state, not audit data.
    user_id: Mapped[str] = mapped_column(
        Text, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    pattern_id: Mapped[str] = mapped_column(Text, primary_key=True)
    # "open" | "retired" — retired at streak >= 2 (two consecutive correct
    # spontaneous productions in tandem sessions); any recurrence reopens.
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'open'")
    )
    streak: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    # Lifetime error count across all modes.
    occurrences: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )
    first_seen: Mapped[datetime] = mapped_column(
        TIMESTAMP, nullable=False, server_default=text("now()")
    )
    last_seen: Mapped[datetime] = mapped_column(
        TIMESTAMP, nullable=False, server_default=text("now()")
    )
    # "satz" | "situation" | "tandem" — where the pattern last surfaced.
    last_source: Mapped[str] = mapped_column(Text, nullable=False)
    # activity_session id (hex) for conversation sources; NULL from satz.
    last_session_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Ring buffer of the learner's own slips, most recent last, capped at 5:
    # [{sentence, corrected, note, source, at, session_id?}, …] — this is what
    # the tandem grammar-focus layer and the debrief quote back to the learner.
    examples: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    # The tandem prompt-layer query: "this user's open patterns".
    __table_args__ = (Index("ix_user_errors_user_status", "user_id", "status"),)


# ── Verbformen (GRAM-002, Exercise C) ────────────────────────────────────
# Drill-local overlay on the Satzschmiede pool. The verb list still AUTO-FEEDS
# from ``user_cards`` (every verb added there brings its spoken-past sibling),
# but Verbformen's schedule and removals live here — so drilling or hiding a
# verb in this mode never touches the shared Satzschmiede schedule, and vice
# versa. No row = "in the feed, never drilled here".


class UserVerbformen(Base):
    __tablename__ = "user_verbformen"

    user_id: Mapped[str] = mapped_column(Text, primary_key=True)
    card_id: Mapped[str] = mapped_column(Text, primary_key=True)
    # Own SRS, same column shape as user_cards so satz/scheduler.py::schedule
    # and the deck serializers apply unchanged. NULL due_at = never practiced.
    due_at: Mapped[datetime | None] = mapped_column(TIMESTAMP, nullable=True)
    interval_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reps: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    # Removed from the Verbformen deck only — the user_cards row lives on.
    hidden: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )

    # The overlay row follows its pool row: deleting the verb from the
    # Satzschmiede pool (or deleting the user, which cascades through
    # user_cards) drops it here too — auto-feed's one accepted coupling.
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "card_id"],
            ["user_cards.user_id", "user_cards.card_id"],
            ondelete="CASCADE",
        ),
    )


# ── Drill attempts (DATA-004) ────────────────────────────────────────────
# Append-only per-attempt event log across all six drills. Distinct from
# ``user_errors`` (one row per user+pattern, mutated in place, lifetime
# tally) — this is one row per attempt, never mutated, and it's what lets
# the stats endpoint and the Tandem recency weighting answer "what
# happened this week" instead of only "what's the running total".


class DrillAttempt(Base):
    __tablename__ = "drill_attempts"

    id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=False), primary_key=True
    )
    # CASCADE like the other per-user learning-state tables (user_cards,
    # user_errors, user_verbformen) — an attempt log is user-owned practice
    # history, not audit-of-record like activity_session.
    user_id: Mapped[str] = mapped_column(
        Text, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # "satz" | "verbformen" | "sprechen" | "bauteil" | "verbindungen" |
    # "szenario" | "zeitfaerbung" | "genus" | "interview"
    exercise: Mapped[str] = mapped_column(Text, nullable=False)
    # card id / task id / item id / scenario id — whatever the exercise
    # itself keys attempts by. Not an FK: each exercise's item catalog is
    # content-as-data (YAML), not a DB table.
    item_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Taxonomy slug (grammar/taxonomy.yaml) when one applies. Not an FK —
    # same content-as-data rule as UserError.pattern_id.
    pattern_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    # NULL = not a graded pass/fail attempt. Szenario-Sparring is a coach,
    # not a grader — its structure judge never emits a binary verdict, so
    # its rows always record NULL here (they still count toward attempt
    # totals, just not toward accuracy).
    correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # Item-recall verdict alone (satz: the target word was produced, grammar
    # aside). Only satz writes it, matching the scheduler's own word-only
    # read, while ``correct`` stays the strict word-AND-grammar read for
    # accuracy stats. NULL everywhere else.
    word_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # "written" | "spoken"
    modality: Mapped[str] = mapped_column(Text, nullable=False)
    # The client-minted practice-sitting id (OBS-007), e.g. "satz-…".
    session_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        Index("ix_drill_attempts_user_created", "user_id", "created_at"),
        Index("ix_drill_attempts_user_pattern", "user_id", "pattern_id"),
    )


# ── Per-user drill items (CONT-002) ──────────────────────────────────────
# One row per (user, exercise, source card): a per-user drill item forged
# from one of the learner's own Satzschmiede cards, in the SAME dict shape
# the drill's YAML catalog uses so round/attempt code treats both sources
# uniformly. See ``drills/forge.py`` for the forging logic.


class UserDrillItem(Base):
    """CONT-002: one per-user drill item forged from one of the learner's own
    Satzschmiede cards. ``item`` holds the SAME dict shape the drill's YAML
    catalog uses, so round/attempt code treats both sources uniformly; a SQL-
    NULL ``item`` is a tombstone — "forge ran, this card yields nothing for
    this exercise" — so backfill never retries it (``none_as_null=True`` is
    load-bearing: without it SQLAlchemy stores Python None as JSON 'null',
    which the round handlers' ``item.is_not(None)`` SQL filter does NOT
    exclude). One row per (user, exercise, source card): the forge is
    idempotent by constraint, not by care."""

    __tablename__ = "user_drill_items"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    exercise: Mapped[str] = mapped_column(String(32))
    source_card_id: Mapped[str] = mapped_column(ForeignKey("cards.id"))
    item: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    __table_args__ = (
        UniqueConstraint("user_id", "exercise", "source_card_id"),
    )


# ── Word gloss cache (UI-007) ────────────────────────────────────────────
# Hover/tap a German word anywhere in the app -> translation + example. Not
# per-user: the cache key is the normalized surface form alone, so every
# learner's hover of the same word is a shared cache hit. Not an FK-linked
# child of anything — a throwaway lookup cache, not learning state.


class WordGloss(Base):
    __tablename__ = "word_glosses"

    # "wg-" + uuid4().hex[:12], minted by the route on a fresh cache write.
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    # Normalized surface form as looked up (whitespace-collapsed, edge
    # punctuation stripped, lowercased) — the cache key.
    lookup: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    # Dictionary form: nouns -> nominative singular, verbs -> infinitive,
    # adjectives -> base form.
    lemma: Mapped[str] = mapped_column(Text, nullable=False)
    # der/die/das — nouns only, null otherwise.
    article: Mapped[str | None] = mapped_column(Text, nullable=True)
    gloss: Mapped[str] = mapped_column(Text, nullable=False)
    example: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )


# ── Personal audio pool (INTV-003 slice 1) ───────────────────────────────
# The interview workbench (``interview_local/``, git-excluded, local-only)
# is where curated interview audio gets segmented, reviewed and
# brief-annotated by hand. This is the first content type registered into
# Postgres from that workbench: one row per interview recording
# (``audio_items``) and one row per approved, edit-applied chunk within it
# (``audio_chunks``) — imported by ``scripts/import_interviews.py``, never
# hand-written. The mp3s themselves are not touched by the importer; they
# already live in the Railway bucket at ``storage_key``, and this schema
# only registers metadata pointing at them.
#
# ``owner_user_id`` is nullable because a NULL row is reserved for a future
# shared catalog (every learner sees it, not just its owner) — the importer
# always sets it today, so every row minted by slice 1 is one learner's
# personal pool. Rejected chunks (per the workbench's review.json) are never
# imported; a chunk's ``transcript``/``segments`` are the POST-edit result of
# applying ``edits.json``'s segment overrides exactly as
# ``interview_local/app.py``'s ``_merge_chunk_edits`` does, except a deleted
# segment (an override of ``""``) is dropped from the stored list entirely
# rather than kept with a ``hidden`` flag — there is no in-app un-delete flow
# on the Postgres side, so there is nothing to preserve it for.


class AudioItem(Base):
    __tablename__ = "audio_items"

    # uuid4().hex, minted by the importer.
    id: Mapped[str] = mapped_column(String, primary_key=True)
    # NULL reserved for a future shared catalog; every importer-written row
    # sets this. CASCADE like the other per-user learning-state tables —
    # a personal pool item is disposable, not audit-of-record.
    owner_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String, nullable=False)
    # CEFR bucket from the workbench's meta.json ("A1"/"A2"/"B1+"), or NULL
    # if that dir never got one.
    level: Mapped[str | None] = mapped_column(String, nullable=True)
    language: Mapped[str] = mapped_column(String, nullable=False)
    # e.g. "interviews/<workbench dir name>" — the bucket key prefix every
    # chunk's storage_key is built from. Unique: one item per source dir.
    storage_prefix: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    # "workbench" for everything the importer writes today; room for a
    # different value if a second content pipeline ever feeds this table.
    source: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP, nullable=False, server_default=text("now()")
    )


class AudioChunk(Base):
    __tablename__ = "audio_chunks"

    # uuid4().hex, minted by the importer.
    id: Mapped[str] = mapped_column(String, primary_key=True)
    item_id: Mapped[str] = mapped_column(
        ForeignKey("audio_items.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # 1-based order among the IMPORTED (approved-only) chunks, in
    # chunks.json's original order — not the same as the gaps left by
    # rejected chunks.
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    # The workbench's own chunk id within its dir, e.g. "chunk_001".
    source_chunk_id: Mapped[str] = mapped_column(String, nullable=False)
    # "<storage_prefix>/<source_chunk_id>.mp3" — the bucket key; these files
    # already exist in the Railway bucket, the importer only registers them.
    storage_key: Mapped[str] = mapped_column(String, nullable=False)
    # POST-edit: edits.json's segment overrides already applied.
    transcript: Mapped[str] = mapped_column(Text, nullable=False)
    # POST-edit list of {t0, t1, text}; a deleted segment (edits.json
    # override "") is dropped, not kept with a hidden flag.
    segments: Mapped[list] = mapped_column(JSONB, nullable=False)
    # briefs.json's entry verbatim ({shape, summary, question, goals,
    # reviewed, generated_at}), or NULL when the chunk was never brief'd.
    brief: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)

    # One chunk id per item — the importer's idempotency check (does this
    # item's storage_prefix already exist) relies on never violating this.
    __table_args__ = (
        UniqueConstraint(
            "item_id", "source_chunk_id", name="uq_audio_chunks_item_source_chunk"
        ),
    )


# ── Stripe billing (PAY-001) ──────────────────────────────────────────────
# ``subscriptions`` is a local mirror of the Stripe subscription object, kept
# just detailed enough to answer "what does this user have" without a Stripe
# round trip on every request; Stripe itself, not this table, is the billing
# audit-of-record. ``stripe_events`` is the webhook dedup ledger — Stripe
# delivery is at-least-once, so every handler checks this table before
# acting on an event id it may have already applied.


class Subscription(Base):
    __tablename__ = "subscriptions"

    # uuid4().hex, minted at insert time — same PK style as AudioItem/
    # AudioChunk/WordGloss (a Text id minted in app code, not a DB identity).
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    # One subscription row per user (UNIQUE). CASCADE, unlike
    # activity_session's RESTRICT: Stripe is the audit-of-record for billing
    # history, so a deleted account doesn't need to keep an orphaned local
    # mirror row — and CASCADE is what keeps scripts/test_user.py destroy
    # working without a special case for this table.
    user_id: Mapped[str] = mapped_column(
        Text, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    stripe_customer_id: Mapped[str] = mapped_column(Text, nullable=False)
    # Nullable+UNIQUE: a customer can exist (e.g. mid-Checkout) before a
    # subscription does; once one exists, its Stripe id is the natural dedup
    # key for webhook upserts.
    stripe_subscription_id: Mapped[str | None] = mapped_column(
        Text, nullable=True, unique=True
    )
    stripe_price_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The basic/premium mapping of stripe_price_id — not a copy of
    # users.tier, kept separate so a webhook can upsert this row before (or
    # without) touching the user's own tier column. Same content-as-data
    # choice as users.tier: validated in code, not by the schema.
    tier: Mapped[str] = mapped_column(Text, nullable=False)
    # Stripe subscription status verbatim ("active", "past_due", "canceled",
    # "incomplete", ...) — not narrowed in the schema, same content-as-data
    # choice as tier.
    status: Mapped[str] = mapped_column(Text, nullable=False)
    current_period_end: Mapped[datetime | None] = mapped_column(
        TIMESTAMP, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP, nullable=False, server_default=text("now()")
    )
    # Bumped explicitly by database.repository.upsert_subscription on every
    # write — no DB trigger, matching this repo's existing no-trigger style.
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP, nullable=False, server_default=text("now()")
    )


class StripeEvent(Base):
    """Webhook dedup ledger (PAY-001). ``id`` is the Stripe event id itself
    (``evt_...``), not a minted one — that's what makes an
    ``ON CONFLICT DO NOTHING`` insert the dedup check: a redelivered event
    (Stripe's delivery is at-least-once) is a cheap no-op instead of a
    double-applied tier change."""

    __tablename__ = "stripe_events"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    # Stripe event type verbatim, e.g. "checkout.session.completed",
    # "customer.subscription.updated" — logged for debugging, not branched
    # on by the schema.
    type: Mapped[str] = mapped_column(Text, nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        TIMESTAMP, nullable=False, server_default=text("now()")
    )


# ── Coin ledger (PAY-002) ──────────────────────────────────────────────────
# One row per grant/spend/top-up. The two delta columns let one spend span
# both buckets in one row (debit allowance + debit purchased). The UNIQUE on
# (user_id, kind, ref) is the idempotency key for top-ups: a Stripe
# checkout.session.completed redelivery with the same session id must not
# double-credit — the UNIQUE + ON CONFLICT DO NOTHING makes it a no-op.


class CoinLedger(Base):
    __tablename__ = "coin_ledger"

    # uuid4().hex minted in app code; migration backfill uses gen_random_uuid()::text.
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[str] = mapped_column(
        Text, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    delta_allowance: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    delta_purchased: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP, nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        UniqueConstraint(
            "user_id", "kind", "ref", name="uq_coin_ledger_user_kind_ref"
        ),
    )


# ── Voice recordings (REL-001) ──────────────────────────────────────────────
# One row per learner audio clip kept in the (separate, EU-West) voice
# bucket: WHO, WHEN, WHAT exercise, and where the object lives.
# `recordings/store.py` writes the S3 object; `recordings/service.py`
# writes this row right after, off the request's critical path everywhere
# it's hooked in (drill audio routes via BackgroundTasks, the voice-session
# disconnect path via a fire-and-forget task) — a slow/failed upload or
# insert must never touch a learner's latency or surface as an error.


class VoiceRecording(Base):
    __tablename__ = "voice_recordings"

    # uuid4().hex minted in app code — same PK style as CoinLedger/AudioItem.
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[str] = mapped_column(
        Text, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    # "satz" | "sprechen" | "szenario" | "interview" | "teacher" | "tandem" |
    # "lesson" | "conversation" — the drill package, or the voice-session
    # lesson type (pipeline/factory.py's lesson_snapshot["type"]: tandem/
    # teacher/conversation keep their own name, "respond" folds into
    # "lesson" since many respond-type lesson ids exist and `exercise`
    # below already carries the specific one). Content-as-data, not an
    # enum — same choice as users.tier / drill_attempts.exercise.
    surface: Mapped[str] = mapped_column(Text, nullable=False)
    # The drill id / lesson id / teacher `drill` value ("sprechen" |
    # "produce") this clip belongs to. Not every surface has one worth
    # stamping (e.g. a szenario answer's scenario id already rides on
    # `item_id`), so this stays nullable.
    exercise: Mapped[str | None] = mapped_column(Text, nullable=True)
    # "drill_attempt" | "activity_session" | "teacher_exercise" |
    # "interview_comprehension" | "interview_answer" | "satz_rehearsal" —
    # names what `ref_id` points at. Not an FK: the kinds point at different
    # tables (or, for teacher_exercise/interview_comprehension/
    # interview_answer/satz_rehearsal, no table row at all — `ref_id` is
    # then just the chunk/card id, the closest stable identifier the clip
    # has). Free text, not a DB enum/CHECK constraint — a new caller can add
    # a kind without a migration. See the module comment above and the
    # class docstring at the top of 0026_voice_recordings.py.
    ref_kind: Mapped[str] = mapped_column(Text, nullable=False)
    # drill_attempts.id (BigInteger PK, as text) / activity_session.id (the
    # bare session hex) / a teacher exercise item's own id / an interview
    # chunk id / a satz card id (no DB row for the last four kinds).
    ref_id: Mapped[str] = mapped_column(Text, nullable=False)
    # card id / item id / chunk id, when the surface has one. NULL for a
    # voice session (activity_session already IS the item).
    item_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    bucket_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    content_type: Mapped[str] = mapped_column(Text, nullable=False)
    bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    # Filled when cheaply known; most upload sites don't decode the clip
    # just to measure it, so this is NULL far more often than not.
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # The STT text the judge saw, when cheap to pass along — lets a later
    # evaluation replay judge-vs-audio without a second transcription pass.
    transcript: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_voice_recordings_user_created", "user_id", "created_at"),
    )
