"""users.level bucket widening — B1+ splits into B1 / B2+ (LEVEL-002)

LEVEL-001 shipped three self-declared buckets — ``A1`` / ``A2`` / ``B1+`` —
collapsing B1 and B2 because the grammar taxonomy tops out at B1. That
collapse turned out too coarse for CONTENT-leveled exercises (Briefkasten
seeds, Szenario question tiers) and judge calibration, which do want a B1
vs. B2+ distinction even though the taxonomy-derived gating in
``grammar/levels.py`` still serves both identically today. ``BUCKETS`` is
now ``("A1", "A2", "B1", "B2+")`` — see that module for the full rationale.

This migration only degrades existing rows: every account that self-declared
"B1+" under the old scheme is remapped to the new "B1" bucket rather than
left on a value the app no longer recognizes as one of its four buckets.
Nobody is silently bumped up to "B2+" — B1 is the conservative read of what
"B1+" meant before this split existed.

Also widens ``ck_users_level`` (created in 0021) from the three-bucket set
to the four-bucket set, since the column's check constraint would otherwise
reject both the remapped rows and any future write of "B1" or "B2+".

Revision ID: 0023_level_buckets
Revises: 0022_audio_items
Create Date: 2026-08-21
"""

from alembic import op


revision = "0023_level_buckets"
down_revision = "0022_audio_items"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Order is load-bearing: the old constraint (0021) rejects 'B1', so it
    # must be dropped before the remap runs; the new constraint would reject
    # any lingering 'B1+' rows, so it must be recreated only after the remap
    # has cleared them out. drop -> remap -> recreate, in that order.
    op.drop_constraint("ck_users_level", "users", type_="check")
    op.execute("UPDATE users SET level = 'B1' WHERE level = 'B1+'")
    op.create_check_constraint(
        "ck_users_level",
        "users",
        "level IS NULL OR level IN ('A1', 'A2', 'B1', 'B2+')",
    )


def downgrade() -> None:
    # No-op: which rows were originally "B1+" vs. genuinely "B1" isn't
    # recoverable — the upgrade doesn't stash the pre-migration value
    # anywhere. The widened constraint also deliberately survives a
    # downgrade of this migration — 0021's own downgrade drops
    # ck_users_level by name regardless of which version created it, so the
    # chain still unwinds cleanly even though this step doesn't narrow it
    # back to three buckets.
    pass
