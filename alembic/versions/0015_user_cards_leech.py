"""user_cards.lapses / benched_at: leech benching (SATZ P2)

Real usage on the expanding-interval ladder (satz/scheduler.py: 1/3/7/16/35
days, a miss quartering the interval) surfaced "leeches" — cards that keep
missing and cycling back to due-now forever instead of ever climbing. One
tracked word sat at 5/21 correct after 15+ lapses: every day it ate a review
slot and produced another failure, no different from Anki's own leech
problem on a fixed-interval ladder.

Anki's answer is to count lifetime lapses per card and auto-suspend ("leech
tag") once a threshold is crossed, so the deck stops grinding on a card the
learner isn't retaining and the learner can decide to re-approach it
deliberately. This migration adds the same two columns:

- ``lapses`` — a lifetime counter, incremented every time the schedule is
  already being punished (a graded word-miss in ``POST /satz/attempts``, a
  ``reveal``, or a ``gender-miss``) — the same three call sites that already
  quarter ``interval_days``. Nullable-free, defaults to 0 so every existing
  row starts unbenched.
- ``benched_at`` — NULL while the card is in normal rotation; stamped with
  the lapse that crossed the threshold (``LEECH_CAP`` in ``satz/routes.py``)
  and cleared by the learner's own choice (``POST /deck/{card_id}/unbench``).
  A benched card still rides in ``GET /deck`` (the "Schwere Wörter" shelf and
  browse-all need it) but reports ``status: "benched"`` instead of
  due/new/later, so the practice queue's status filter drops it out of
  rotation with no other schedule change.

Pure additive migration; no existing rows are touched (all read as
lapses=0, benched_at=NULL — correct, nothing before this shipped with a
lapse count).

Revision ID: 0015_user_cards_leech
Revises: 0014_drill_attempts_word_ok
Create Date: 2026-07-30
"""

from alembic import op
import sqlalchemy as sa


revision = "0015_user_cards_leech"
down_revision = "0014_drill_attempts_word_ok"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_cards",
        sa.Column("lapses", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "user_cards", sa.Column("benched_at", sa.TIMESTAMP(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("user_cards", "benched_at")
    op.drop_column("user_cards", "lapses")
