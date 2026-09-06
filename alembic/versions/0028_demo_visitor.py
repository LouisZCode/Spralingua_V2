"""demo visitor identity (REL-001 follow-up, P2-IMPL, 2026-09-05).

Luis's decision: one anonymous visitor token per browser for the front-page
demo (Proposal-2) — minted client-side, persisted in the browser's
``localStorage``, replayed on every ``/ws/demo/{user_id}`` connect, and
linked to a real account the first time that browser signs up. This lets
Luis see how a visitor uses the demo and whether they convert later,
without ever writing an IP address anywhere (privacy §2.6's promise).

Two nullable, additive columns — no backfill, no rewrite of existing rows:

1. ``activity_session.visitor_id`` — the token for a demo session row, NULL
   for every authenticated session (``/ws/{user_id}`` never sets it). A
   PARTIAL index (``WHERE visitor_id IS NOT NULL``) keeps the index to just
   the demo rows instead of indexing a mostly-NULL column across the whole
   table — cheap today, stays cheap as the authenticated session count
   grows much faster than the demo's.
2. ``users.demo_visitor_id`` — set at most once, by ``PUT
   /me/demo-visitor``'s first-wins UPDATE, the first time a browser that
   already used the demo signs up. Nullable with no default, so this is a
   metadata-only change on Postgres (no table rewrite, no long lock) — worth
   saying explicitly since ``users`` is the hottest table in the schema
   (DB-001's policy note: a future DDL change on it needs to justify its
   lock cost).

Follows 0027_dbfix.py's style: plain ``op.add_column``/``op.create_index``,
downgrade reverses all three statements in the opposite order.

Revision ID: 0028_demo_visitor
Revises: 0027_dbfix
Create Date: 2026-09-05
"""

from alembic import op
import sqlalchemy as sa


revision = "0028_demo_visitor"
down_revision = "0027_dbfix"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. activity_session.visitor_id — NULL for every authenticated session;
    # only the demo socket (main.py::ws_demo_endpoint) ever writes it.
    op.add_column(
        "activity_session", sa.Column("visitor_id", sa.Text(), nullable=True)
    )
    # Partial index: NULL on every row but the demo's, so only demo rows are
    # actually indexed.
    op.create_index(
        "ix_activity_session_visitor",
        "activity_session",
        ["visitor_id"],
        postgresql_where=sa.text("visitor_id IS NOT NULL"),
    )

    # 2. users.demo_visitor_id — nullable, no default, so Postgres treats this
    # as metadata-only (no table rewrite, no long lock) even on `users`, the
    # hottest table in the schema (DB-001 policy). Written at most once, by
    # PUT /me/demo-visitor's first-wins UPDATE ... WHERE demo_visitor_id IS NULL.
    op.add_column(
        "users", sa.Column("demo_visitor_id", sa.Text(), nullable=True)
    )


def downgrade() -> None:
    # Reverse order of upgrade().
    op.drop_column("users", "demo_visitor_id")
    op.drop_index("ix_activity_session_visitor", table_name="activity_session")
    op.drop_column("activity_session", "visitor_id")
