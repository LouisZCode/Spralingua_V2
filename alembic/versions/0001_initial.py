"""initial schema: users + activity_session (DATA-001)

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-25
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "activity_session",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("lesson_id", sa.Text(), nullable=False),
        sa.Column("level", sa.Text(), nullable=True),
        sa.Column("situation", sa.Text(), nullable=True),
        sa.Column("voice", sa.Text(), nullable=True),
        sa.Column("started_at", sa.TIMESTAMP(), nullable=False),
        sa.Column("ended_at", sa.TIMESTAMP(), nullable=True),
        sa.Column("ended_by", sa.Text(), nullable=True),
        sa.Column("transcript", sa.Text(), nullable=True),
        sa.Column("audio_path", sa.Text(), nullable=True),
        sa.Column("lesson_snapshot", postgresql.JSONB(), nullable=True),
        sa.Column("goal_eval", postgresql.JSONB(), nullable=True),
        sa.Column("pron_eval", postgresql.JSONB(), nullable=True),
        sa.Column("passed", sa.Boolean(), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        "ix_activity_session_user_started",
        "activity_session",
        ["user_id", sa.text("started_at DESC")],
    )
    op.create_index(
        "ix_activity_session_user_lesson",
        "activity_session",
        ["user_id", "lesson_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_activity_session_user_lesson", table_name="activity_session")
    op.drop_index("ix_activity_session_user_started", table_name="activity_session")
    op.drop_table("activity_session")
    op.drop_table("users")
