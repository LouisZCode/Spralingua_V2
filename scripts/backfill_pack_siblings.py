"""CONT-003 backfill: "existing learners get no backfill".

Since ``892e3f2`` the six curated packs carry 23 spoken-past sibling cards,
but only ``POST /satz/packs/{id}/add`` writes ``user_cards`` — a learner who
added a pack BEFORE that commit owns a partial pack (missing the sibling
cards' ``user_cards`` links) and so has an empty Verbformen deck for it.

This script completes the VERB PAIRS of packs a learner already chose —
nothing broader. For every user (or one ``--user``), it finds each pack the
learner has at least one ``user_cards`` link into (``source_pack`` set to
that pack) and inserts the missing links for that pack's ``tense='past'``
sibling cards whose present-tense base verb (same ``type='verb'``, same
``lower(target)``, ``tense IS NULL`` — the match ``satz/routes.py::
_ensure_past_sibling`` uses) the learner still owns. Same idempotent
``ON CONFLICT DO NOTHING`` insert as ``add_pack`` (``user_id``, ``card_id``,
``source_pack``; ``added_at`` left to its ``now()`` default).

Why so narrow (review, 2026-09-06): ``DELETE /satz/deck/{card_id}`` is a hard
delete with no removal marker, so "every pack card the learner does not
own" is indistinguishable from "every pack card the learner threw away" —
a blanket completion silently re-added a deliberately removed noun. The
sibling cards are the ONLY cards ever added to a pack after learners had
already linked it (``892e3f2``), and a sibling whose base verb is gone was
either never wanted or removed with it, so this shape can only ever
restore the pair the design promised ("every verb brings its past form
along"). The residual it cannot see — a learner who added a pack AFTER
``892e3f2`` and then removed just the sibling while keeping the base —
gets that sibling back; two days of exposure at six accounts, accepted
and documented here.

Usage:
    uv run python scripts/backfill_pack_siblings.py                        # dry run, all users
    uv run python scripts/backfill_pack_siblings.py --user test-scripts-2  # dry run, one user
    uv run python scripts/backfill_pack_siblings.py --apply --user test-scripts-2

Dry run (default) only SELECTs and prints; ``--apply`` writes. Skips
``demo`` (its cards, if any, aren't a learner's own pack choice in the
usual sense). With ``SPRALINGUA_TEST_GUARD=1`` set, ``--apply`` refuses to
write for any non-``test-*`` user id — mirroring
``database/repository.py``'s ``_assert_test_user`` guard, which this script
also imports and calls directly (same guard ``satz/routes.py::add_pack``
uses for its own direct ``user_cards`` write) since this script's own
INSERT is raw SQLAlchemy and doesn't otherwise pass through that guard.

Standalone-ish like the other ``scripts/*.py`` DB tools: imports
``database``/``database.orm``/``database.repository`` and ``config`` only —
no FastAPI router import.
"""

import argparse
import asyncio
import sys
from pathlib import Path

# Run as `uv run python scripts/backfill_pack_siblings.py …` from anywhere —
# make sure the repo root (not scripts/) is on sys.path so `config`/
# `database` resolve.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, literal, select  # noqa: E402
from sqlalchemy.dialects.postgresql import insert as pg_insert  # noqa: E402
from sqlalchemy.orm import aliased  # noqa: E402

from config import database_url  # noqa: E402
from database import dispose_engine, get_sessionmaker, init_engine  # noqa: E402
from database.orm import PackCard, User, UserCard, VocabCard  # noqa: E402
from database.repository import _assert_test_user  # noqa: E402

_SKIP_USERS = {"demo"}


async def _packs_for_user(db, user_id: str) -> list[str]:
    """Distinct pack ids the learner has at least one ``user_cards`` link
    into (``source_pack`` set, not NULL — an individually-added card has no
    pack to complete)."""
    rows = await db.execute(
        select(UserCard.source_pack)
        .where(UserCard.user_id == user_id, UserCard.source_pack.isnot(None))
        .distinct()
    )
    return sorted(r[0] for r in rows.all())


def _missing_sibling_ids(user_id: str, pack_id: str):
    """SELECT of the pack's ``tense='past'`` sibling card ids the learner
    does not own but whose present base verb (same pack, same
    ``lower(target)``, ``tense IS NULL``) they DO own — see the module
    docstring for why nothing wider is safe."""
    Sibling = aliased(VocabCard)
    Base = aliased(VocabCard)
    BasePack = aliased(PackCard)
    owned = select(UserCard.card_id).where(UserCard.user_id == user_id)
    return (
        select(Sibling.id)
        .select_from(PackCard)
        .join(Sibling, Sibling.id == PackCard.card_id)
        .join(
            Base,
            (Base.type == "verb")
            & (Base.tense.is_(None))
            & (func.lower(Base.target) == func.lower(Sibling.target)),
        )
        .join(BasePack, (BasePack.card_id == Base.id) & (BasePack.pack_id == pack_id))
        .where(
            PackCard.pack_id == pack_id,
            Sibling.type == "verb",
            Sibling.tense == "past",
            Sibling.id.notin_(owned),
            Base.id.in_(owned),
        )
        .distinct()
    )


async def _missing_count(db, user_id: str, pack_id: str) -> int:
    return (
        await db.scalar(
            select(func.count()).select_from(
                _missing_sibling_ids(user_id, pack_id).subquery()
            )
        )
        or 0
    )


async def _apply_pack(db, user_id: str, pack_id: str) -> int:
    """Same idempotent insert as ``satz/routes.py::add_pack`` — same three
    columns, same ``ON CONFLICT (user_id, card_id) DO NOTHING`` key, and
    ``added_at`` left to the column's own server default rather than
    backdated — over the narrowed sibling set only."""
    missing = _missing_sibling_ids(user_id, pack_id).subquery()
    result = await db.execute(
        pg_insert(UserCard)
        .from_select(
            ["user_id", "card_id", "source_pack"],
            select(literal(user_id), missing.c.id, literal(pack_id)),
        )
        .on_conflict_do_nothing(index_elements=["user_id", "card_id"])
    )
    return result.rowcount


async def _user_ids(db, only_user: str | None) -> list[str]:
    if only_user:
        if only_user in _SKIP_USERS:
            raise SystemExit(f"--user {only_user!r} is skipped (not a learner's own pack pool).")
        if await db.get(User, only_user) is None:
            raise SystemExit(f"--user {only_user!r}: no such user.")
        return [only_user]
    rows = await db.execute(select(User.id).order_by(User.id))
    return [uid for (uid,) in rows.all() if uid not in _SKIP_USERS]


async def run(apply: bool, only_user: str | None) -> None:
    await init_engine(database_url)
    try:
        sessionmaker = get_sessionmaker()
        async with sessionmaker() as db:
            user_ids = await _user_ids(db, only_user)
            grand_total = 0
            for user_id in user_ids:
                pack_ids = await _packs_for_user(db, user_id)
                if apply and pack_ids:
                    # TEST-001: direct write, not via repository. Under the
                    # guard a non-test user is SKIPPED with a line, not a
                    # crash — the full-table run must still reach every
                    # fixture behind it (review, 2026-09-06).
                    try:
                        _assert_test_user(user_id)
                    except RuntimeError as exc:
                        print(f"{user_id}\tskipped\t{exc}")
                        continue
                for pack_id in pack_ids:
                    if apply:
                        added = await _apply_pack(db, user_id, pack_id)
                        await db.commit()
                        if added:
                            print(f"{user_id}\t{pack_id}\tadded {added}")
                    else:
                        added = await _missing_count(db, user_id, pack_id)
                        if added:
                            print(f"{user_id}\t{pack_id}\twould add {added}")
                    grand_total += added
            verb = "added" if apply else "would add"
            print(f"total {verb}: {grand_total}")
    finally:
        await dispose_engine()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "CONT-003 backfill: complete partial packs (missing spoken-past "
            "sibling user_cards links) for learners who added a pack before "
            "it carried siblings. Dry run by default."
        )
    )
    parser.add_argument(
        "--apply", action="store_true", help="Write the missing user_cards rows (default: dry run)."
    )
    parser.add_argument(
        "--user", default=None, help="Only operate on this one user id (default: every user)."
    )
    args = parser.parse_args()
    asyncio.run(run(args.apply, args.user))


if __name__ == "__main__":
    main()
