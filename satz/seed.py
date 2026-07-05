"""Startup sync of curated YAML packs → Postgres (SATZ-002).

Called from the FastAPI lifespan right after ``init_engine``, so editing a
pack file + restarting the backend is the whole publish flow. Idempotent:

- curated ``cards`` rows are upserted on id (content fields refreshed from
  YAML; ``source``/``first_added_by``/``created_at`` are left alone, so
  future ``community`` rows are never touched — YAML ids can't collide with
  them anyway, but the update set makes it structurally impossible);
- each pack's membership (``pack_cards``) is replaced wholesale in YAML order;
- packs whose YAML file was removed are deleted (memberships cascade;
  ``user_cards.source_pack`` goes NULL — pool rows survive their pack).

Fail-loud: any exception here aborts startup, same as an unreachable DB.

Standalone run (no backend):  uv run python -m satz.seed
"""

import asyncio

from loguru import logger
from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert as pg_insert

from database.connection import get_sessionmaker
from database.orm import Pack, PackCard, VocabCard

from .content import load_packs

# Everything the YAML owns on a curated card — refreshed on every sync.
_CARD_CONTENT_FIELDS = (
    "type",
    "target",
    "article",
    "reflexive",
    "gloss",
    "note",
    "tense",
    "tense_form",
    "example",
    "level",
)


async def sync_curated_content() -> None:
    packs, cards_by_id = load_packs()

    async with get_sessionmaker()() as db:
        for card in cards_by_id.values():
            values = {
                "id": card["id"],
                "source": "curated",
                **{f: card.get(f, False if f == "reflexive" else None) for f in _CARD_CONTENT_FIELDS},
            }
            stmt = pg_insert(VocabCard).values(**values)
            await db.execute(
                stmt.on_conflict_do_update(
                    index_elements=["id"],
                    set_={f: getattr(stmt.excluded, f) for f in _CARD_CONTENT_FIELDS},
                )
            )

        for pack in packs:
            pack_stmt = pg_insert(Pack).values(
                id=pack["id"],
                title=pack["title"],
                description=pack["description"],
                kind=pack["kind"],
                level=pack["level"],
                position=pack["position"],
            )
            await db.execute(
                pack_stmt.on_conflict_do_update(
                    index_elements=["id"],
                    set_={
                        f: getattr(pack_stmt.excluded, f)
                        for f in ("title", "description", "kind", "level", "position")
                    },
                )
            )
            # Replace membership wholesale — YAML order is display order.
            await db.execute(delete(PackCard).where(PackCard.pack_id == pack["id"]))
            for pos, cid in enumerate(pack["card_ids"]):
                db.add(PackCard(pack_id=pack["id"], card_id=cid, position=pos))

        # Packs whose YAML file was removed disappear from the gallery.
        await db.execute(delete(Pack).where(Pack.id.notin_([p["id"] for p in packs])))

        await db.commit()

    logger.info(
        f"Satz content synced: {len(packs)} packs, {len(cards_by_id)} curated cards"
    )


async def _main() -> None:
    """Standalone entrypoint: bring up the engine, sync, tear down."""
    from config import database_url
    from database.connection import dispose_engine, init_engine

    await init_engine(database_url)
    try:
        await sync_curated_content()
    finally:
        await dispose_engine()


if __name__ == "__main__":
    asyncio.run(_main())
