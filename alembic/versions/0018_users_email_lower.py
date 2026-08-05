"""users: backfill email to lowercase (CHORE-001)

Case-insensitive email uniqueness is now enforced at the write site
(``database.repository.upsert_user`` lowercases ``email`` before every
insert/update), not via a DB-level constraint — the unique constraint on
``users.email`` stays on the raw column. This migration is a one-time
backfill so rows written before that change (mixed-case emails) match the
now-normalized rows going forward.

Pure data migration; no schema change.

Revision ID: 0018_users_email_lower
Revises: 0017_user_streaks
Create Date: 2026-08-05
"""

from alembic import op


revision = "0018_users_email_lower"
down_revision = "0017_user_streaks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "UPDATE users SET email = LOWER(email) "
        "WHERE email IS NOT NULL AND email <> LOWER(email)"
    )


def downgrade() -> None:
    # No-op: lowercasing is not reversible (the original casing isn't stored).
    pass
