"""user_errors: the grammar-error ledger (GRAM-001)

One row per (user, grammar pattern) — patterns, not individual slips.
``pattern_id`` is a slug from ``grammar/taxonomy.yaml`` (content-as-data,
so no FK; writes validate against the loaded catalog instead). ``examples``
is a JSONB ring buffer (capped at 5 in code) holding the learner's own
erroneous sentences + corrections, which the tandem grammar-focus layer and
debrief quote back verbatim.

Lifecycle columns exist from day one so the tandem phases (3–4) are pure
code, no second migration — same trick as user_cards' SRS columns in 0004:
``streak``/``status`` implement retire-at-2 / reopen-on-recurrence.

Revision ID: 0006_user_errors
Revises: 0005_verb_tenses
Create Date: 2026-07-05
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0006_user_errors"
down_revision = "0005_verb_tenses"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_errors",
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("pattern_id", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'open'")),
        sa.Column("streak", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("occurrences", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("first_seen", sa.TIMESTAMP(), nullable=False, server_default=sa.text("now()")),
        sa.Column("last_seen", sa.TIMESTAMP(), nullable=False, server_default=sa.text("now()")),
        sa.Column("last_source", sa.Text(), nullable=False),
        sa.Column("last_session_id", sa.Text(), nullable=True),
        sa.Column("examples", postgresql.JSONB(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "pattern_id"),
    )
    op.create_index("ix_user_errors_user_status", "user_errors", ["user_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_user_errors_user_status", table_name="user_errors")
    op.drop_table("user_errors")
