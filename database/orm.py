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
