"""users: daily streak cache (GAME-001)

Adds a denormalized streak cache to ``users``, updated on write by
``database.repository.credit_streak_day`` (called from ``record_drill_attempt``
and the conversation-session disconnect path) — O(1) read for the stats
endpoint, no hot-path scan over ``drill_attempts``/``activity_session``.

- ``current_streak`` / ``longest_streak`` — consecutive UTC-day count and its
  all-time high (the high never resets, even when the current streak breaks).
- ``last_streak_day`` — the last UTC calendar day credited.
- ``streak_grace_used_on`` — the one missed day the automatic weekly grace
  forgave (at most once per rolling 7 days); NULL = grace available.

Days bucket by UTC calendar day (same documented limitation as ``stats/routes.py``'s
``today`` window) — a learner west of UTC sees their day roll over mid-evening
local time, pending a per-user timezone column.

Pure additive migration; no existing rows are touched.

Revision ID: 0017_user_streaks
Revises: 0016_cards_examples
Create Date: 2026-08-05
"""

from alembic import op
import sqlalchemy as sa


revision = "0017_user_streaks"
down_revision = "0016_cards_examples"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("current_streak", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "users",
        sa.Column("longest_streak", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "users",
        sa.Column("last_streak_day", sa.Date(), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("streak_grace_used_on", sa.Date(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "streak_grace_used_on")
    op.drop_column("users", "last_streak_day")
    op.drop_column("users", "longest_streak")
    op.drop_column("users", "current_streak")
