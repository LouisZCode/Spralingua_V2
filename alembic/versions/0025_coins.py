"""Coin system: two-bucket model + ledger (PAY-002).

Two buckets per user:

- ``allowance`` — daily coins that RESET at 5am in the user's local time.
  Refreshed LAZILY on next read/spend (no cron): ``users.allowance_day`` is
  the user-local "coin day" ``(local_now - 5h).date()`` that the current
  bucket belongs to. When that day differs from ``today_key(timezone)`` the
  bucket is overwritten with ``DAILY_ALLOWANCE[tier]``. Lazy refresh is what
  lets a single Postgres hold thousands of users without a 5am fan-out job
  that must succeed for every timezone offset; a user who doesn't log in for
  a week simply sees a stale bucket until their next access, which is correct
  (unused daily coins do NOT roll over). ``allowance_day`` is NULL for
  never-refreshed users (every row before this migration, and new signups
  before their first spend/read).

- ``purchased_coins`` — persistent bucket that NEVER resets: the one-time
  signup grant (100) + Stripe top-ups (500 each) land here and survive the
  daily refresh. Spending drains allowance FIRST, purchased second.

``coin_ledger`` is the append-only audit trail behind both buckets: every
granted or spent delta has a row, keyed ``(user_id, kind, ref)`` UNIQUE as
the idempotency key for top-ups (a Stripe redelivery with the same session
id must not double-credit — the UNIQUE + ON CONFLICT DO NOTHING makes the
second credit a no-op without a separate "already seen?" check). ``delta_*``
columns let one spend span both buckets in one row (debit the allowance
column + debit the purchased column), while top-ups/grants touch only the
purchased column.

Data migration: every existing ``users`` row gets ``purchased_coins = 100``
(the pending signup grant from cost_price.md's offer) and one matching
``signup_grant`` ledger row per user (``ref = users.id``), so
``sum(ledger) == balance`` holds without a separate backfill step.

Revision ID: 0025_coins
Revises: 0024_stripe_billing
Create Date: 2026-08-25
"""

from alembic import op
import sqlalchemy as sa

revision = "0025_coins"
down_revision = "0024_stripe_billing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Users: daily bucket + persistent bucket + timezone.
    op.add_column(
        "users",
        sa.Column("timezone", sa.Text(), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("allowance_day", sa.Date(), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column(
            "allowance_remaining",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "purchased_coins",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )

    # Ledger: one row per grant/spend/top-up, auditable and idempotent for
    # top-ups via the (user_id, kind, ref) unique.
    op.create_table(
        "coin_ledger",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Text(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("ref", sa.Text(), nullable=True),
        sa.Column(
            "delta_allowance",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "delta_purchased",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("user_id", "kind", "ref", name="uq_coin_ledger_user_kind_ref"),
    )
    op.create_index("ix_coin_ledger_user_id", "coin_ledger", ["user_id"])

    # Backfill: grant 100 purchased coins to every existing user and insert
    # the matching ledger row so sum(ledger) == balance. Use gen_random_uuid()
    # for the ledger id (uuid4().hex is the app-code mint; the migration must
    # not import Python uuid — use Postgres' own generator). pg_extension
    # pgcrypto (gen_random_uuid) is already available on this DB via prior
    # Postgres defaults; fall back to uuid_generate_v4 if needed is not
    # provided — both are present on a stock Postgres.
    op.execute(sa.text("UPDATE users SET purchased_coins = 100"))
    op.execute(
        sa.text(
            "INSERT INTO coin_ledger (id, user_id, kind, ref, delta_allowance, delta_purchased) "
            "SELECT gen_random_uuid()::text, id, 'signup_grant', id, 0, 100 FROM users"
        )
    )


def downgrade() -> None:
    op.drop_index("ix_coin_ledger_user_id", table_name="coin_ledger")
    op.drop_table("coin_ledger")
    op.drop_column("users", "purchased_coins")
    op.drop_column("users", "allowance_remaining")
    op.drop_column("users", "allowance_day")
    op.drop_column("users", "timezone")
