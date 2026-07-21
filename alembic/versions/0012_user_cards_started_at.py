"""user_cards.started_at: first-graded-attempt stamp (SATZ dosing P1)

Drives the 5-new-per-day allowance in ``GET /satz/deck`` — a card counts as
"started today" once it gets its first graded attempt or reveal, and the
allowance counts how many cards started today. NULL = still untouched
(never counted).

Backfill: any card that already shows practice (``due_at`` set, or
``reps > 0``) is stamped with its own ``added_at`` — good enough since only
"not today" matters for the allowance, and the exact historical date is
otherwise irrelevant.

Revision ID: 0012_user_cards_started_at
Revises: 0011_word_glosses
Create Date: 2026-07-21
"""

from alembic import op
import sqlalchemy as sa


revision = "0012_user_cards_started_at"
down_revision = "0011_word_glosses"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Naive timestamp, matching due_at/added_at on this table (drill_attempts
    # is the tz-aware outlier).
    op.add_column("user_cards", sa.Column("started_at", sa.TIMESTAMP(), nullable=True))
    op.execute(
        "UPDATE user_cards SET started_at = added_at "
        "WHERE due_at IS NOT NULL OR reps > 0"
    )


def downgrade() -> None:
    op.drop_column("user_cards", "started_at")
