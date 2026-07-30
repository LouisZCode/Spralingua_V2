"""drill_attempts.word_ok: item-recall verdict for the new-word throttle

``correct`` on this table is the strict read — word AND grammar both ok —
which is right for accuracy stats but wrong for the SATZ dosing throttle:
the scheduler moves cards on ``word_ok`` alone (grammar slips elsewhere in
the sentence never touch the ladder), so gating new-word intake on the
stricter flag punished intake for a signal the schedule ignores. In
practice a learner with healthy ~70% word recall but rough sentence grammar
sat below the 80% floor almost every day and got no new words at all.

This column stores the item-recall verdict (word_ok) per attempt so the
throttle can judge what it actually doses: retrieval success of the target
word (Rowland 2014's success-rate band is about the practiced item, not
incidental grammar). Nullable — only satz populates it; every other
exercise has a single verdict and keeps recording ``correct`` alone. The
throttle reads COALESCE(word_ok, correct) so pre-migration rows still
count while the window rolls over.

Pure additive migration; no existing rows are touched.

Revision ID: 0014_drill_attempts_word_ok
Revises: 0013_user_cards_gloss_source
Create Date: 2026-07-30
"""

from alembic import op
import sqlalchemy as sa


revision = "0014_drill_attempts_word_ok"
down_revision = "0013_user_cards_gloss_source"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "drill_attempts", sa.Column("word_ok", sa.Boolean(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("drill_attempts", "word_ok")
