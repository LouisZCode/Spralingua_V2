"""users.level — the learner's self-declared CEFR bucket (LEVEL-001)

Rounds were assembled with no notion of where the learner is, so a B1
learner got A1 items (present-tense verb endings, sein-vs-haben) mixed into
every Flow. This column is the input to the serving rule in
``grammar/levels.py``: the level sets the ceiling, the ``user_errors``
ledger sets the floor.

Three buckets — ``A1`` / ``A2`` / ``B1+`` — not six. A self-declared level
is only accurate to about that resolution, and the taxonomy tops out at B1.

**Nullable on purpose, with no default.** NULL means "not asked yet", and
``grammar.levels.select()`` serves NULL-level users everything, exactly as
before this release. Backfilling every existing account to a guessed level
would silently narrow the rounds of learners who never asked for it — the
question gets asked in the UI instead, and until it is answered nothing
about their experience changes.

Revision ID: 0021_users_level
Revises: 0020_unbench_all_cards
Create Date: 2026-08-14
"""

import sqlalchemy as sa
from alembic import op


revision = "0021_users_level"
down_revision = "0020_unbench_all_cards"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("level", sa.Text(), nullable=True))
    # Cheap guard against a typo'd write reaching the serving rule, where a
    # junk value would silently disable filtering for that user.
    op.create_check_constraint(
        "ck_users_level",
        "users",
        "level IS NULL OR level IN ('A1', 'A2', 'B1+')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_users_level", "users", type_="check")
    op.drop_column("users", "level")
