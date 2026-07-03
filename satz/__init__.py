"""Satzschmiede — vocabulary practice module (SATZ-002).

Curated content lives in ``satz/packs/*.yaml`` (authored via the
``satz-cards`` skill), is synced into Postgres at startup by ``seed.py``,
and is served per-user by the ``/satz`` router in ``routes.py``.
"""

from .routes import router
from .seed import sync_curated_content

__all__ = ["router", "sync_curated_content"]
