"""Database layer (DATA-001) — see ``database/orm.py`` for the schema."""

from .connection import (
    dispose_engine,
    get_db,
    get_engine,
    get_sessionmaker,
    init_engine,
)
from .orm import ActivitySession, Base, User
from .repository import create_session_row, finalize_session_row

__all__ = [
    "ActivitySession",
    "Base",
    "User",
    "create_session_row",
    "dispose_engine",
    "finalize_session_row",
    "get_db",
    "get_engine",
    "get_sessionmaker",
    "init_engine",
]
