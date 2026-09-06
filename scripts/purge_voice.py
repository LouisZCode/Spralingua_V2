"""REL-001 operator tool: purge a learner's voice-bucket objects + their
``voice_recordings`` rows.

    uv run python scripts/purge_voice.py test-scripts-1                     # dry run
    uv run python scripts/purge_voice.py test-scripts-1 --apply             # purge
    uv run python scripts/purge_voice.py 0001 --i-know-this-is-a-real-learner --apply
    uv run python scripts/purge_voice.py demo --i-know-this-is-a-real-learner --apply

Standalone like ``scripts/pg_dump_to_bucket.py``: imports ``recordings.store``
+ ``config``/``config.settings`` + ``database`` only — no FastAPI router
import. ``recordings/__init__.py`` is deliberately kept free of heavy
imports for exactly this reason (see its module docstring), so
``import recordings.store`` here is safe, unlike ``interview.bucket`` (which
``pg_dump_to_bucket.py`` avoids importing because that pulls in
``interview/routes.py`` -> the full router stack -> ``agents.*`` at import
time).

Two purposes, one prefix:
  - account deletion — the bucket has no FK onto ``users``, so a
    ``DELETE FROM users`` never touches ``voice/{user_id}/`` on its own;
    this script is that missing step.
  - a retention purge for a learner who KEEPS their account — so ``--apply``
    deletes the ``voice_recordings`` rows explicitly rather than relying on
    the cascade a user delete would trigger.

Default is a DRY RUN: lists the keys under ``voice/{user_id}/`` (count +
total bytes, first 20 keys) and the matching ``voice_recordings`` row count,
then exits 0 without touching anything. ``--apply`` deletes the bucket
prefix FIRST, then the DB rows: the audio is the personal data, so if one
of the two steps is going to fail it must be the bookkeeping one. The cost
of that order is the transient it can leave — a crash between the two
steps leaves ``voice_recordings`` rows whose ``bucket_key`` no longer
exists (nothing serves those keys today, and the transcript column is
personal data too, so re-run ``--apply``: it reports ``0 object(s)`` and
then deletes the rows — proven in review, 2026-09-06).

Refuses any user id that doesn't start with ``"test-"`` unless
``--i-know-this-is-a-real-learner`` is also passed — real-account purges are
Luis's call (see CLAUDE.md's DB-001 note on account deletion); this script
is the tool, not the policy. ``"demo"`` needs the same flag — its objects
sit under ``voice/demo/`` like any other id (REL-001 follow-up).

Fails loud (real traceback, non-zero exit) on any DB or bucket error — this
is a hand-run operator tool, not a background job with the app's
never-raises contract; a caller needs to know when it didn't work.

Reads ``DATABASE_URL`` and the ``VOICE_BUCKET_*`` settings from ``.env`` via
``config``/``config.settings`` and never prints any of them, per this
repo's "never print .env values" rule.
"""

import argparse
import asyncio
import sys
from pathlib import Path

# Run as `uv run python scripts/purge_voice.py …` from anywhere — make sure
# the repo root (not scripts/) is on sys.path so `config`/`database`/
# `recordings` resolve.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete, func, select  # noqa: E402

from config import database_url  # noqa: E402
from database import dispose_engine, get_sessionmaker, init_engine  # noqa: E402
from database.orm import VoiceRecording  # noqa: E402
from recordings import store  # noqa: E402


def _require_authorized(user_id: str, allow_real: bool) -> None:
    """Same shape as ``scripts/test_user.py``'s ``_require_test_id``, but
    with an explicit escape hatch — unlike that script, this one has a
    real, intended use against non-test accounts (account deletion /
    retention purge), so the guard is a flag, not a hard refusal."""
    if user_id.startswith("test-") or allow_real:
        return
    raise SystemExit(
        f"Refusing: {user_id!r} is not a test-* id. Pass "
        "--i-know-this-is-a-real-learner to purge a real learner's (or "
        "demo's) voice recordings — this script is the tool, not the "
        "account-deletion policy."
    )


async def _matching_row_count(db, user_id: str) -> int:
    return (
        await db.scalar(
            select(func.count())
            .select_from(VoiceRecording)
            .where(VoiceRecording.user_id == user_id)
        )
        or 0
    )


async def _dry_run(user_id: str) -> None:
    prefix = f"voice/{user_id}/"
    objects = await store.list_prefix(prefix)
    if objects is None:
        raise SystemExit(
            f"purge_voice: could not list {prefix!r} — bucket not configured or the "
            "listing call failed (see the warning above). Refusing to report a "
            "possibly-wrong count."
        )
    total_bytes = sum(obj.get("Size", 0) for obj in objects)
    print(f"[dry-run] {prefix}: {len(objects)} object(s), {total_bytes} byte(s) total")
    for obj in objects[:20]:
        print(f"  {obj['Key']}  ({obj.get('Size', 0)} bytes)")
    if len(objects) > 20:
        print(f"  ... and {len(objects) - 20} more")

    sessionmaker = get_sessionmaker()
    async with sessionmaker() as db:
        row_count = await _matching_row_count(db, user_id)
    print(f"[dry-run] voice_recordings rows for {user_id!r}: {row_count}")
    print("[dry-run] no changes made. Pass --apply to delete.")


async def _apply(user_id: str) -> None:
    prefix = f"voice/{user_id}/"
    deleted_objects = await store.delete_prefix(prefix)
    if deleted_objects is None:
        raise SystemExit(
            f"purge_voice: bucket delete failed or not configured for {prefix!r} — "
            "aborting before touching the DB (see the warning above for the reason)."
        )
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as db:
        result = await db.execute(delete(VoiceRecording).where(VoiceRecording.user_id == user_id))
        await db.commit()
    print(f"purge_voice: deleted {deleted_objects} bucket object(s) under {prefix!r}")
    print(f"purge_voice: deleted {result.rowcount} voice_recordings row(s) for {user_id!r}")


async def run(user_id: str, apply: bool) -> None:
    await init_engine(database_url)
    try:
        if apply:
            await _apply(user_id)
        else:
            await _dry_run(user_id)
    finally:
        await dispose_engine()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Dry-run (default) or purge (--apply) a learner's voice-bucket "
            "objects + voice_recordings rows."
        )
    )
    parser.add_argument("user_id")
    parser.add_argument(
        "--apply", action="store_true", help="Actually delete (default is dry run)."
    )
    parser.add_argument(
        "--i-know-this-is-a-real-learner",
        action="store_true",
        dest="allow_real",
        help="Required to target a non-test-* id (including 'demo').",
    )
    args = parser.parse_args()
    _require_authorized(args.user_id, args.allow_real)
    asyncio.run(run(args.user_id, args.apply))


if __name__ == "__main__":
    main()
