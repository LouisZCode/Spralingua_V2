"""cards: verb tense siblings (SATZ-002 — one verb, two cards)

tense       — NULL = present (every pre-existing row stays untouched), "past"
              for the spoken-past sibling forged alongside a verb.
tense_form  — the natural SPOKEN past shown on the answer side: "ist
              geflogen", "dachte · hat gedacht", or Präteritum-only where the
              Perfekt is unnatural in speech ("war", "hatte", "wollte").

The dedup unique index widens to include the tense so the present and past
siblings of one verb can share (type, lower(target)).

Revision ID: 0005_verb_tenses
Revises: 0004_satzschmiede
Create Date: 2026-07-05
"""

from alembic import op
import sqlalchemy as sa


revision = "0005_verb_tenses"
down_revision = "0004_satzschmiede"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("cards", sa.Column("tense", sa.Text(), nullable=True))
    op.add_column("cards", sa.Column("tense_form", sa.Text(), nullable=True))
    op.drop_index("uq_cards_type_target_lower", table_name="cards")
    op.create_index(
        "uq_cards_type_target_lower",
        "cards",
        ["type", sa.text("lower(target)"), sa.text("coalesce(tense, 'present')")],
        unique=True,
    )


def downgrade() -> None:
    # The narrower index can't coexist with tense siblings — drop them (and
    # their pool links) before shrinking the dedup seam back.
    op.execute(
        "DELETE FROM user_cards WHERE card_id IN "
        "(SELECT id FROM cards WHERE tense IS NOT NULL)"
    )
    op.execute("DELETE FROM cards WHERE tense IS NOT NULL")
    op.drop_index("uq_cards_type_target_lower", table_name="cards")
    op.create_index(
        "uq_cards_type_target_lower",
        "cards",
        ["type", sa.text("lower(target)")],
        unique=True,
    )
    op.drop_column("cards", "tense_form")
    op.drop_column("cards", "tense")
