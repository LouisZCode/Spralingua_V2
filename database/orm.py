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

from datetime import datetime

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    Integer,
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

    __table_args__ = (UniqueConstraint("email", name="uq_users_email"),)


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
    type: Mapped[str] = mapped_column(Text, nullable=False)  # noun|verb|phrase
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
