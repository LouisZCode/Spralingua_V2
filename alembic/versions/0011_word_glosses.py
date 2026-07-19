"""word_glosses: cache table for the word-gloss endpoint (UI-007)

Hover/tap a German word anywhere in the app -> translation + example. Most
surface forms are typed straight out of the catalog (``cards.target``), so
the route checks that first and never touches this table for a catalog hit.
On a catalog miss it makes one structured-output LLM call
(``satz/glosser.py``) and caches the result here keyed on the normalized
surface form (``lookup``) — every later hover of the same inflected form
("Mänteln", "gehänselt", ...) across every learner is then a plain SELECT,
no LLM call.

Distinct from ``cards``: a gloss row is a throwaway lookup cache (lemma +
one gloss + one example, no article/type/level/note taxonomy), not a
flashcard — it never feeds ``user_cards`` directly. The route resolves a
catalog card for the LEMMA separately so the frontend can still offer
"add to deck" when one exists.

Pure additive migration; no existing rows are touched.

Revision ID: 0011_word_glosses
Revises: 0010_user_drill_items
Create Date: 2026-07-19
"""

from alembic import op
import sqlalchemy as sa


revision = "0011_word_glosses"
down_revision = "0010_user_drill_items"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "word_glosses",
        sa.Column("id", sa.Text(), primary_key=True),
        # Normalized surface form as looked up (whitespace-collapsed,
        # edge-punctuation-stripped, lowercased) — the cache key.
        sa.Column("lookup", sa.Text(), nullable=False),
        # Dictionary form: nouns get nominative singular, verbs the
        # infinitive, adjectives the base form.
        sa.Column("lemma", sa.Text(), nullable=False),
        # der/die/das — nouns only, null otherwise.
        sa.Column("article", sa.Text(), nullable=True),
        sa.Column("gloss", sa.Text(), nullable=False),
        sa.Column("example", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_unique_constraint(
        "uq_word_glosses_lookup", "word_glosses", ["lookup"]
    )


def downgrade() -> None:
    op.drop_table("word_glosses")
