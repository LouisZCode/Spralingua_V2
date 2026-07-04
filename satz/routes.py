"""HTTP routes for Satzschmiede (SATZ-002 — Phase 1: browse packs + read deck;
Phase 2: forge your own word + remove a card from the pool; Phase 3: speak a
sentence, get an examiner verdict).

Every route resolves the caller via the session JWT (``get_current_user_id``)
and gets one ``AsyncSession`` per request (``get_db``) — the two dependencies
AUTH-001/DATA-001 left ready for exactly this. CORS is app-wide in ``main.py``,
so the Next.js origin needs no extra wiring here.
"""

import re

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import and_, delete, func, literal, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from auth.deps import get_current_user_id
from database.connection import get_db
from database.orm import Pack, PackCard, User, UserCard, VocabCard
from satz.content import _validate_card
from satz.enricher import enrich_word
from satz.examiner import examine_attempt, transcribe_attempt

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


# Leads a learner plausibly types along with the word itself. Stripping them
# lets "der Termin" / "sich freuen" hit the canonical catalog without an LLM
# call (targets are stored bare: article/reflexivity live in their own fields).
_LEADS = ("der ", "die ", "das ", "ein ", "eine ", "sich ")

_TRANSLIT = (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss"))
_ID_PREFIX = {"noun": "n", "verb": "v", "phrase": "p"}


async def _find_canonical(db: AsyncSession, word: str) -> VocabCard | None:
    """Match typed input against the canonical catalog on ``lower(target)``,
    with plausible leads stripped. Homograph pairs ("Essen"/"essen") share a
    lowercase form — prefer the row whose exact casing appears in the input."""
    terms = {word.lower()}
    for lead in _LEADS:
        if word.lower().startswith(lead) and len(word) > len(lead):
            terms.add(word.lower()[len(lead):].strip())
    rows = (
        (await db.execute(select(VocabCard).where(func.lower(VocabCard.target).in_(terms))))
        .scalars()
        .all()
    )
    for c in rows:
        if c.target in word:
            return c
    return rows[0] if rows else None


async def _fresh_id(db: AsyncSession, card_type: str, target: str) -> str:
    """Mint a community card id in the same shape as the pack YAML slugs
    ("n-feierabend"), suffixing on the rare id collision across targets."""
    s = target.lower()
    for a, b in _TRANSLIT:
        s = s.replace(a, b)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-") or "wort"
    base = f"{_ID_PREFIX[card_type]}-{s[:40]}"
    cid, n = base, 2
    while await db.get(VocabCard, cid) is not None:
        cid = f"{base}-{n}"
        n += 1
    return cid


async def _forge_card(db: AsyncSession, word: str, user_id: str) -> VocabCard:
    """Catalog miss → enrich via LLM, validate against the curated card rules,
    insert a ``community`` canonical row. Flushed but not committed — the
    caller commits together with the pool link."""
    try:
        enriched = await enrich_word(word)
    except Exception:
        logger.exception("Satz enrichment call failed for {!r}", word)
        raise HTTPException(
            status_code=502,
            detail="The word forge is unavailable right now — try again in a moment.",
        )
    if not enriched.valid or not enriched.target or not enriched.type:
        raise HTTPException(
            status_code=422,
            detail=enriched.reason or "That doesn't look like a German word or phrase.",
        )

    # The enricher normalizes ("Termine" → "Termin"), so re-check the dedup
    # seam (type, lower(target)) before inserting a duplicate canonical row.
    existing = await db.scalar(
        select(VocabCard).where(
            VocabCard.type == enriched.type,
            func.lower(VocabCard.target) == enriched.target.lower(),
        )
    )
    if existing is not None:
        return existing

    card = VocabCard(
        id=await _fresh_id(db, enriched.type, enriched.target),
        type=enriched.type,
        target=enriched.target,
        article=enriched.article,
        reflexive=bool(enriched.reflexive),
        gloss=enriched.gloss or "",
        note=enriched.note,
        example=enriched.example,
        level=enriched.level,
        source="community",
        first_added_by=user_id,
    )
    try:
        # Same rules the curated YAML must pass — a card that would fail the
        # pack validator must not enter the catalog through the side door.
        _validate_card(
            {k: getattr(card, k) for k in ("id", "type", "target", "article", "reflexive", "gloss", "note")},
            "community",
        )
    except ValueError as err:
        logger.warning("Satz enricher produced an invalid card for {!r}: {}", word, err)
        raise HTTPException(
            status_code=502,
            detail="The forge produced a malformed card — try that word again.",
        )

    db.add(card)
    try:
        await db.flush()
    except IntegrityError:
        # Two learners forged the same word concurrently — theirs won, use it.
        await db.rollback()
        await db.execute(
            pg_insert(User).values(id=user_id).on_conflict_do_nothing(index_elements=["id"])
        )
        existing = await db.scalar(
            select(VocabCard).where(
                VocabCard.type == enriched.type,
                func.lower(VocabCard.target) == enriched.target.lower(),
            )
        )
        if existing is None:
            raise HTTPException(status_code=502, detail="Couldn't save the card — try again.")
        return existing
    return card


class WordIn(BaseModel):
    word: str


@router.post("/cards")
async def add_word(
    body: WordIn,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Add a single typed word to the caller's pool ("forge your own").

    Canonical-catalog hit (curated, or a card another learner already forged)
    → just link it, no LLM call. Miss → one cheap structured-output enrichment
    call builds a ``community`` card that then serves everyone — the same
    shared-canonical model pack cards use.
    """
    word = " ".join(body.word.split())
    if not word:
        raise HTTPException(status_code=422, detail="Type a word first.")
    if len(word) > 60:
        raise HTTPException(
            status_code=422,
            detail="That looks like a whole sentence — cards are for a single word or short phrase.",
        )

    # Same dev-DB-wipe guard as add_pack: a valid JWT implies the user row
    # exists, but tokens can outlive a wiped local DB.
    await db.execute(
        pg_insert(User).values(id=user_id).on_conflict_do_nothing(index_elements=["id"])
    )

    card = await _find_canonical(db, word)
    created = False
    if card is None:
        card = await _forge_card(db, word, user_id)
        created = True

    result = await db.execute(
        pg_insert(UserCard)
        .values(user_id=user_id, card_id=card.id)
        .on_conflict_do_nothing(index_elements=["user_id", "card_id"])
    )
    payload = _card_payload(card)  # serialize before commit expires the ORM row
    await db.commit()

    pool_size = await db.scalar(
        select(func.count()).select_from(UserCard).where(UserCard.user_id == user_id)
    )
    return {
        "card": payload,
        "created": created,
        "added": result.rowcount,
        "poolSize": pool_size,
    }


@router.delete("/deck/{card_id}")
async def remove_card(
    card_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Remove a card from the caller's pool. Only the ``user_cards`` link dies
    — the canonical card stays (other users may own it), and the owning pack
    naturally reverts from "Added ✓" to "Add the rest"."""
    result = await db.execute(
        delete(UserCard).where(UserCard.user_id == user_id, UserCard.card_id == card_id)
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="That card isn't in your pool.")
    await db.commit()

    pool_size = await db.scalar(
        select(func.count()).select_from(UserCard).where(UserCard.user_id == user_id)
    )
    return {"removed": result.rowcount, "poolSize": pool_size}


# A single spoken sentence — ~20 s of browser opus/aac lands well under this;
# the cap just keeps someone from streaming a podcast through the examiner.
_MAX_AUDIO_BYTES = 2_500_000


@router.post("/attempts")
async def submit_attempt(
    card_id: str = Form(...),
    audio: UploadFile = File(...),
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Judge one spoken sentence for a card in the caller's pool.

    Transcribe (Deepgram prerecorded, one POST) → examine (one structured-
    output LLM call) → verdict + feedback. Stateless for now: the scheduling
    phase will start recording outcomes on ``user_cards``.
    """
    card = await db.scalar(
        select(VocabCard)
        .join(UserCard, UserCard.card_id == VocabCard.id)
        .where(UserCard.user_id == user_id, VocabCard.id == card_id)
    )
    if card is None:
        raise HTTPException(status_code=404, detail="That card isn't in your pool.")

    data = await audio.read()
    if not data:
        raise HTTPException(status_code=422, detail="We didn't get any audio — try again.")
    if len(data) > _MAX_AUDIO_BYTES:
        raise HTTPException(
            status_code=413,
            detail="That recording is too long — keep it to one sentence.",
        )

    try:
        transcript = await transcribe_attempt(data, audio.content_type)
    except Exception:
        logger.exception("Satz transcription failed (card {})", card_id)
        raise HTTPException(
            status_code=502,
            detail="Couldn't process the audio — try again in a moment.",
        )
    if not transcript:
        raise HTTPException(
            status_code=422,
            detail="We couldn't hear anything — try again a bit closer to the mic.",
        )

    try:
        judgement = await examine_attempt(card, transcript)
    except Exception:
        logger.exception("Satz examiner call failed (card {})", card_id)
        raise HTTPException(
            status_code=502,
            detail="The examiner is unavailable right now — try again in a moment.",
        )

    return {
        "transcript": transcript,
        "verdict": judgement.verdict,
        "feedback": judgement.feedback,
        "corrected": judgement.corrected,
    }


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
