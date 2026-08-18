"""audio_items / audio_chunks: the personal audio pool (INTV-003 slice 1)

First content type registered into Postgres from the interview workbench
(``interview_local/``, git-excluded, local-only). ``audio_items`` is one row
per curated interview recording; ``audio_chunks`` is one row per approved,
edit-applied chunk within it. Both are written only by
``scripts/import_interviews.py`` — nothing else in the app writes them yet.
The mp3s themselves already live in the Railway bucket; this schema only
registers metadata (``storage_key``) pointing at them.

``audio_items.owner_user_id`` is nullable — NULL is reserved for a future
shared catalog — but every row the importer writes today sets it, so slice 1
is entirely "personal pool" content. CASCADE on delete like the other
per-user learning-state tables (``user_cards``, ``user_errors``): a personal
pool item is disposable, not audit-of-record like ``activity_session``.

``audio_chunks.transcript`` / ``segments`` are the POST-edit result of
applying the workbench's ``edits.json`` overrides — see
``interview_local/app.py``'s ``_merge_chunk_edits`` for the semantics the
importer mirrors (a deleted segment, override ``""``, is dropped from the
stored ``segments`` list entirely rather than kept with a hidden flag).

Pure additive migration; no existing rows or columns are touched.

Revision ID: 0022_audio_items
Revises: 0021_users_level
Create Date: 2026-08-18
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0022_audio_items"
down_revision = "0021_users_level"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audio_items",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("owner_user_id", sa.String(), nullable=True),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("level", sa.String(), nullable=True),
        sa.Column("language", sa.String(), nullable=False),
        sa.Column("storage_prefix", sa.String(), nullable=False, unique=True),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"], ["users.id"], ondelete="CASCADE"
        ),
    )
    op.create_index(
        "ix_audio_items_owner_user_id",
        "audio_items",
        ["owner_user_id"],
    )

    op.create_table(
        "audio_chunks",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("item_id", sa.String(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("source_chunk_id", sa.String(), nullable=False),
        sa.Column("storage_key", sa.String(), nullable=False),
        sa.Column("transcript", sa.Text(), nullable=False),
        # POST-edit list of {t0, t1, text}; deleted segments dropped.
        sa.Column("segments", postgresql.JSONB(), nullable=False),
        # briefs.json's entry verbatim, or NULL when never brief'd.
        sa.Column("brief", postgresql.JSONB(), nullable=True),
        sa.Column("duration_s", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(
            ["item_id"], ["audio_items.id"], ondelete="CASCADE"
        ),
    )
    op.create_index(
        "ix_audio_chunks_item_id",
        "audio_chunks",
        ["item_id"],
    )
    op.create_unique_constraint(
        "uq_audio_chunks_item_source_chunk",
        "audio_chunks",
        ["item_id", "source_chunk_id"],
    )


def downgrade() -> None:
    op.drop_table("audio_chunks")
    op.drop_table("audio_items")
