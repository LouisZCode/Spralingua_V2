"""activity_session.error_eval: the drill-harvest record (GRAM-001 Phase 2)

Adds one nullable JSONB column alongside ``goal_eval`` / ``pron_eval``. It
stores the post-session grammar-error extraction
(``ErrorExtraction.model_dump()`` — the classified slips harvested from the
drill transcript), the third disconnect-side evaluator result. NULL when the
lesson has no goals (harvest is gated on the same ``load_goal`` check as the
goal evaluator, so lesson_zero / welcome / goodbye_test are auto-excluded).

The ledger (``user_errors``) is the durable cross-session store; this column
is the per-session record, parallel to the other two eval columns — useful
for debugging and later backfill, deliberately NOT surfaced in the drill
modal (one feedback voice per mode).

Revision ID: 0007_activity_session_error_eval
Revises: 0006_user_errors
Create Date: 2026-07-05
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0007_activity_session_error_eval"
down_revision = "0006_user_errors"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "activity_session",
        sa.Column("error_eval", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("activity_session", "error_eval")
