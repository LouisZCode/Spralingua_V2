"""user role: access-tier column on users (developer/premium/normal)

Revision ID: 0003_user_role
Revises: 0002_user_auth
Create Date: 2026-06-07
"""

from alembic import op
import sqlalchemy as sa


revision = "0003_user_role"
down_revision = "0002_user_auth"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Access tier. NOT NULL with a server default so existing rows (incl. the
    # 'demo' sentinel) backfill to 'normal'. Only 'developer' is wired today
    # (unlocks the UI dev tab); 'premium' is reserved for a future tier.
    op.add_column(
        "users",
        sa.Column(
            "role",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'normal'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "role")
