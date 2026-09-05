"""DBFIX — three schema fixes from the 2026-09-05 database review (DB-001).

Three independent fixes bundled into one migration because each is a
one-line DDL change with no data risk (the review's fourth fix, the
``audio_items.owner_user_id`` type drift, was ORM-only and needed no DDL):

1. ``user_drill_items.user_id`` gets ``ON DELETE CASCADE``. It was the only
   per-user table without it — ``user_cards``, ``user_errors``,
   ``drill_attempts``, ``coin_ledger`` and ``voice_recordings`` all cascade
   already. Without this, ``DELETE FROM users`` fails for any learner who
   ever had a personal drill pool, before ``activity_session``'s
   deliberate RESTRICT is even reached.
2. ``user_drill_items.created_at`` gets a ``server_default`` of ``now()``,
   matching every other timestamp column in the schema. Today only the ORM
   default (``datetime.now``, Python-side) fills it, so a bare INSERT that
   doesn't go through the ORM leaves it NULL-then-rejected instead of
   defaulted.
3. Drop ``ix_coin_ledger_user_id``. ``uq_coin_ledger_user_kind_ref
   (user_id, kind, ref)`` already serves every ``WHERE user_id = ...``
   lookup by leftmost prefix, so the standalone index was a permanent
   double write on an append-only table for no read benefit.

Revision ID: 0027_dbfix
Revises: 0026_voice_recordings
Create Date: 2026-09-05
"""

from alembic import op
import sqlalchemy as sa


revision = "0027_dbfix"
down_revision = "0026_voice_recordings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Order matters for lock time, not correctness (DBFIXREV, 2026-09-05):
    # Alembic runs the whole upgrade in ONE transaction, and dropping /
    # re-adding a foreign key takes ACCESS EXCLUSIVE on BOTH tables — on
    # `users` too, which every coin gate reads. So the two cheap statements
    # go first and the FK swap goes last: the exclusive lock on `users` is
    # then held only from the final statement to COMMIT, not across the
    # index drop as well. All three tables are tiny today; this is about
    # not letting the window grow with them.

    # 1. user_drill_items.created_at -> server_default now().
    op.alter_column(
        "user_drill_items",
        "created_at",
        server_default=sa.text("now()"),
    )

    # 2. Drop the redundant standalone index on coin_ledger.user_id.
    op.drop_index("ix_coin_ledger_user_id", table_name="coin_ledger")

    # 3. user_drill_items.user_id -> ON DELETE CASCADE. The existing FK has
    # the default Postgres-generated name (no naming convention configured
    # on Base.metadata), confirmed against the live DB with \d
    # user_drill_items before writing this: user_drill_items_user_id_fkey.
    op.drop_constraint(
        "user_drill_items_user_id_fkey", "user_drill_items", type_="foreignkey"
    )
    op.create_foreign_key(
        "user_drill_items_user_id_fkey",
        "user_drill_items",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    # Reverse order of upgrade(): FK first (it is the last thing upgrade
    # did), then the index, then the default.
    op.drop_constraint(
        "user_drill_items_user_id_fkey", "user_drill_items", type_="foreignkey"
    )
    op.create_foreign_key(
        "user_drill_items_user_id_fkey",
        "user_drill_items",
        "users",
        ["user_id"],
        ["id"],
    )

    op.create_index("ix_coin_ledger_user_id", "coin_ledger", ["user_id"])

    op.alter_column(
        "user_drill_items",
        "created_at",
        server_default=None,
    )
