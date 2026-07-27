"""user_cards.source: gloss-path daily allowance (SATZ-013)

Marks which ``user_cards`` link came from the GLOSS popover's one-tap add
("gloss") vs. everything else — the manual add-word form, a pack add, the
auto-forged verb-past sibling (NULL). This is the only signal the
``POST /satz/cards`` route needs to count "gloss adds today" without
conflating it with the uncapped manual/pack paths, so the hard cap of 3
gloss-path adds per calendar day (UTC) can be enforced there.

``added_at`` already exists on this table (SATZ dosing P1, TIMESTAMP,
server_default now()) — no new timestamp column is needed; the gloss cap
counts against it directly, same as the existing ``started_at`` /
``NEW_PER_DAY`` allowance in ``GET /satz/deck``.

Pure additive migration; no existing rows are touched (all read as NULL =
legacy/manual, which is correct — nothing before this shipped through the
gloss path).

Revision ID: 0013_user_cards_gloss_source
Revises: 0012_user_cards_started_at
Create Date: 2026-07-27
"""

from alembic import op
import sqlalchemy as sa


revision = "0013_user_cards_gloss_source"
down_revision = "0012_user_cards_started_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_cards", sa.Column("source", sa.String(length=16), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("user_cards", "source")
