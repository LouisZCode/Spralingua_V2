"""HTTP routes for Satzschmiede (SATZ-002, Phase 1: browse packs + read deck).

Every route resolves the caller via the session JWT (``get_current_user_id``)
and gets one ``AsyncSession`` per request (``get_db``) — the two dependencies
AUTH-001/DATA-001 left ready for exactly this. CORS is app-wide in ``main.py``,
so the Next.js origin needs no extra wiring here.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import and_, func, literal, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from auth.deps import get_current_user_id
from database.connection import get_db
from database.orm import Pack, PackCard, User, UserCard, VocabCard

router = APIRouter(prefix="/satz", tags=["satzschmiede"])


def _card_payload(c: VocabCard) -> dict:
    """Serialize to the frontend ``Card`` contract (deck.ts). Optional fields
    are omitted rather than sent as null so ``card.note && …`` guards and
    ``??`` fallbacks behave identically to the old mock objects."""
    payload: dict = {
        "id": c.id,
        "type": c.type,
        "target": c.target,
        "gloss": c.gloss,
    }
    if c.article:
        payload["article"] = c.article
    if c.reflexive:
        payload["reflexive"] = True
    if c.note:
        payload["note"] = c.note
    if c.example:
        payload["example"] = c.example
    if c.level:
        payload["level"] = c.level
    return payload


@router.get("/packs")
async def list_packs(
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """The pack gallery: every pack with its size and how much of it the
    caller already owns (drives the "Added ✓ / 4 of 10" states)."""
    rows = (
        await db.execute(
            select(
                Pack,
                func.count(PackCard.card_id).label("card_count"),
                func.count(UserCard.card_id).label("owned_count"),
            )
            .outerjoin(PackCard, PackCard.pack_id == Pack.id)
            .outerjoin(
                UserCard,
                and_(
                    UserCard.card_id == PackCard.card_id,
                    UserCard.user_id == user_id,
                ),
            )
            .group_by(Pack.id)
            .order_by(Pack.position, Pack.id)
        )
    ).all()
    return {
        "packs": [
            {
                "id": p.id,
                "title": p.title,
                "description": p.description,
                "kind": p.kind,
                "level": p.level,
                "cardCount": card_count,
                "ownedCount": owned_count,
            }
            for p, card_count, owned_count in rows
        ]
    }


@router.post("/packs/{pack_id}/add")
async def add_pack(
    pack_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Add every card of a pack to the caller's pool.

    One INSERT…SELECT with ``ON CONFLICT DO NOTHING`` — re-adding a pack (or
    overlapping packs sharing a card) silently skips what's already owned,
    so the endpoint is idempotent and race-safe without a SELECT-then-INSERT.
    """
    if await db.get(Pack, pack_id) is None:
        raise HTTPException(status_code=404, detail="unknown pack")

    # A valid JWT implies /auth/google upserted the user, but a dev DB wipe
    # can outlive a token — the same idempotent no-op guard as session rows.
    await db.execute(
        pg_insert(User).values(id=user_id).on_conflict_do_nothing(index_elements=["id"])
    )

    result = await db.execute(
        pg_insert(UserCard)
        .from_select(
            ["user_id", "card_id", "source_pack"],
            select(literal(user_id), PackCard.card_id, literal(pack_id)).where(
                PackCard.pack_id == pack_id
            ),
        )
        .on_conflict_do_nothing(index_elements=["user_id", "card_id"])
    )
    await db.commit()

    pool_size = await db.scalar(
        select(func.count()).select_from(UserCard).where(UserCard.user_id == user_id)
    )
    return {"added": result.rowcount, "poolSize": pool_size}


@router.get("/deck")
async def get_deck(
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """The caller's pool, oldest-added first. Phase 1 serves the whole pool;
    the scheduler phase will narrow this to cards that are due."""
    cards = (
        (
            await db.execute(
                select(VocabCard)
                .join(UserCard, UserCard.card_id == VocabCard.id)
                .where(UserCard.user_id == user_id)
                .order_by(UserCard.added_at, VocabCard.id)
            )
        )
        .scalars()
        .all()
    )
    return {"cards": [_card_payload(c) for c in cards]}
