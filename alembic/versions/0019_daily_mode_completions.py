"""daily_mode_completions: per-mode day-earning ledger (GAME-001 v2)

GAME-001 shipped a streak that any single graded drill attempt, or any
finished conversation, credited. The owner tightened it: a day is now
*earned* only by completing 3 of the 4 learner-facing practice modes shown
on ``/practice`` (Satzschmiede, Flow, Tandem, Briefkasten) — one attempt in
one mode is no longer enough. This table is the per-mode ledger that makes
that countable: one row per ``(user_id, day, mode)`` the learner actually
completed, written by ``database.repository.complete_daily_mode`` from each
mode's own completion point (frontend POST for satz/flow, server-side for
tandem at disconnect and briefkasten at the second attempt). Once a day has
rows for >= 3 distinct modes, ``complete_daily_mode`` calls the existing
``credit_streak_day`` (0017) to advance the ``users`` streak cache.

The composite primary key ``(user_id, day, mode)`` both enforces "one
completion per mode per day" (the write is `ON CONFLICT DO NOTHING`, so
completing a mode twice in one day is a no-op, not a duplicate row) and,
since ``(user_id, day)`` is the PK's leading prefix, already serves the
"how many modes did this user finish today" lookup — no separate index
needed.

``mode`` is plain ``Text``, not an enum type — same content-as-data choice
already made for ``user_errors.pattern_id``: the four slugs
(``satz``/``flow``/``tandem``/``briefkasten``) are validated in code
(``database.repository.STREAK_MODES``), not by the schema.

Days bucket by UTC calendar day — same documented limitation as 0017's
``users`` streak columns and ``stats/routes.py``'s ``today`` window: a
learner west of UTC sees their day roll over mid-evening local time,
pending a per-user timezone column.

Pure additive migration; no existing rows or columns are touched.

Revision ID: 0019_daily_mode_completions
Revises: 0018_users_email_lower
Create Date: 2026-08-07
"""

from alembic import op
import sqlalchemy as sa


revision = "0019_daily_mode_completions"
down_revision = "0018_users_email_lower"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "daily_mode_completions",
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "day", "mode"),
    )


def downgrade() -> None:
    op.drop_table("daily_mode_completions")
