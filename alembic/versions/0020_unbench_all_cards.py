"""user_cards: clear every leech bench (satz P3)

Leech benching (SATZ P2) — auto-shelving a card past LEECH_CAP lifetime
lapses onto the "Schwere Wörter" screen, reversible only via
``POST /satz/deck/{id}/unbench`` — is removed. Card dealing no longer reads
``benched_at`` at all (``_srs_payload`` has no "benched" status branch, and
the standalone/Flow queues never filtered on it beyond that status), so any
row still carrying a bench timestamp from before this release would sit
inert rather than genuinely returning to rotation. This is a one-time
backfill clearing that stale state so every existing card is live again.

The column itself (``user_cards.benched_at``) stays — dead but harmless,
no schema change here. A future migration can drop it once we're confident
nothing depends on it.

Pure data migration; no schema change.

Revision ID: 0020_unbench_all_cards
Revises: 0019_daily_mode_completions
Create Date: 2026-08-12
"""

from alembic import op


revision = "0020_unbench_all_cards"
down_revision = "0019_daily_mode_completions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE user_cards SET benched_at = NULL WHERE benched_at IS NOT NULL")


def downgrade() -> None:
    # No-op: which cards were benched, and when, isn't recoverable — the
    # upgrade doesn't stash the old timestamps anywhere.
    pass
