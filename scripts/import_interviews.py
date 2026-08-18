"""
INTV-003 slice 1: register curated interview audio from the local-only
workbench (``interview_local/``, git-excluded) into Postgres as the first
"personal pool" content — one ``audio_items`` row per interview recording,
one ``audio_chunks`` row per approved, edit-applied chunk within it.

    uv run python scripts/import_interviews.py --owner 0001 \\
        --data-dir /path/to/interview_local/data/chunks

    uv run python scripts/import_interviews.py --owner 0001 \\
        --data-dir /path/to/interview_local/data/chunks \\
        --only "aivisory-Interview-Irene-Ternes_2026-07-22_151411"

Only chunks whose review status is "approved" are imported. review.json can
carry ids not present in chunks.json (stale, left over from resegmentation)
-- this always iterates chunks.json's own chunk list and looks up review
status by id, so a stale review.json id is silently ignored rather than
counted either way.

Transcript/segments mirror interview_local/app.py's edit-application
semantics (_merge_chunk_edits / _merge_segment_edit) exactly, with one
deliberate difference: a deleted segment (an edits.json override of "") is
dropped from the stored segments list entirely, rather than kept with a
"hidden" flag -- there is no edit-review UI on the Postgres side, so there
is nothing to preserve it for.

Idempotent per dir: if an audio_items row already exists for a dir's
storage_prefix, the whole dir is skipped with a message -- no partial
updates. One transaction per dir, so a mid-dir failure leaves nothing
partially imported for that dir.

Reads DATABASE_URL from .env via config.settings by default (override with
--database-url, which is what will later let this same script run against
prod) and never prints it, per CLAUDE.md's "never print .env values" rule.
"""

import argparse
import asyncio
import json
import re
import sys
import uuid
from pathlib import Path

# Run as `uv run python scripts/import_interviews.py …` from anywhere — make
# sure the repo root (not scripts/) is on sys.path so `config`/`database`
# resolve.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from config import database_url as _settings_database_url
from database import dispose_engine, get_sessionmaker, init_engine
from database.orm import AudioChunk, AudioItem, User

# Trailing "_YYYY-MM-DD_HHMMSS" stamp every workbench dir name carries.
_TIMESTAMP_SUFFIX_RE = re.compile(r"_\d{4}-\d{2}-\d{2}_\d{6}$")


def _title_from_dir(dir_name: str) -> str:
    """Dir name with the trailing "_YYYY-MM-DD_HHMMSS" timestamp stripped."""
    return _TIMESTAMP_SUFFIX_RE.sub("", dir_name)


def _load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _load_optional_json(path: Path) -> dict:
    """Best-effort load; missing/corrupt -> empty dict. Same fallback shape
    as interview_local/app.py's _load_review/_load_edits/_load_briefs/etc."""
    if not path.exists():
        return {}
    try:
        return _load_json(path)
    except (json.JSONDecodeError, OSError):
        return {}


def merge_chunk_edit(chunk: dict, chunk_edit: dict | None) -> tuple[str, list[dict]]:
    """Mirror interview_local/app.py's _merge_chunk_edits / _merge_segment_edit.

    Returns (transcript, segments): segments is the POST-edit list of
    {t0, t1, text}. A segment explicitly deleted by an edits.json override
    of "" is dropped from the list entirely (app.py instead keeps it with a
    "hidden" flag for its own edit-review UI; there is no equivalent on the
    Postgres side, so there is nothing to preserve it for). transcript is
    the " ".join of the surviving segments' effective text -- exactly
    app.py's chunk-level "text" recompute -- EXCEPT when the chunk has no
    edits.json entry at all, in which case app.py passes the chunk through
    unchanged and this returns chunks.json's original "text" field verbatim.
    """
    seg_overrides = (chunk_edit or {}).get("segments") or {}
    segments_in = chunk.get("segments") or []

    if not seg_overrides:
        transcript = chunk.get("text") or ""
        segments_out = [
            {"t0": s.get("t0"), "t1": s.get("t1"), "text": s.get("text")}
            for s in segments_in
        ]
        return transcript, segments_out

    segments_out = []
    effective_texts = []
    for i, seg in enumerate(segments_in):
        override = seg_overrides.get(str(i))
        if override is not None:
            text = override
            deleted = override == ""
        else:
            text = seg.get("text")
            deleted = False
        if deleted:
            continue
        segments_out.append({"t0": seg.get("t0"), "t1": seg.get("t1"), "text": text})
        if text:
            effective_texts.append(text)

    transcript = " ".join(effective_texts)
    return transcript, segments_out


def _iter_source_dirs(data_dir: Path, only: list[str] | None) -> list[Path]:
    if only:
        dirs = []
        for name in only:
            d = data_dir / name
            if not d.is_dir():
                raise SystemExit(f"--only {name!r}: no such dir under {data_dir}")
            dirs.append(d)
        return dirs
    return sorted(p for p in data_dir.iterdir() if p.is_dir())


async def _import_dir(db, rec_dir: Path, owner_user_id: str) -> str:
    """Import one workbench dir. Adds rows to `db` but never commits --
    the caller commits (or lets the session close and discard on error) so
    the whole dir is one transaction. Returns a one-line summary."""
    dir_name = rec_dir.name
    storage_prefix = f"interviews/{dir_name}"

    existing = await db.scalar(
        select(AudioItem).where(AudioItem.storage_prefix == storage_prefix)
    )
    if existing is not None:
        return f"{dir_name}: skipped, already imported (item_id={existing.id})"

    chunks_path = rec_dir / "chunks.json"
    if not chunks_path.exists():
        raise SystemExit(f"{dir_name}: no chunks.json — cannot import.")

    data = _load_json(chunks_path)
    chunks = data.get("chunks", [])
    review = _load_optional_json(rec_dir / "review.json")
    edits = _load_optional_json(rec_dir / "edits.json")
    briefs = _load_optional_json(rec_dir / "briefs.json")
    meta = _load_optional_json(rec_dir / "meta.json")

    language = data.get("language")
    if not language:
        raise SystemExit(f"{dir_name}: chunks.json has no language — cannot import.")

    level = meta.get("level")
    level = level if isinstance(level, str) else None

    item_id = uuid.uuid4().hex
    db.add(
        AudioItem(
            id=item_id,
            owner_user_id=owner_user_id,
            title=_title_from_dir(dir_name),
            level=level,
            language=language,
            storage_prefix=storage_prefix,
            source="workbench",
        )
    )
    # Flush the parent row before adding chunks — the chunk inserts are
    # batched (executemany) and need the item row already visible for the
    # FK check, same reasoning as scripts/test_user.py's create().
    await db.flush()

    imported = 0
    rejected = 0
    briefs_missing = 0
    position = 0
    for c in chunks:
        chunk_id = c.get("id")
        status = review.get(chunk_id, {}).get("status")
        if status != "approved":
            rejected += 1
            continue

        position += 1
        transcript, segments = merge_chunk_edit(c, edits.get(chunk_id))
        brief = briefs.get(chunk_id)
        if brief is None:
            briefs_missing += 1

        db.add(
            AudioChunk(
                id=uuid.uuid4().hex,
                item_id=item_id,
                position=position,
                source_chunk_id=chunk_id,
                storage_key=f"{storage_prefix}/{chunk_id}.mp3",
                transcript=transcript,
                segments=segments,
                brief=brief,
                duration_s=c.get("duration_s"),
            )
        )
        imported += 1

    return (
        f"{dir_name}: imported={imported} rejected_skipped={rejected} "
        f"briefs_missing={briefs_missing} (item_id={item_id})"
    )


async def run(owner: str, data_dir: Path, only: list[str] | None, db_url: str) -> None:
    await init_engine(db_url)
    try:
        sessionmaker = get_sessionmaker()

        async with sessionmaker() as db:
            if await db.get(User, owner) is None:
                raise SystemExit(f"--owner {owner!r}: no such user — nothing imported.")

        for rec_dir in _iter_source_dirs(data_dir, only):
            async with sessionmaker() as db:
                summary = await _import_dir(db, rec_dir, owner)
                await db.commit()
            print(summary)
    finally:
        await dispose_engine()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import curated interview audio from the local workbench into Postgres (INTV-003 slice 1)."
    )
    parser.add_argument("--owner", required=True, help="users.id to own the imported items, e.g. 0001.")
    parser.add_argument(
        "--data-dir", required=True, type=Path,
        help="Path to interview_local/data/chunks.",
    )
    parser.add_argument(
        "--only", action="append", default=None,
        help="Import only this dir name (repeatable). Default: every dir under --data-dir.",
    )
    parser.add_argument(
        "--database-url", default=None,
        help="Override DATABASE_URL (default: from .env via config.settings).",
    )
    args = parser.parse_args()

    db_url = args.database_url or _settings_database_url
    if not db_url:
        raise SystemExit("No DATABASE_URL available (not in .env and --database-url not given).")

    data_dir = args.data_dir
    if not data_dir.is_dir():
        raise SystemExit(f"--data-dir {data_dir}: not a directory.")

    asyncio.run(run(args.owner, data_dir, args.only, db_url))


if __name__ == "__main__":
    main()
