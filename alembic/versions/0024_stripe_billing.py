"""Stripe billing: users.tier + subscriptions + stripe_events (PAY-001)

Phase 1 (database layer) of the Stripe integration. Three pieces:

- ``users.tier`` — ``free`` | ``basic`` | ``premium``, NOT NULL, defaulting
  every existing and future account to ``free``. Deliberately plain
  ``Text``, not a DB enum or a checked column — same content-as-data
  choice already made for ``daily_mode_completions.mode``: the three
  values are validated in code (``database.repository.set_user_tier``),
  not by the schema. Separate from ``role``, which keeps its existing
  dev-tools meaning (``normal``/``premium``/``developer``) untouched.
  Webhook handlers are the only writer for paid tiers; every Google
  sign-up lands on ``free`` via the column default, no app-code write
  needed at signup time.

- ``subscriptions`` — one row per user (``user_id`` UNIQUE) mirroring the
  Stripe subscription object closely enough to answer "what does this
  user have" without a Stripe round trip. ``user_id`` is CASCADE on
  delete: Stripe itself is the billing audit-of-record, so a deleted
  Spralingua account doesn't need to keep an orphaned local mirror row —
  and CASCADE is what keeps ``scripts/test_user.py destroy`` working
  without a special case. ``stripe_customer_id`` is NOT NULL (a Checkout
  session always creates or reuses a customer); ``stripe_subscription_id``
  is nullable+UNIQUE because a customer can exist (e.g. mid-Checkout)
  before a subscription does, and once a subscription exists its Stripe id
  is the natural dedup key for webhook upserts.

- ``stripe_events`` — the webhook dedup ledger. Stripe's delivery is
  at-least-once, so every webhook handler must check-then-skip a
  previously processed event id before acting on it; ``id`` is the Stripe
  event id itself (``evt_...``) as the primary key, and
  ``record_stripe_event`` inserts with ``ON CONFLICT DO NOTHING`` so a
  redelivered event is a cheap no-op rather than a double-applied tier
  change.

Pure additive migration; no existing rows or columns beyond the new
``users.tier`` (server-defaulted, so existing rows backfill to ``'free'``
with no separate UPDATE) are touched.

Revision ID: 0024_stripe_billing
Revises: 0023_level_buckets
Create Date: 2026-08-24
"""

from alembic import op
import sqlalchemy as sa


revision = "0024_stripe_billing"
down_revision = "0023_level_buckets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "tier", sa.Text(), nullable=False, server_default=sa.text("'free'")
        ),
    )

    op.create_table(
        "subscriptions",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("stripe_customer_id", sa.Text(), nullable=False),
        sa.Column("stripe_subscription_id", sa.Text(), nullable=True),
        sa.Column("stripe_price_id", sa.Text(), nullable=True),
        # The basic/premium mapping of stripe_price_id, not a copy of
        # users.tier — kept separate so a webhook can upsert this row before
        # (or without) touching the user's own tier column.
        sa.Column("tier", sa.Text(), nullable=False),
        # Stripe subscription status verbatim ("active", "past_due",
        # "canceled", "incomplete", ...) — not narrowed in the schema, same
        # content-as-data choice as ``tier``.
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("current_period_end", sa.TIMESTAMP(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_unique_constraint(
        "uq_subscriptions_user_id", "subscriptions", ["user_id"]
    )
    op.create_unique_constraint(
        "uq_subscriptions_stripe_subscription_id",
        "subscriptions",
        ["stripe_subscription_id"],
    )

    op.create_table(
        "stripe_events",
        # The Stripe event id itself (e.g. "evt_..."), not a minted uuid —
        # that's what makes ON CONFLICT DO NOTHING the dedup check.
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column(
            "received_at",
            sa.TIMESTAMP(),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )


def downgrade() -> None:
    op.drop_table("stripe_events")
    op.drop_table("subscriptions")
    op.drop_column("users", "tier")
