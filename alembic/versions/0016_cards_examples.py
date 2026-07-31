"""cards.examples: forged leveled example pool (SATZ-017)

Every card ships with ONE example sentence (hand-authored in the packs,
enricher-written for community adds), so each encounter re-reads the same
sentence forever — the user asked for "different ways to use that same word
correctly in different levels of sentences".

This adds a nullable JSONB list of extra forged examples per card:

- ``examples`` — a list of ~3 natural German sentences at ranged difficulty
  (one simple ~A2, one mid, one more complex ~B1+), written once per card by
  ``satz/example_forge.py`` (forge-on-add + lazy deck-fetch backfill, the
  CONT-002 pattern) and gated by a deterministic target-presence scan plus a
  verify-LLM pass before anything reaches a learner. NULL = not forged yet
  (backfill retries); content is CARD-level — shared catalog, not per-user.

The original ``example`` column stays untouched: it remains the first entry
of the rotation pool and the only example Verbformen's payload serves.

Pure additive migration; no existing rows are touched.

Revision ID: 0016_cards_examples
Revises: 0015_user_cards_leech
Create Date: 2026-07-31
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0016_cards_examples"
down_revision = "0015_user_cards_leech"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "cards",
        sa.Column("examples", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("cards", "examples")
