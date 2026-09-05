"""voice_recordings — WHO/WHEN/WHAT-exercise ledger for the voice bucket (REL-001).

Luis's decision 2026-09-05: start recording each learner's voice — spoken
drill attempts AND voice-session audio — into a new bucket of its own, to
improve grading and personalize practice later. This table is the DB half:
one row per clip, linking the learner, the timestamp, and the exercise it
came from to the object's key in the (separate, not-yet-created) voice
bucket. The bucket itself is a Railway dashboard step; `config/settings.py`'s
five `VOICE_BUCKET_*` vars and `recordings/store.py` are what actually
write objects there, both already inert-by-default (absent vars -> no rows,
no objects, one warning).

`ref_kind` + `ref_id` point back at whichever record actually graded/used
the clip — never an FK, because the ref kinds name different tables (and
several are not a DB row at all, just the closest stable identifier the
clip has). Free text, not a DB enum/CHECK constraint, so a new kind never
needs a migration:

  - `drill_attempt` -> `drill_attempts.id` (BigInteger PK) as text.
  - `activity_session` -> `activity_session.id` (the bare session hex).
  - `teacher_exercise` -> the Clara exercise item's own id (no DB row —
    `teacher/routes.py`'s attempts-audio route is deliberately exempt from
    every ledger/attempt-log write; the recording is the one thing about
    that room this migration lets through, and that carve-out is spelled
    out at the route itself).
  - `interview_comprehension` -> the interview chunk id (round 1, "listen
    & retell" — writes no persisted grading row of its own by design, so
    the chunk id is the only stable thing to point at).
  - `interview_answer` -> the interview chunk id (round 2, "read &
    answer" — used for EVERY answer, clean or with a harvested slip; a
    harvested slip's own `drill_attempts` row is intentionally NOT linked
    here — join by `user_id` + `created_at` + the chunk id instead, since
    `drill_attempts.session_id`/`item_ref` already carry that context).
  - `satz_rehearsal` -> the Satzschmiede card id (a `rehearsal=True`
    attempt writes no `drill_attempts` row by design — SATZ-015 — so the
    card id is what the clip has to point at instead).

`user_id` is CASCADE, matching every other per-user learning-state table
(`user_cards`, `user_errors`, `coin_ledger`, ...) rather than
`activity_session`'s RESTRICT — a destroyed test fixture (`scripts/test_user.py
destroy`) must take its recordings with it with no special-casing, same as
the coin ledger.

Follows 0025_coins.py's style: sync psycopg2 (Alembic), Text primary key
minted app-side (`uuid4().hex`, see `recordings/service.py`), plain
`sa.Column`/`op.create_table`, downgrade included.

Revision ID: 0026_voice_recordings
Revises: 0025_coins
Create Date: 2026-09-05
"""

from alembic import op
import sqlalchemy as sa

revision = "0026_voice_recordings"
down_revision = "0025_coins"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "voice_recordings",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Text(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        # "satz" | "sprechen" | "szenario" | "interview" | "teacher" |
        # "tandem" | "lesson" | "conversation" — content-as-data, not an
        # enum, same choice as users.tier / drill_attempts.exercise.
        sa.Column("surface", sa.Text(), nullable=False),
        # The drill id / lesson id / teacher `drill` value this clip
        # belongs to. Nullable — not every surface has one worth stamping.
        sa.Column("exercise", sa.Text(), nullable=True),
        # "drill_attempt" | "activity_session" | "teacher_exercise" |
        # "interview_comprehension" | "interview_answer" | "satz_rehearsal"
        # -- free text, not an enum/CHECK constraint (see the module
        # docstring above).
        sa.Column("ref_kind", sa.Text(), nullable=False),
        sa.Column("ref_id", sa.Text(), nullable=False),
        # card id / item id / chunk id, when the surface has one.
        sa.Column("item_id", sa.Text(), nullable=True),
        sa.Column("bucket_key", sa.Text(), nullable=False),
        sa.Column("content_type", sa.Text(), nullable=False),
        sa.Column("bytes", sa.Integer(), nullable=False),
        # Filled when cheaply known; most upload sites don't decode the clip
        # just to measure it, so this stays NULL far more often than not.
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        # The STT text the judge saw, when cheap to pass along — lets a
        # later evaluation replay judge-vs-audio without a second
        # transcription pass.
        sa.Column("transcript", sa.Text(), nullable=True),
        sa.UniqueConstraint("bucket_key", name="uq_voice_recordings_bucket_key"),
    )
    op.create_index(
        "ix_voice_recordings_user_created", "voice_recordings", ["user_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_voice_recordings_user_created", table_name="voice_recordings")
    op.drop_table("voice_recordings")
