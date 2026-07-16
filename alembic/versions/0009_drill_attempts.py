"""drill_attempts: append-only per-attempt event log across all drills (DATA-004)

Every graded (or coach-reviewed) attempt across the six practice surfaces —
Satzschmiede, Verbformen, Sprechen, Bauteil-Sätze, Feste Verbindungen,
Szenario-Sparring — writes one row here, independent of and in addition to
whatever exercise-local state it also updates (``user_cards`` schedule,
``user_verbformen`` overlay, ``user_errors`` ledger). The ledger tracks
PATTERNS (one row per user+pattern, mutated in place); this table tracks
EVENTS (one row per attempt, never mutated) — the stats endpoint (``GET
/me/stats``) and the Tandem grammar-focus recency weighting both read
attempt-level history the ledger alone can't answer ("what happened this
week" vs. "what's the lifetime tally").

``correct`` is nullable because Szenario-Sparring is a coach, not a grader —
P1's structure judge never emits a binary pass/fail, so its rows record
NULL rather than a fabricated bool.

Pure additive migration; no existing rows are touched.

Revision ID: 0009_drill_attempts
Revises: 0008_user_verbformen
Create Date: 2026-07-16
"""

from alembic import op
import sqlalchemy as sa


revision = "0009_drill_attempts"
down_revision = "0008_user_verbformen"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "drill_attempts",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=False),
            primary_key=True,
        ),
        sa.Column("user_id", sa.Text(), nullable=False),
        # satz | verbformen | sprechen | bauteil | verbindungen | szenario
        sa.Column("exercise", sa.Text(), nullable=False),
        # card id / task id / item id / scenario id — whatever the exercise
        # itself keys attempts by.
        sa.Column("item_ref", sa.Text(), nullable=True),
        # Taxonomy slug (grammar/taxonomy.yaml) when one applies; NULL for
        # Szenario (structure-only judge, no pattern targeting).
        sa.Column("pattern_id", sa.Text(), nullable=True),
        # NULL = not a graded pass/fail attempt (Szenario coach rows).
        sa.Column("correct", sa.Boolean(), nullable=True),
        # written | spoken
        sa.Column("modality", sa.Text(), nullable=False),
        # The client-minted practice-sitting id (OBS-007), e.g. "satz-…".
        sa.Column("session_id", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_drill_attempts_user_created",
        "drill_attempts",
        ["user_id", "created_at"],
    )
    op.create_index(
        "ix_drill_attempts_user_pattern",
        "drill_attempts",
        ["user_id", "pattern_id"],
    )


def downgrade() -> None:
    op.drop_table("drill_attempts")
