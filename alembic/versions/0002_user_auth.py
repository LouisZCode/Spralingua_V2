"""user auth: OAuth profile columns + demo sentinel (AUTH-001)

Revision ID: 0002_user_auth
Revises: 0001_initial
Create Date: 2026-06-04
"""

from alembic import op
import sqlalchemy as sa


revision = "0002_user_auth"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # OAuth profile fields on the existing users table (Google sign-in, P-3).
    op.add_column("users", sa.Column("email", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("name", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("picture", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("last_login_at", sa.TIMESTAMP(), nullable=True))
    # Email is unique among real users; the demo user (and any profile-less row)
    # has NULL email, and Postgres treats NULLs as distinct, so no collision.
    op.create_unique_constraint("uq_users_email", "users", ["email"])
    # Single sentinel user that anchors anonymous front-page demo sessions, so
    # the world-facing demo route doesn't write one users row per visitor.
    # Idempotent so re-running (or a downgrade/upgrade cycle) is a no-op.
    op.execute(
        "INSERT INTO users (id, created_at) VALUES ('demo', now()) "
        "ON CONFLICT (id) DO NOTHING"
    )


def downgrade() -> None:
    # The 'demo' sentinel is intentionally NOT deleted here: activity_session
    # rows may FK-reference it (ondelete RESTRICT), and re-upgrade re-seeds it
    # idempotently anyway.
    op.drop_constraint("uq_users_email", "users", type_="unique")
    op.drop_column("users", "last_login_at")
    op.drop_column("users", "picture")
    op.drop_column("users", "name")
    op.drop_column("users", "email")
