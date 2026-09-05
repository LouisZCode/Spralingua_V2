"""Async engine + session factory for the Spralingua DB (DATA-001).

Singleton pattern matches the rest of this codebase (loguru, OTel tracer,
``ACTIVE_TASKS`` dict): module-level state initialized at FastAPI startup
via ``init_engine(...)``. ``pipeline/factory.py`` (not a FastAPI route,
never sees the request object) imports ``get_sessionmaker()`` directly;
HTTP routes use ``get_db`` via ``Depends`` if/when read endpoints land.

``init_engine`` runs ``SELECT 1`` so misconfiguration fails loud at startup
rather than producing silent broken persistence at runtime.
"""

import os
from collections.abc import AsyncIterator

from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


async def init_engine(database_url: str) -> None:
    """Build the async engine + sessionmaker; verify connectivity.

    Called from the FastAPI lifespan on app startup. Failure to reach
    Postgres here raises — the lifespan crash propagates and uvicorn exits
    non-zero, which is the locked behavior.
    """
    global _engine, _sessionmaker
    if _engine is not None:
        logger.warning("init_engine called twice; ignoring second invocation")
        return

    if not database_url:
        raise RuntimeError(
            "DATABASE_URL is not set. Add it to .env "
            "(form: postgresql+asyncpg://user:password@host:port/dbname)."
        )

    # CHORE-001: env-configurable so a deployment can size the pool without a
    # code edit; same os.environ.get(...) + int(...) pattern as config/settings.py.
    pool_size = int(os.environ.get("DB_POOL_SIZE", "5"))
    max_overflow = int(os.environ.get("DB_MAX_OVERFLOW", "5"))

    _engine = create_async_engine(
        database_url,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_pre_ping=True,
        # Bound how long a request waits for a pooled connection, and how
        # long a query / new connection can run, so a hung Postgres can't
        # stall request or cleanup coroutines indefinitely.
        pool_timeout=30,
        connect_args={
            "command_timeout": 30,
            "timeout": 10,
            # DBFIX-5: asyncpg passes these through verbatim as Postgres
            # session GUCs (both accepted as strings). application_name
            # makes every backend connection identifiable in
            # pg_stat_activity; statement_timeout mirrors command_timeout
            # above so a runaway query is bounded at the DB too, not just
            # at the client; idle_in_transaction_session_timeout is set
            # well above the longest provider round-trip a route can hold
            # a session open across. REL-005 restructured the sites that
            # held one for learners (briefkasten, bauteil, faelle, both
            # interview routes, teacher/dealer), but satz / verbformen /
            # sprechen / genus still run STT + judge inside a
            # Depends(get_db) request: a learner's coin-gate commit has
            # already released the connection by then, a developer's
            # bypass never commits, so a developer caller keeps one
            # checked out for up to ~25 s (12 s a leg, one retry) — still
            # a quarter of this bound (DB-001). So it only fires on a
            # genuinely leaked/forgotten transaction, never a
            # slow-but-legitimate one, and it exists so that leak can
            # never pin a pooled connection forever.
            "server_settings": {
                "application_name": "spralingua-backend",
                "statement_timeout": "30000",
                "idle_in_transaction_session_timeout": "120000",
            },
        },
    )
    _sessionmaker = async_sessionmaker(
        bind=_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async with _engine.connect() as conn:
        await conn.execute(text("SELECT 1"))

    logger.info("Database engine initialized")


async def dispose_engine() -> None:
    """Tear down the engine on app shutdown."""
    global _engine, _sessionmaker
    if _engine is None:
        return
    await _engine.dispose()
    _engine = None
    _sessionmaker = None
    logger.info("Database engine disposed")


def get_engine() -> AsyncEngine:
    if _engine is None:
        raise RuntimeError("Engine not initialized — did the FastAPI lifespan run?")
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    if _sessionmaker is None:
        raise RuntimeError("Sessionmaker not initialized — did the FastAPI lifespan run?")
    return _sessionmaker


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding one ``AsyncSession`` per request.

    Not used by the WebSocket / pipeline path (that imports ``get_sessionmaker``
    directly). Kept here so future HTTP read routes can write
    ``db: AsyncSession = Depends(get_db)`` without scaffolding.
    """
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as db:
        yield db
