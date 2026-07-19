"""user_drill_items: per-user drill items forged from the learner's own
Satzschmiede vocabulary (CONT-002)

The user ruled (2026-07-19) that Satzschmiede deck words should feed the
grammar drills — deck words are by definition still being LEARNED, so the
old "familiar words let you dodge the ending" caveat doesn't apply. Each
round mixes 50/50: half the shared YAML catalog, half items forged here
(``drills/forge.py``) from the learner's own nouns/verbs.

``item`` holds the SAME dict shape the drill's YAML catalog uses, so
round/attempt code treats both sources uniformly. A NULL ``item`` is a
tombstone — "forge ran, this card yields nothing for this exercise" — so
backfill never retries it. One row per (user, exercise, source card): the
forge is idempotent by constraint, not by care.

Pure additive migration; no existing rows are touched.

Revision ID: 0010_user_drill_items
Revises: 0009_drill_attempts
Create Date: 2026-07-19
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0010_user_drill_items"
down_revision = "0009_drill_attempts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_drill_items",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("user_id", sa.Text(), nullable=False),
        # bauteil | verbindungen
        sa.Column("exercise", sa.String(length=32), nullable=False),
        sa.Column("source_card_id", sa.Text(), nullable=False),
        # Same dict shape the YAML catalog items use; NULL = tombstone.
        sa.Column("item", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["source_card_id"], ["cards.id"]),
    )
    op.create_index(
        "ix_user_drill_items_user_id",
        "user_drill_items",
        ["user_id"],
    )
    op.create_unique_constraint(
        "uq_user_drill_items_user_exercise_card",
        "user_drill_items",
        ["user_id", "exercise", "source_card_id"],
    )


def downgrade() -> None:
    op.drop_table("user_drill_items")
