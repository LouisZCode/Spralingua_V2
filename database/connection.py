"""Async engine + session factory for the Spralingua DB (DATA-001).

Singleton pattern matches the rest of this codebase (loguru, OTel tracer,
``ACTIVE_TASKS`` dict): module-level state initialized at FastAPI startup
via ``init_engine(...)``. ``pipeline/factory.py`` (not a FastAPI route,
never sees the request object) imports ``get_sessionmaker()`` directly;
HTTP routes use ``get_db`` via ``Depends`` if/when read endpoints land.

``init_engine`` runs ``SELECT 1`` so misconfiguration fails loud at startup
rather than producing silent broken persistence at runtime.
"""

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

    _engine = create_async_engine(
        database_url,
        pool_size=5,
        max_overflow=5,
        pool_pre_ping=True,
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
