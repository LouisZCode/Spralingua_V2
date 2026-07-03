"""satzschmiede: canonical cards catalog + packs + per-user pool (SATZ-002)

cards        — one shared canonical row per word (curated from YAML sync,
               community from the add-a-word enricher). No owner_id: user-added
               words join the same catalog so popularity counts work.
pack_cards   — pack membership (a card can sit in several packs).
user_cards   — the per-user pool + SRS scheduling state (due_at etc. created
               now so the scheduler phase needs no second migration).

Revision ID: 0004_satzschmiede
Revises: 0003_user_role
Create Date: 2026-07-03
"""

from alembic import op
import sqlalchemy as sa


revision = "0004_satzschmiede"
down_revision = "0003_user_role"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cards",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("target", sa.Text(), nullable=False),
        sa.Column("article", sa.Text(), nullable=True),
        sa.Column(
            "reflexive", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("gloss", sa.Text(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("example", sa.Text(), nullable=True),
        sa.Column("level", sa.Text(), nullable=True),
        sa.Column(
            "source", sa.Text(), nullable=False, server_default=sa.text("'curated'")
        ),
        sa.Column(
            "first_added_by",
            sa.Text(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    # Dedup seam for add-a-word: one canonical row per (type, word).
    op.create_index(
        "uq_cards_type_target_lower",
        "cards",
        ["type", sa.text("lower(target)")],
        unique=True,
    )

    op.create_table(
        "packs",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("level", sa.Text(), nullable=True),
        sa.Column(
            "position", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
    )

    op.create_table(
        "pack_cards",
        sa.Column(
            "pack_id",
            sa.Text(),
            sa.ForeignKey("packs.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "card_id",
            sa.Text(),
            sa.ForeignKey("cards.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "position", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
    )

    op.create_table(
        "user_cards",
        sa.Column(
            "user_id",
            sa.Text(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "card_id",
            sa.Text(),
            sa.ForeignKey("cards.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column(
            "added_at",
            sa.TIMESTAMP(),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "source_pack",
            sa.Text(),
            sa.ForeignKey("packs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("due_at", sa.TIMESTAMP(), nullable=True),
        sa.Column("interval_days", sa.Integer(), nullable=True),
        sa.Column("reps", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_score", sa.Integer(), nullable=True),
    )
    op.create_index("ix_user_cards_user_due", "user_cards", ["user_id", "due_at"])


def downgrade() -> None:
    op.drop_index("ix_user_cards_user_due", table_name="user_cards")
    op.drop_table("user_cards")
    op.drop_table("pack_cards")
    op.drop_table("packs")
    op.drop_index("uq_cards_type_target_lower", table_name="cards")
    op.drop_table("cards")
