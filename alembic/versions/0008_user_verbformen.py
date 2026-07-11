"""user_verbformen: the Verbformen drill-local overlay (GRAM-002, Exercise C)

Verbformen stops being a pure lens over ``user_cards``: the verb list still
auto-feeds from the Satzschmiede pool (past-tense sibling cards), but the
drill's schedule and removals move to this per-user overlay — so greening or
hiding a verb in Verbformen never touches the shared Satzschmiede schedule.

Same SRS column shape as ``user_cards`` (0004), so ``satz/scheduler.py`` and
the deck serializers apply unchanged. The composite FK cascades from
``user_cards``: deleting a verb from the Satzschmiede pool drops its overlay
row too (Satzschmiede stays the vocab source — auto-feed's one coupling).

Pure additive migration; no existing rows are touched.

Revision ID: 0008_user_verbformen
Revises: 0007_activity_session_error_eval
Create Date: 2026-07-11
"""

from alembic import op
import sqlalchemy as sa


revision = "0008_user_verbformen"
down_revision = "0007_activity_session_error_eval"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_verbformen",
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("card_id", sa.Text(), nullable=False),
        sa.Column("due_at", sa.TIMESTAMP(), nullable=True),
        sa.Column("interval_days", sa.Integer(), nullable=True),
        sa.Column("reps", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "hidden", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "card_id"],
            ["user_cards.user_id", "user_cards.card_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", "card_id"),
    )


def downgrade() -> None:
    op.drop_table("user_verbformen")
