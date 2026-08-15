"""
TEST-001 Proposal-2: script the fixture recipe so nobody hand-writes the
INSERT … SELECT dance from CLAUDE.md's sim-harness section again.

    uv run python scripts/test_user.py create test-t001 --copy-from 0001 --copy-cards --level A2
    uv run python scripts/test_user.py destroy test-t001
    uv run python scripts/test_user.py list

Every id must start with "test-" — create/destroy refuse anything else, so
this can never be pointed at a real account (0001) by a typo. Pair with
SPRALINGUA_TEST_GUARD=1 (config/settings.py, database/repository.py) for a
second, code-level backstop on the same rule.

Reads DATABASE_URL from .env via config.settings and never prints it (or
any other secret) — see CLAUDE.md's "never print .env values" rule.
"""

import argparse
import asyncio
import sys
from pathlib import Path

# Run as `uv run python scripts/test_user.py …` from anywhere — make sure the
# repo root (not scripts/) is on sys.path so `config`/`database` resolve.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete, func, insert, literal, select, Text

from config import database_url
from database import dispose_engine, get_sessionmaker, init_engine
from database.orm import (
    ActivitySession,
    DrillAttempt,
    User,
    UserCard,
    UserDrillItem,
    UserError,
)

TEST_PREFIX = "test-"


def _require_test_id(user_id: str) -> None:
    if not user_id.startswith(TEST_PREFIX):
        raise SystemExit(
            f"Refusing: {user_id!r} does not start with {TEST_PREFIX!r}. "
            "This script only ever creates/destroys test-* accounts."
        )


async def _copy_rows(db, model, *, from_user: str, to_user: str) -> int:
    """INSERT … SELECT every ``from_user`` row of ``model``'s table into a
    copy owned by ``to_user``, substituting only the ``user_id`` column and
    keeping every other column verbatim. Generic over UserError/UserCard —
    both key on ``user_id`` as their first PK column."""
    table = model.__table__
    col_names = [c.name for c in table.columns]
    select_cols = [
        literal(to_user, type_=Text()) if c.name == "user_id" else c
        for c in table.columns
    ]
    stmt = insert(table).from_select(
        col_names,
        select(*select_cols).where(table.c.user_id == from_user),
    )
    result = await db.execute(stmt)
    return result.rowcount


async def create(user_id: str, *, copy_from: str | None, copy_cards: bool, level: str | None) -> None:
    _require_test_id(user_id)
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as db:
        if await db.get(User, user_id) is not None:
            raise SystemExit(f"{user_id!r} already exists — destroy it first or pick a new id.")
        # A typo'd --copy-from would otherwise "succeed" with 0 rows copied and
        # hand you an empty fixture that looks seeded (found by the T7 tester).
        if copy_from and await db.get(User, copy_from) is None:
            raise SystemExit(f"--copy-from {copy_from!r}: no such user — nothing created.")
        db.add(
            User(
                id=user_id,
                email=f"{user_id}@test.local",
                name=f"Test {user_id}",
                level=level,
            )
        )
        await db.flush()

        errors_copied = 0
        cards_copied = 0
        if copy_from:
            errors_copied = await _copy_rows(db, UserError, from_user=copy_from, to_user=user_id)
            if copy_cards:
                cards_copied = await _copy_rows(db, UserCard, from_user=copy_from, to_user=user_id)
        await db.commit()

    print(f"created {user_id!r} (level={level!r})")
    if copy_from:
        print(f"  copied {errors_copied} user_errors row(s) from {copy_from!r}")
        if copy_cards:
            print(f"  copied {cards_copied} user_cards row(s) from {copy_from!r}")


async def destroy(user_id: str) -> None:
    _require_test_id(user_id)
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as db:
        if await db.get(User, user_id) is None:
            print(f"{user_id!r} does not exist — nothing to do.")
            return

        # Counts for the report, read before the cascade fires.
        errors_n = await db.scalar(
            select(func.count()).select_from(UserError).where(UserError.user_id == user_id)
        )
        cards_n = await db.scalar(
            select(func.count()).select_from(UserCard).where(UserCard.user_id == user_id)
        )
        attempts_n = await db.scalar(
            select(func.count()).select_from(DrillAttempt).where(DrillAttempt.user_id == user_id)
        )

        # activity_session.user_id FK is ondelete="RESTRICT" — must go first.
        sessions_result = await db.execute(
            delete(ActivitySession).where(ActivitySession.user_id == user_id)
        )
        # user_drill_items.user_id FK has NO ondelete clause (default NO
        # ACTION, i.e. RESTRICT-like) — the only other non-cascading table,
        # so it also needs an explicit delete before the user row goes.
        drill_items_result = await db.execute(
            delete(UserDrillItem).where(UserDrillItem.user_id == user_id)
        )
        # The user row cascades user_cards, user_errors, drill_attempts,
        # daily_mode_completions (all ondelete="CASCADE" in orm.py), and
        # transitively user_verbformen (CASCADE via its composite FK onto
        # user_cards).
        await db.execute(delete(User).where(User.id == user_id))
        await db.commit()

    print(f"destroyed {user_id!r}")
    print(f"  deleted {sessions_result.rowcount} activity_session row(s) (explicit, FK RESTRICT)")
    print(f"  deleted {drill_items_result.rowcount} user_drill_items row(s) (explicit, FK has no ondelete)")
    print(
        f"  cascaded on user delete: {errors_n} user_errors, {cards_n} user_cards, "
        f"{attempts_n} drill_attempts (+ daily_mode_completions, user_verbformen)"
    )


async def list_test_users() -> None:
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as db:
        rows = (
            (
                await db.execute(
                    select(User).where(User.id.like(f"{TEST_PREFIX}%")).order_by(User.id)
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            print("no test-* users.")
            return
        for u in rows:
            sessions_n = await db.scalar(
                select(func.count()).select_from(ActivitySession).where(ActivitySession.user_id == u.id)
            )
            errors_n = await db.scalar(
                select(func.count()).select_from(UserError).where(UserError.user_id == u.id)
            )
            cards_n = await db.scalar(
                select(func.count()).select_from(UserCard).where(UserCard.user_id == u.id)
            )
            print(
                f"{u.id}\tlevel={u.level}\tsessions={sessions_n}\terrors={errors_n}\tcards={cards_n}"
            )


async def _run(args: argparse.Namespace) -> None:
    await init_engine(database_url)
    try:
        if args.command == "create":
            await create(args.user_id, copy_from=args.copy_from, copy_cards=args.copy_cards, level=args.level)
        elif args.command == "destroy":
            await destroy(args.user_id)
        elif args.command == "list":
            await list_test_users()
    finally:
        await dispose_engine()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create/destroy/list isolated test-* fixture users (TEST-001)."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_create = sub.add_parser("create", help="Create a test-* user, optionally seeded from a real account.")
    p_create.add_argument("user_id")
    p_create.add_argument(
        "--copy-from", default=None,
        help="Copy user_errors (and optionally user_cards) rows from this user id, e.g. 0001.",
    )
    p_create.add_argument(
        "--copy-cards", action="store_true",
        help="Also copy user_cards rows (only takes effect with --copy-from).",
    )
    p_create.add_argument("--level", default=None, help="CEFR level to set on the new user, e.g. A2.")

    p_destroy = sub.add_parser("destroy", help="Delete a test-* user and its rows.")
    p_destroy.add_argument("user_id")

    sub.add_parser("list", help="List all test-* users with row counts.")

    args = parser.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
