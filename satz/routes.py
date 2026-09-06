"""HTTP routes for Satzschmiede (SATZ-002 — Phase 1: browse packs + read deck;
Phase 2: forge your own word + remove a card from the pool; Phase 3: speak a
sentence, get an examiner verdict; Phase 4: expanding-interval scheduling).

Every route resolves the caller via the session JWT (``get_current_user_id``)
and gets one ``AsyncSession`` per request (``get_db``) — the two dependencies
AUTH-001/DATA-001 left ready for exactly this. CORS is app-wide in ``main.py``,
so the Next.js origin needs no extra wiring here.
"""

import asyncio
import re
import time
from datetime import date, datetime, time as dtime
from typing import Literal
from uuid import uuid4

import aiohttp
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy import and_, delete, func, literal, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from agents.observability import mark_span_error, propagate_trace_context, tracer
from auth.deps import get_current_user_id
from config import langfuse_base_url, langfuse_public_key, langfuse_secret_key
from database.connection import get_db, get_sessionmaker
from database.orm import DrillAttempt, Pack, PackCard, User, UserCard, VocabCard, WordGloss
from database.repository import _assert_test_user, record_drill_attempt, record_grammar_error
from drills import forge_items_for_card
from grammar.ledger_guard import ledger_guard_reason
from satz.content import _validate_card
from satz.enricher import EnrichedCard, enrich_word
from satz.example_forge import backfill_card_examples, forge_card_examples
from satz.examiner import examine_attempt, transcribe_attempt
from satz.explainer import explain_correction
from satz.glosser import function_word_gloss, gloss_word
from satz.scheduler import lapse_interval, schedule

from coins.gate import admit_coins_or_402
from coins.prices import SATZ_ATTEMPT
from recordings.service import schedule_recording
from satz.verifier import verify_card
from security import drill_try_admit, gloss_try_admit

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
    if c.tense:
        payload["tense"] = c.tense
        payload["tenseForm"] = c.tense_form
    if c.example:
        payload["example"] = c.example
    # SATZ-017: the rotation pool — original example first, forged leveled
    # examples after, deduped. Sent only when there is real variety (>1) so
    # single-example payloads (incl. Verbformen's own copy, which never sets
    # this) keep the plain `example` path.
    if c.examples:
        pool = [c.example] if c.example else []
        pool += [e for e in c.examples if isinstance(e, str) and e not in pool]
        if len(pool) > 1:
            payload["examples"] = pool
    if c.level:
        payload["level"] = c.level
    return payload


def _srs_payload(uc: UserCard, now: datetime) -> dict:
    """Per-user schedule state riding on each deck card (frontend ``CardSrs``).
    ``status`` is computed server-side so the client never compares clocks:
    "new" = never practiced, "due" = practice today, "later" = scheduled
    ahead. Dealing (the standalone practice queue's buildQueue AND the Flow
    mixed-practice cycle) both key off `status` alone.
    """
    if uc.due_at is None:
        status = "new"
    elif uc.due_at <= now:
        status = "due"
    else:
        status = "later"
    return {
        "status": status,
        "dueAt": uc.due_at.isoformat() if uc.due_at else None,
        "intervalDays": uc.interval_days,
        "reps": uc.reps,
        # SATZ P2: shown on the "Schwere Wörter" shelf ("6× daneben").
        "lapses": uc.lapses,
    }


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

    _assert_test_user(user_id)  # TEST-001: direct write, not via repository
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
_ID_PREFIX = {
    "noun": "n", "verb": "v", "phrase": "p", "adjective": "adj",
    "preposition": "prep", "adverb": "adv",
}


def _type_hint(word: str) -> str | None:
    """Best-effort card-type hint from the RAW input, to tell homographs apart
    (verb ``unternehmen`` vs. noun ``Unternehmen``). Returns ``"noun"``,
    ``"not-noun"`` (verb/adjective/preposition/adverb/phrase), or ``None`` when the
    input gives no signal — so an article-led or capitalized single word never
    dedups against a card of the wrong type (SATZ homograph fix)."""
    w = word.strip()
    low = w.lower()
    # A leading article is an unambiguous noun marker.
    if any(
        low.startswith(a)
        for a in ("der ", "die ", "das ", "ein ", "eine ", "kein ", "keine ",
                  "mein ", "dein ", "sein ", "ihr ", "unser ", "euer ")
    ):
        return "noun"
    # A leading "sich" marks a reflexive verb.
    if low.startswith("sich "):
        return "not-noun"
    # One token: German capitalizes nouns and lowercases verbs/adjectives/
    # prepositions, so casing is a reliable-enough hint for a single word.
    if len(w.split()) == 1:
        return "noun" if w[:1].isupper() else "not-noun"
    return None


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
    # Homograph guard: a mismatched-type catalog hit must NOT dedup — drop it so
    # the word flows to enrichment and forges the correct new-type card.
    hint = _type_hint(word)
    if hint == "noun":
        rows = [c for c in rows if c.type == "noun"]
    elif hint == "not-noun":
        rows = [c for c in rows if c.type != "noun"]
    # A verb's past sibling shares its target — typed input always resolves
    # to the base (present) card; pairing links the sibling separately.
    rows = sorted(rows, key=lambda c: c.tense is not None)
    for c in rows:
        if c.target in word:
            return c
    return rows[0] if rows else None


async def _fresh_id(
    db: AsyncSession, card_type: str, target: str, *, suffix: str = ""
) -> str:
    """Mint a community card id in the same shape as the pack YAML slugs
    ("n-feierabend"; tense siblings append theirs: "v-fliegen-past"),
    suffixing a counter on the rare id collision across targets."""
    s = target.lower()
    for a, b in _TRANSLIT:
        s = s.replace(a, b)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-") or "wort"
    base = f"{_ID_PREFIX[card_type]}-{s[:40]}{suffix}"
    cid, n = base, 2
    while await db.get(VocabCard, cid) is not None:
        cid = f"{base}-{n}"
        n += 1
    return cid


# SATZ-021: forge -> fact-check -> retry-with-feedback, capped. A measured
# 10.1% of LLM-forged cards carried a confirmed factual error (wrong sense,
# invented plural, wrong Perfekt auxiliary, …) — this is the gate that
# catches those before they enter the shared catalog.
_MAX_FORGE_ATTEMPTS = 3


async def _forge_card(
    db: AsyncSession, word: str, user_id: str, *, session_id: str | None = None
) -> tuple[VocabCard, EnrichedCard]:
    """Catalog miss → enrich via LLM, fact-check it, validate against the
    curated card rules, insert a ``community`` canonical row. Flushed but
    not committed — the caller commits together with the pool link. Returns
    the enrichment too so a verb's past sibling can be forged without a
    second LLM call.

    SATZ-021: ``_validate_card`` below only checks the card's SHAPE — never
    whether the German is TRUE. Before building the card, up to
    ``_MAX_FORGE_ATTEMPTS`` rounds of enrich -> ``verify_card`` fact-check
    run: a rejected verdict's ``problem`` feeds back into the next
    ``enrich_word`` call so the retry fixes exactly what was named instead
    of just resampling the same mistake. Exhausting every attempt still
    ships the last one — the learner asked for this word and must not be
    blocked — but marks the Langfuse span an error so the failure stays
    visible: the verdict is forgotten, a wrong card in the catalog is not.
    """
    feedback: str | None = None
    enriched: EnrichedCard | None = None
    for attempt in range(1, _MAX_FORGE_ATTEMPTS + 1):
        try:
            enriched = await enrich_word(
                word, user_id=user_id, session_id=session_id, feedback=feedback
            )
        except Exception:
            logger.exception("Satz enrichment call failed for {!r}", word)
            raise HTTPException(
                status_code=502,
                detail="The word forge is unavailable right now — try again in a moment.",
            )
        if not enriched.valid or not enriched.target or not enriched.type:
            # Reject/translate outcome — there is no card here to fact-check.
            break
        try:
            verdict = await verify_card(enriched, user_id=user_id, session_id=session_id)
        except Exception:
            # The verifier itself going down must never block an add — ship
            # this attempt unverified, same as every other non-fatal failure
            # in this module.
            logger.exception("Satz card verification call failed for {!r}", word)
            break
        if verdict.ok:
            break
        feedback = verdict.problem
        logger.warning(
            "Satz forge attempt {}/{} rejected for {!r}: {}",
            attempt, _MAX_FORGE_ATTEMPTS, word, feedback,
        )
    else:
        # All _MAX_FORGE_ATTEMPTS rounds came back rejected — ship the last
        # attempt anyway, but make the failure loud in Langfuse rather than
        # silent.
        with propagate_trace_context(user_id=user_id, session_id=session_id), tracer.start_as_current_span("satz-forge-gate-exhausted") as span:
            if session_id:
                span.set_attribute("langfuse.session.id", session_id)
            span.set_attribute("user.id", user_id)
            span.set_attribute("word", word)
            span.set_attribute("satz.gate", "base_card")
            span.set_attribute("langfuse.environment", "forge")
            mark_span_error(span, feedback or "verification failed")
        logger.warning(
            "Satz forge exhausted {} attempts for {!r}, shipping unverified: {}",
            _MAX_FORGE_ATTEMPTS, word, feedback,
        )

    if not enriched.valid or not enriched.target or not enriched.type:
        if enriched.german_equivalent:
            # SATZ-005: the input is a real foreign word we can name a German
            # equivalent for. Don't dead-end the learner into retyping it by
            # hand — hand back a structured suggestion so the frontend can
            # offer it as a one-tap "add the German word X?" confirmation.
            raise HTTPException(
                status_code=422,
                detail={
                    "message": enriched.reason
                    or f"That looks like {enriched.source_language or 'another language'}.",
                    "suggestion": {
                        "word": enriched.german_equivalent,
                        "gloss": enriched.gloss,
                        "sourceLanguage": enriched.source_language,
                    },
                },
            )
        raise HTTPException(
            status_code=422,
            detail=enriched.reason or "That doesn't look like a German word or phrase.",
        )

    # The enricher normalizes ("Termine" → "Termin"), so re-check the dedup
    # seam (type, lower(target), present) before inserting a duplicate row.
    existing = await db.scalar(
        select(VocabCard).where(
            VocabCard.type == enriched.type,
            func.lower(VocabCard.target) == enriched.target.lower(),
            VocabCard.tense.is_(None),
        )
    )
    if existing is not None:
        return existing, enriched

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

    _assert_test_user(user_id)  # TEST-001: direct write, not via repository
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
                VocabCard.tense.is_(None),
            )
        )
        if existing is None:
            raise HTTPException(status_code=502, detail="Couldn't save the card — try again.")
        return existing, enriched
    return card, enriched


async def _ensure_past_sibling(
    db: AsyncSession,
    present: VocabCard,
    user_id: str,
    enriched: EnrichedCard | None,
    *,
    session_id: str | None = None,
) -> VocabCard | None:
    """A verb comes as a pair: the base (present) card plus a spoken-past
    sibling whose answer side shows the form as actually spoken ("ist
    geflogen", "dachte · hat gedacht"). Find the sibling, or forge it — from
    the enrichment already in hand on a fresh forge, or one extra enricher
    call when a catalog hit predates verb pairing. Returns None (with a log)
    rather than failing the add: the present card alone is still a win.

    SATZ-021: the already-in-hand ``enriched`` case is never re-verified
    here — it already went through ``_forge_card``'s fact-check loop over
    the WHOLE card, past fields included. Only the catalog-hit branch below
    (its own fresh ``enrich_word`` call) runs the fact-check gate, and on
    exhaustion it gives up on the sibling rather than risk inserting a
    wrong one — a missing past sibling is strictly better than a wrong one.
    """
    sibling = await db.scalar(
        select(VocabCard).where(
            VocabCard.type == "verb",
            func.lower(VocabCard.target) == present.target.lower(),
            VocabCard.tense == "past",
        )
    )
    if sibling is not None:
        return sibling

    if enriched is None or not enriched.past_form:
        feedback: str | None = None
        fresh: EnrichedCard | None = None
        for attempt in range(1, _MAX_FORGE_ATTEMPTS + 1):
            try:
                fresh = await enrich_word(
                    present.target,
                    user_id=user_id,
                    session_id=session_id,
                    feedback=feedback,
                )
            except Exception:
                logger.exception(
                    "Satz past-sibling enrichment failed for {!r}", present.target
                )
                return None
            if not fresh.past_form:
                # Nothing to fact-check — the missing-past-form warning
                # below handles this exactly as before.
                break
            try:
                verdict = await verify_card(
                    fresh, user_id=user_id, session_id=session_id
                )
            except Exception:
                # Verifier outage: ship this attempt unverified rather than
                # block the add, same non-fatal handling as everywhere else.
                logger.exception(
                    "Satz past-sibling verification call failed for {!r}",
                    present.target,
                )
                break
            if verdict.ok:
                break
            feedback = verdict.problem
            logger.warning(
                "Satz past-sibling forge attempt {}/{} rejected for {!r}: {}",
                attempt, _MAX_FORGE_ATTEMPTS, present.target, feedback,
            )
        else:
            # Exhausted every attempt without a passing verdict — skip the
            # sibling instead of inserting one we couldn't fact-check clean.
            # Same Langfuse error signal as the base-card gate: giving up is
            # the safe outcome here, but it must never be a *silent* one, or
            # verbs quietly stop getting past siblings and nobody notices.
            with propagate_trace_context(user_id=user_id, session_id=session_id), tracer.start_as_current_span("satz-forge-gate-exhausted") as span:
                if session_id:
                    span.set_attribute("langfuse.session.id", session_id)
                span.set_attribute("user.id", user_id)
                span.set_attribute("word", present.target)
                span.set_attribute("satz.gate", "past_sibling")
                span.set_attribute("langfuse.environment", "forge")
                mark_span_error(span, feedback or "verification failed")
            logger.warning(
                "Satz past sibling for {!r} never verified after {} attempts "
                "— skipping rather than risking a wrong one: {}",
                present.target, _MAX_FORGE_ATTEMPTS, feedback,
            )
            return None
        enriched = fresh
    if not enriched.past_form:
        logger.warning(
            "Satz enricher gave no past form for {!r} — pairing skipped",
            present.target,
        )
        return None

    values = dict(
        id=await _fresh_id(db, "verb", present.target, suffix="-past"),
        type="verb",
        target=present.target,
        reflexive=present.reflexive,
        gloss=present.gloss,
        tense="past",
        tense_form=enriched.past_form,
        example=enriched.past_example,
        level=present.level,
        source="community",
        first_added_by=user_id,
    )
    try:
        _validate_card({**values, "article": None, "note": None}, "community")
    except ValueError as err:
        logger.warning(
            "Satz past sibling invalid for {!r}: {}", present.target, err
        )
        return None

    # Plain insert would race two learners adding the same verb — DO NOTHING
    # and re-select instead: whoever's row landed, both link it.
    await db.execute(pg_insert(VocabCard).values(**values).on_conflict_do_nothing())
    return await db.scalar(
        select(VocabCard).where(
            VocabCard.type == "verb",
            func.lower(VocabCard.target) == present.target.lower(),
            VocabCard.tense == "past",
        )
    )


class WordIn(BaseModel):
    word: str
    # OBS-007: optional frontend-minted practice-sitting id, threaded to
    # enrich_word() so an add-a-word call fired mid-session files its
    # "satz-forge" trace into that session instead of standing alone.
    # Optional so older clients and curl keep working.
    session_id: str | None = None
    # SATZ-013: "gloss" when this add came from the hover/tap GLOSS popover's
    # one-tap add button — the only path subject to GLOSS_ADDS_PER_DAY.
    # Absent/None for the manual add-word form and pack adds, which stay
    # uncapped.
    source: Literal["gloss"] | None = None


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
    shared-canonical model pack cards use. Verbs arrive as a pair: the base
    card plus a spoken-past sibling, each on its own schedule.
    """
    if not drill_try_admit(user_id):
        raise HTTPException(
            status_code=429,
            detail="You're going very fast — take a short break and try again in a few minutes.",
        )
    word = " ".join(body.word.split())
    if not word:
        raise HTTPException(status_code=422, detail="Type a word first.")
    if len(word) > 60:
        raise HTTPException(
            status_code=422,
            detail="That looks like a whole sentence — cards are for a single word or short phrase.",
        )

    _assert_test_user(user_id)  # TEST-001: direct write, not via repository
    # Same dev-DB-wipe guard as add_pack: a valid JWT implies the user row
    # exists, but tokens can outlive a wiped local DB.
    await db.execute(
        pg_insert(User).values(id=user_id).on_conflict_do_nothing(index_elements=["id"])
    )

    card = await _find_canonical(db, word)
    created = False
    enriched = None
    if card is None:
        card, enriched = await _forge_card(
            db, word, user_id, session_id=body.session_id
        )
        created = True

    to_link = [card]
    if card.type == "verb" and card.tense is None:
        sibling = await _ensure_past_sibling(
            db, card, user_id, enriched, session_id=body.session_id
        )
        if sibling is not None:
            to_link.append(sibling)

    # SATZ-013: the GLOSS popover's one-tap add is capped at
    # GLOSS_ADDS_PER_DAY/day; every other path (manual add-word form, pack
    # add) leaves `gloss_path` False and behaves exactly as before.
    gloss_path = body.source == "gloss"
    gloss_used = 0
    gloss_blocked = False
    if gloss_path:
        gloss_used = await _gloss_adds_used_today(db, user_id)
        # A dedupe no-op (the tapped card is already in the caller's pool)
        # must never consume allowance — only a click that would actually
        # link the tapped card can be blocked. Sibling links are free (see
        # the stamping rule below), so they neither block nor get blocked.
        already_linked = set(
            (
                await db.execute(
                    select(UserCard.card_id).where(
                        UserCard.user_id == user_id,
                        UserCard.card_id.in_([c.id for c in to_link]),
                    )
                )
            )
            .scalars()
            .all()
        )
        would_create_new = card.id not in already_linked
        gloss_blocked = would_create_new and gloss_used >= GLOSS_ADDS_PER_DAY

    added = 0
    gloss_spent = 0
    if not gloss_blocked:
        for c in to_link:
            values = {"user_id": user_id, "card_id": c.id}
            # One popover click = one allowance unit: stamp (and count) only
            # the card the learner tapped — a verb's auto-created past-tense
            # sibling rides along unstamped so it can't burn a second slot.
            if gloss_path and c.id == card.id:
                values["source"] = "gloss"
            result = await db.execute(
                pg_insert(UserCard)
                .values(**values)
                .on_conflict_do_nothing(index_elements=["user_id", "card_id"])
            )
            added += result.rowcount
            if gloss_path and c.id == card.id and result.rowcount:
                gloss_spent = 1
    payload = _card_payload(card)  # serialize before commit expires the ORM row
    await db.commit()

    pool_size = await db.scalar(
        select(func.count()).select_from(UserCard).where(UserCard.user_id == user_id)
    )

    # CONT-002: forge personal drill items for the newly linked word in the
    # background — never blocks or fails the add. Skipped when the gloss cap
    # blocked the link: the card never entered this user's pool, so there is
    # nothing of theirs to forge drill items from.
    if not gloss_blocked:
        try:
            asyncio.create_task(forge_items_for_card(user_id, card.id))
        except Exception:
            logger.exception("Drill-item forge scheduling failed ({})", card.id)
        # SATZ-017: forge the card's rotating example pool too (card-level,
        # shared — a no-op if another user's add already forged it).
        try:
            asyncio.create_task(forge_card_examples(card.id, user_id))
        except Exception:
            logger.exception("Example forge scheduling failed ({})", card.id)

    response = {
        "card": payload,
        "created": created,
        "added": added,
        "poolSize": pool_size,
    }
    if gloss_path:
        response["glossRemaining"] = max(
            0, GLOSS_ADDS_PER_DAY - (gloss_used + gloss_spent)
        )
    return response


@router.delete("/deck/{card_id}")
async def remove_card(
    card_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Remove a card from the caller's pool. Only the ``user_cards`` link dies
    — the canonical card stays (other users may own it), and the owning pack
    naturally reverts from "Added ✓" to "Add the rest"."""
    _assert_test_user(user_id)  # TEST-001: direct write, not via repository
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
    # OBS-007: frontend-minted practice-sitting id — groups every attempt of
    # one VocabTrainer mount into a single Langfuse Session (the analog of
    # the conversation's connect→disconnect session id). Optional so older
    # clients and curl keep working.
    session_id: str | None = Form(None, max_length=64),
    # SATZ-015: an immediate re-attempt of a card that just passed green with
    # a grammar correction — habit formation while the fix is fresh. Judged
    # exactly like any other attempt, but must NEVER move the schedule: the
    # original graded pass already claimed the interval climb / new-card slot
    # / ledger entry, and a second write here would double-advance or (on a
    # failed retry) wrongly punish the ladder for extra practice.
    rehearsal: bool = Form(False),
    user_id: str = Depends(get_current_user_id),
    background_tasks: BackgroundTasks = None,
):
    """Judge one spoken sentence for a card in the caller's pool.

    Transcribe (Deepgram prerecorded, one POST) → examine (one structured-
    output LLM call) → verdict + feedback, then record the outcome on the
    ``user_cards`` row: only ``word_ok`` moves the schedule (a grammar note
    on a green card costs nothing — same rule as the verdict colour).

    GRAM-001: when the examiner also classified the slip into the grammar-
    pattern catalog, the attempt is harvested into the error ledger — after
    the schedule commit and non-fatally, so the ledger can never break a
    practice attempt. The response payload is unchanged: feedback separation
    means Satzschmiede never surfaces the ledger.

    SATZ-015: ``rehearsal=True`` runs the identical STT + examiner judge and
    returns the identical payload shape, but skips every schedule/ledger/
    attempt-log write — the card's row must be byte-identical before and
    after. ``DrillAttempt`` has no free-form field that could mark a row as
    a rehearsal without a migration or ambiguity in per-card stats, so the
    row is skipped entirely rather than logged oddly; the fact still rides
    on this request's span as the ``rehearsal`` attribute either way.
    REL-001's clip archive is NOT skipped, though — a rehearsal clip is
    still kept, under ``ref_kind="satz_rehearsal"`` pointing at the card id
    instead of a (nonexistent) attempt row.
    """
    if not drill_try_admit(user_id):
        raise HTTPException(
            status_code=429,
            detail="You're going very fast — take a short break and try again in a few minutes.",
        )
    # REL-005 (DBFIX review, 2026-09-05): the pool-ownership read, the coin
    # gate, and the (cheap) sibling-form lookup all run inside their OWN
    # short-lived session, closed (connection released back to the pool)
    # BEFORE transcribe_attempt's Deepgram call and examine_attempt's judge
    # call below start — a request must never hold a pooled connection
    # across a slow provider call. No ``Depends(get_db)`` on this route for
    # that reason; same pattern ``briefkasten/routes.py::get_letter`` and
    # ``bauteil/routes.py::submit_attempt`` use. `card` and `user_card`
    # cross the boundary below as plain, already-loaded rows — `VocabCard`/
    # `UserCard` carry no relationships, so touching their already-fetched
    # scalar columns after the session closes triggers no lazy load; the
    # write path re-fetches `user_card` fresh instead of mutating this one.
    async with get_sessionmaker()() as db:
        row = (
            await db.execute(
                select(VocabCard, UserCard)
                .join(UserCard, UserCard.card_id == VocabCard.id)
                .where(UserCard.user_id == user_id, VocabCard.id == card_id)
            )
        ).first()
        if row is None:
            raise HTTPException(status_code=404, detail="That card isn't in your pool.")
        # PAY-002: graded satz attempt — 5 coins each. Charged AFTER the 404
        # (a stale card id must not burn coins) but BEFORE any STT/judge work.
        # Rehearsal is charged like any other attempt.
        try:
            await admit_coins_or_402(db, user_id=user_id, price=SATZ_ATTEMPT, kind="spend_attempt")
        except HTTPException as _e:
            if _e.status_code == 402:
                raise
            raise HTTPException(status_code=503, detail="billing temporarily unavailable")
        card, user_card = row
        # Snapshotted for the write session below, which re-fetches the row
        # and must still produce a verdict if that row is gone by then.
        user_card_interval_days = user_card.interval_days

        # STT-003 P2: the card names the exact word we expect to hear — the
        # ideal keyterm. For a past-tense card add the spoken past form too
        # (the learner says "ist gegangen", not the lemma "gehen").
        card_keyterms = [card.target]
        if card.tense_form:
            card_keyterms.append(card.tense_form)

        # SATZ-008: a base verb card accepts spoken past forms, but strong-verb
        # participles ("erkannt") share no stem with the infinitive — pull the
        # past sibling's spoken form so STT biasing AND the presence guard see it.
        extra_forms: list[str] = []
        if card.type == "verb" and card.tense is None:
            sibling_form = await db.scalar(
                select(VocabCard.tense_form).where(
                    VocabCard.type == "verb",
                    func.lower(VocabCard.target) == card.target.lower(),
                    VocabCard.tense == "past",
                )
            )
            if sibling_form:
                extra_forms.append(sibling_form)
                card_keyterms.append(sibling_form)

        # SATZ-015: a rehearsal reports the schedule state the ORIGINAL
        # graded attempt already committed — snapshot it now, before this
        # session closes, since the write path below never touches it again
        # on a rehearsal (it skips the write block entirely).
        rehearsal_due_in_days = user_card.interval_days or 0
    # `db` is closed here — the pooled connection is released before the
    # STT/judge calls below (REL-005).

    data = await audio.read()
    if not data:
        raise HTTPException(status_code=422, detail="We didn't get any audio — try again.")
    if len(data) > _MAX_AUDIO_BYTES:
        raise HTTPException(
            status_code=413,
            detail="That recording is too long — keep it to one sentence.",
        )

    # OBS-006: one Langfuse trace per judged attempt. The `stt` and `llm`
    # child generations (opened inside transcribe_attempt / examine_attempt
    # — start_as_current_span nests them here automatically) split the
    # attempt's highly variable latency per stage; the perf_counter log line
    # below answers the same question locally when Langfuse is off.
    with propagate_trace_context(user_id=user_id, session_id=session_id), tracer.start_as_current_span("satz-attempt") as attempt_span:
        attempt_span.set_attribute("user.id", user_id)
        attempt_span.set_attribute("card_id", card_id)
        if session_id:
            attempt_span.set_attribute("langfuse.session.id", session_id)
        # SATZ-015: the rehearsal fact always rides on the span, whether or
        # not a DrillAttempt row gets written below.
        attempt_span.set_attribute("rehearsal", bool(rehearsal))

        t0 = time.perf_counter()
        try:
            transcript = await transcribe_attempt(
                data, audio.content_type, keyterms=card_keyterms
            )
        except Exception as exc:
            attempt_span.record_exception(exc)
            logger.exception("Satz transcription failed (card {})", card_id)
            raise HTTPException(
                status_code=502,
                detail="Couldn't process the audio — try again in a moment.",
            )
        t_stt = time.perf_counter()
        if not transcript:
            raise HTTPException(
                status_code=422,
                detail="We couldn't hear anything — try again a bit closer to the mic.",
            )
        attempt_span.set_attribute("langfuse.observation.input", transcript)

        try:
            judgement = await examine_attempt(card, transcript, extra_forms=extra_forms)
        except Exception as exc:
            attempt_span.record_exception(exc)
            logger.exception("Satz examiner call failed (card {})", card_id)
            raise HTTPException(
                status_code=502,
                detail="The examiner is unavailable right now — try again in a moment.",
            )
        t_llm = time.perf_counter()
        logger.info(
            "Satz attempt timing: stt={:.2f}s llm={:.2f}s (card {})",
            t_stt - t0,
            t_llm - t_stt,
            card_id,
        )
        attempt_span.set_attribute(
            "langfuse.observation.output",
            f"wordOk={judgement.word_ok} grammarOk={judgement.grammar_ok}"
            + (f" → {judgement.corrected}" if judgement.corrected else ""),
        )
        # Structured verdict attributes so Langfuse can filter without
        # string-parsing the free-text trace.output above.
        attempt_span.set_attribute("verdict.word_ok", bool(judgement.word_ok))
        attempt_span.set_attribute("verdict.grammar_ok", bool(judgement.grammar_ok))
        if judgement.pattern_id:
            attempt_span.set_attribute("verdict.pattern_id", judgement.pattern_id)

        attempt_id: int | None = None
        if rehearsal:
            # SATZ-015: judge normally, touch nothing. The original graded
            # attempt already claimed the new-card slot and moved the
            # schedule — report that already-committed state back unchanged
            # rather than a fresh (and unwritten) computation. Snapshotted
            # before the read session closed above (REL-005) since no write
            # session opens at all on this path.
            due_in_days = rehearsal_due_in_days
        else:
            # REL-005: a second, fresh short-lived session for the schedule
            # write and the ledger/DATA-004 writes below — opened only now,
            # AFTER transcribe_attempt/examine_attempt above have already
            # returned, so nothing here holds a pooled connection across
            # those up-to-12s-per-leg calls either. `user_card` from the
            # read session above is detached and would silently no-op on
            # commit — re-fetch it fresh in THIS session instead of mutating
            # the old instance.
            async with get_sessionmaker()() as db:
                user_card = await db.get(UserCard, {"user_id": user_id, "card_id": card_id})
                if user_card is None:
                    # The learner removed the card from the pool DURING the
                    # STT/judge window (the window REL-005 widened). The old
                    # single-session code silently updated 0 rows here; the
                    # first two-session cut crashed with a 500 after the
                    # charge and the judge call (hygiene review, 2026-09-06).
                    # Keep the old outcome: no schedule to move, the verdict
                    # still comes back, and the ledger / attempt-log /
                    # archive writes below still run.
                    logger.warning(
                        "Satz attempt on card {} finished after the learner removed it — "
                        "verdict returned, no schedule to update",
                        card_id,
                    )
                    interval, due_at = schedule(
                        judgement.word_ok, user_card_interval_days, datetime.now()
                    )
                else:
                    # SATZ dosing P1: the card's first graded attempt claims its
                    # new-card slot for today (idempotent past the first attempt).
                    if user_card.started_at is None:
                        user_card.started_at = datetime.now()

                    # Record the outcome. A miss lands on (lapse_interval, now) —
                    # still due, retryable this session; a hit climbs the ladder.
                    interval, due_at = schedule(
                        judgement.word_ok, user_card.interval_days, datetime.now()
                    )
                    user_card.interval_days = interval
                    user_card.due_at = due_at
                    user_card.reps += 1
                    user_card.last_score = 1 if judgement.word_ok else 0
                    # A graded word-miss is a lapse — same signal that already
                    # quartered interval_days above.
                    if not judgement.word_ok:
                        _record_lapse(user_card)
                    try:
                        await db.commit()
                    except Exception:
                        logger.exception("Satz schedule commit failed (card {})", card_id)
                        await db.rollback()
                # A miss stores a punished (partially reset) interval, not zero —
                # but the card IS due now. The client's "Back in N days" vs "due
                # again now" copy keys off this field, so report 0 on any miss
                # regardless of what got stored on interval_days.
                due_in_days = interval if judgement.word_ok else 0

                # Harvest into the grammar-error ledger (GRAM-001) — its own
                # commit, after the schedule is safe; a ledger failure only
                # logs. Rehearsal never reaches this branch (SATZ-015): the
                # original graded attempt already harvested whatever pattern
                # applied — a rehearsal must not double-write it.
                #
                # STT-006: this is the one spoken-drill ledger writer with no
                # deterministic guard in front of it (the tandem debrief and
                # the error extractor both go through `ledger_guard_reason`
                # — see agents/debrief.py, agents/error_extractor.py).
                # `submit_attempt` always transcribes audio (never a typed
                # path — see the route docstring), so the guard applies
                # unconditionally here, same as every other caller of
                # `satz/examiner.py` (SATZ-022's module note). `quote`/
                # `source_text` are both the same raw transcript: there is
                # no separate "evidence span" field on `Judgement` the way
                # the debrief/harvest have one, the judged sentence IS the
                # transcript.
                if judgement.pattern_id:
                    guard_reason = ledger_guard_reason(
                        pattern_id=judgement.pattern_id,
                        quote=transcript,
                        corrected=judgement.corrected,
                        source_text=transcript,
                        check_das_dass=True,
                        check_asr_artifacts=True,
                        strip_punctuation=True,
                    )
                    if guard_reason:
                        logger.info(
                            "Ledger guard dropped satz row: pattern={} reason={} card={} session={}",
                            judgement.pattern_id, guard_reason, card_id, session_id,
                        )
                    else:
                        try:
                            await record_grammar_error(
                                db,
                                user_id=user_id,
                                pattern_id=judgement.pattern_id,
                                sentence=transcript,
                                corrected=judgement.corrected,
                                note=judgement.error,
                                source="satz",
                            )
                        except Exception:
                            logger.exception(
                                "Grammar-ledger write failed (pattern {})", judgement.pattern_id
                            )

                # Append to the cross-drill attempt log (DATA-004) — its own
                # commit, non-fatal like the ledger write above; an
                # attempt-log outage must never break the practice attempt
                # it rides on. Rehearsal never reaches this branch — see the
                # route docstring for why the row is dropped rather than
                # marked.
                try:
                    attempt_id = await record_drill_attempt(
                        db,
                        user_id=user_id,
                        exercise="satz",
                        item_ref=card_id,
                        pattern_id=judgement.pattern_id,
                        correct=judgement.word_ok and judgement.grammar_ok,
                        word_ok=judgement.word_ok,
                        modality="spoken",
                        session_id=session_id,
                    )
                except Exception:
                    logger.exception("Drill-attempt log write failed (card {})", card_id)

        # REL-001: archive the spoken clip. A rehearsal (SATZ-015) writes no
        # `drill_attempts` row by design, but the clip is kept anyway —
        # "every learner clip is kept" — under its own `ref_kind` pointing
        # at the card instead of an attempt row. A normal (non-rehearsal)
        # attempt links to the attempt row just written; skipped only if
        # that write itself failed above (nothing to link to).
        if rehearsal:
            schedule_recording(
                background_tasks,
                user_id=user_id,
                surface="satz",
                exercise="satz",
                ref_kind="satz_rehearsal",
                ref_id=card_id,
                item_id=card_id,
                data=data,
                content_type=audio.content_type,
                transcript=transcript,
            )
        elif attempt_id is not None:
            schedule_recording(
                background_tasks,
                user_id=user_id,
                surface="satz",
                exercise="satz",
                ref_kind="drill_attempt",
                ref_id=str(attempt_id),
                item_id=card_id,
                data=data,
                content_type=audio.content_type,
                transcript=transcript,
            )

        # camelCase like every other satz payload (poolSize, cardCount, …).
        return {
            "transcript": transcript,
            "wordOk": judgement.word_ok,
            "grammarOk": judgement.grammar_ok,
            "error": judgement.error,
            "corrected": judgement.corrected,
            "dueInDays": due_in_days,
            # SATZ-008: the OTel trace id of THIS judgement, so the client
            # can file a disagree-flag score onto the exact trace.
            "traceId": format(attempt_span.get_span_context().trace_id, "032x"),
        }


# SATZ dosing P2: today's deal is a fixed-size session, not a flat drip. The
# old NEW_PER_DAY=5 rule (Anki manual rationale: each new word costs roughly
# 5-10 eventual reviews as it climbs the ladder) plus an accuracy throttle
# meant a heavy-review day could zero out new intake entirely — a learner
# camped on their pool for a week and never saw a new word. This replaces
# both: SESSION_TARGET cards a day, REVIEW_SHARE of them claimed by reviews
# (due + already cleared today) at most, new words fill whatever's left — so
# intake never drops below SESSION_TARGET - REVIEW_SHARE and grows toward
# SESSION_TARGET as reviews clear. See get_deck for the allowance math.
SESSION_TARGET = 20   # cards a learner should meet in a day
REVIEW_SHARE = 15     # reviews' slice of the target; the rest is new words

def _record_lapse(user_card: UserCard) -> None:
    """Bump the lifetime lapse counter — telemetry only.

    Called from every place that already punishes the schedule for this
    card (a graded word-miss, a reveal, a gender-miss). Used to also
    auto-bench the card past a leech threshold (SATZ P2); benching was
    removed (satz P3, 0020 migration cleared every bench), so this is now
    just a counter nothing acts on — kept in case a future policy wants the
    lifetime lapse history.
    """
    user_card.lapses += 1


# SATZ-013: the GLOSS popover's one-tap add is a casual-reading collector,
# not a study decision — capped far below the new-word allowance so a
# browsing session can't silently balloon the pool. Manual add-word and pack
# adds are untouched.
GLOSS_ADDS_PER_DAY = 3


async def _gloss_adds_used_today(db: AsyncSession, user_id: str) -> int:
    """SATZ-013: how many of the caller's GLOSS_ADDS_PER_DAY slots are
    already spent today — rows in ``user_cards`` stamped ``source='gloss'``
    with ``added_at`` today. Same naive-TIMESTAMP/UTC day-boundary convention
    already used by the ``started_at`` / new-word allowance below."""
    today_midnight = datetime.combine(date.today(), dtime.min)
    return await db.scalar(
        select(func.count())
        .select_from(UserCard)
        .where(
            UserCard.user_id == user_id,
            UserCard.source == "gloss",
            UserCard.added_at >= today_midnight,
        )
    )


@router.get("/deck")
async def get_deck(
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """The caller's whole pool, oldest-added first, each card carrying its
    schedule state. One payload serves both trainer modes: the frontend builds
    today's practice queue from the due/new cards and keeps browse-all over
    the full list.

    ``newAllowance`` (SATZ dosing P2) tells the frontend how many more NEW
    cards it may start today. Reviews are never capped by this — due cards
    always show up — but they DO claim part of the fixed SESSION_TARGET, so
    a heavy review day still leaves room for at least
    ``SESSION_TARGET - REVIEW_SHARE`` new words rather than zeroing intake.
    """
    now = datetime.now()
    rows = (
        await db.execute(
            select(VocabCard, UserCard)
            .join(UserCard, UserCard.card_id == VocabCard.id)
            .where(UserCard.user_id == user_id)
            .order_by(UserCard.added_at, VocabCard.id)
        )
    ).all()

    today_midnight = datetime.combine(date.today(), dtime.min)
    started_today = await db.scalar(
        select(func.count())
        .select_from(UserCard)
        .where(UserCard.user_id == user_id, UserCard.started_at >= today_midnight)
    )

    # Today's review load: cards due right now, plus cards already reviewed
    # today (a review knocked out this morning doesn't free up a new-word
    # slot it never actually vacated). "Reviewed" = a graded satz attempt on
    # a card that was started BEFORE today — the join + started_at filter is
    # what keeps a same-day new-word attempt from double-counting as a review,
    # and the due_at > now filter keeps a review attempted-but-MISSED today
    # (lapse ⇒ due again right now) from counting twice, once per query.
    due_now = await db.scalar(
        select(func.count())
        .select_from(UserCard)
        .where(
            UserCard.user_id == user_id,
            UserCard.due_at.is_not(None),
            UserCard.due_at <= now,
        )
    )
    reviewed_today = await db.scalar(
        select(func.count(func.distinct(DrillAttempt.item_ref)))
        .select_from(DrillAttempt)
        .join(
            UserCard,
            and_(
                UserCard.user_id == DrillAttempt.user_id,
                UserCard.card_id == DrillAttempt.item_ref,
            ),
        )
        .where(
            DrillAttempt.user_id == user_id,
            DrillAttempt.exercise == "satz",
            DrillAttempt.correct.is_not(None),
            DrillAttempt.created_at >= today_midnight,
            UserCard.started_at < today_midnight,
            UserCard.due_at > now,
        )
    )
    review_target = min(due_now + reviewed_today, REVIEW_SHARE)
    new_budget = SESSION_TARGET - review_target
    new_allowance = max(0, new_budget - started_today)

    # SATZ-017: quietly forge example pools for a couple of this user's cards
    # that predate the forge — off the critical path, non-fatal, so the whole
    # catalog fills in over normal usage.
    try:
        asyncio.create_task(backfill_card_examples(user_id, limit=2))
    except Exception:
        logger.exception("Example backfill scheduling failed ({})", user_id)

    return {
        "cards": [
            {**_card_payload(c), "srs": _srs_payload(uc, now)} for c, uc in rows
        ],
        "newAllowance": new_allowance,
    }


@router.post("/deck/{card_id}/reveal")
async def reveal_card(
    card_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """The learner peeked at the example instead of attempting — a lapse.

    Drops the card to "due now" at a quarter of its old interval so the peek
    can't silently keep a long interval alive, while the next green climbs
    back from that quarter rather than restarting the whole ladder.
    ``reps``/``last_score`` stay untouched: a reveal isn't a graded attempt.
    """
    user_card = await db.scalar(
        select(UserCard).where(
            UserCard.user_id == user_id, UserCard.card_id == card_id
        )
    )
    if user_card is None:
        raise HTTPException(status_code=404, detail="That card isn't in your pool.")
    # A reveal consumes a new-card slot too — otherwise revealing new cards
    # would bypass the daily allowance into review load.
    if user_card.started_at is None:
        user_card.started_at = datetime.now()
    now = datetime.now()
    user_card.interval_days = lapse_interval(user_card.interval_days)
    user_card.due_at = now
    _record_lapse(user_card)
    await db.commit()
    return {"dueInDays": 0}


@router.post("/deck/{card_id}/gender-miss")
async def gender_miss_card(
    card_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """SATZ-010: the learner picked the wrong gender at the pre-record gate.

    Same schedule consequence as a reveal — a lapse, not a graded attempt:
    the card drops to "due now" at a quarter of its old interval, and
    ``reps``/``last_score`` stay untouched. A separate endpoint (rather than
    reusing /reveal) so the two lapse causes stay distinguishable in logs.
    """
    user_card = await db.scalar(
        select(UserCard).where(
            UserCard.user_id == user_id, UserCard.card_id == card_id
        )
    )
    if user_card is None:
        raise HTTPException(status_code=404, detail="That card isn't in your pool.")
    if user_card.started_at is None:
        user_card.started_at = datetime.now()
    now = datetime.now()
    user_card.interval_days = lapse_interval(user_card.interval_days)
    user_card.due_at = now
    _record_lapse(user_card)
    await db.commit()
    return {"dueInDays": 0}


class ExplainIn(BaseModel):
    card_id: str
    transcript: str
    corrected: str
    error: str | None = None
    # OBS-007: same optional practice-sitting id as /attempts.
    session_id: str | None = None


@router.post("/explain")
async def explain_attempt(
    body: ExplainIn,
    user_id: str = Depends(get_current_user_id),
):
    """SATZ-007: unpack a correction the learner just saw — one on-demand LLM
    call, no schedule/ledger/attempt-log writes. Keyed on the catalog card
    (not pool ownership) so the Verbformen mount of the same trainer works
    against its overlay deck too."""
    if not drill_try_admit(user_id):
        raise HTTPException(
            status_code=429,
            detail="You're going very fast — take a short break and try again in a few minutes.",
        )
    transcript = body.transcript.strip()
    corrected = body.corrected.strip()
    if not transcript or not corrected:
        raise HTTPException(status_code=422, detail="Nothing to explain yet.")
    if len(transcript) > 600 or len(corrected) > 600 or len(body.error or "") > 300:
        raise HTTPException(
            status_code=422, detail="That attempt is too long to explain."
        )
    if body.session_id and len(body.session_id) > 64:
        raise HTTPException(status_code=422, detail="Bad session id.")

    # REL-005: the card lookup runs in its own short-lived session, closed
    # before explain_correction's LLM call below — this route writes
    # nothing, so there is no matching post-call session either.
    async with get_sessionmaker()() as db:
        card = await db.get(VocabCard, body.card_id)
    if card is None:
        raise HTTPException(status_code=404, detail="Unknown card.")

    with propagate_trace_context(user_id=user_id, session_id=body.session_id), tracer.start_as_current_span("satz-explain-attempt") as span:
        span.set_attribute("user.id", user_id)
        span.set_attribute("card_id", body.card_id)
        if body.session_id:
            span.set_attribute("langfuse.session.id", body.session_id)
        span.set_attribute("langfuse.observation.input", transcript)
        try:
            result = await explain_correction(
                card, transcript, corrected, body.error, user_id=user_id
            )
        except Exception as exc:
            span.record_exception(exc)
            logger.exception("Satz explain call failed (card {})", body.card_id)
            raise HTTPException(
                status_code=502,
                detail="The coach is unavailable right now — try again in a moment.",
            )
        span.set_attribute("langfuse.observation.output", result.explanation)
    return {"explanation": result.explanation}


# UI-007: word-gloss endpoint — hover/tap any German word anywhere in the app
# -> translation + example. The learner-facing edge punctuation a hovered
# word might drag along ("Mänteln," at a clause boundary).
_EDGE_PUNCT = ".,!?;:…\"'()"


class GlossIn(BaseModel):
    word: str
    context: str | None = None
    # OBS-007-style optional practice-sitting id, threaded to gloss_word()
    # so a hover fired mid-drill files its "satz-gloss" trace into that
    # session instead of standing alone.
    session_id: str | None = Field(default=None, max_length=64)


@router.post("/gloss")
async def gloss_word_route(
    body: GlossIn,
    user_id: str = Depends(get_current_user_id),
):
    """UI-007: hover/tap any German word anywhere in the app -> lemma,
    gloss, and a short example, with enough info for the frontend to offer
    a one-tap "add to deck" (the add itself is ``POST /satz/cards``,
    unchanged).

    Four-tier lookup, cheapest first:
    0. SATZ-026: a closed-class function-word table (articles, pronouns,
       the commonest particles) — no DB, no LLM, and never wrong, so it
       runs before the cache too: a cache row poisoned by the old bug
       (e.g. "die" once wrongly cached as "Schuh") can never win again for
       one of these words. Marked ``glossable=False`` — the frontend hides
       "add to deck" for it.
    1. Shared Satzschmiede catalog — the hovered word IS a card's target as
       typed (no LLM).
    2. The ``word_glosses`` cache, keyed on the normalized surface form (no
       LLM) — every earlier hover of this exact inflected form, by any
       learner, already paid for the LLM call.
    3. A genuine miss: one structured-output LLM call
       (``satz/glosser.py``), rate-limited per user, then cached for every
       later hover of the same surface form.
    """
    word = " ".join(body.word.split())
    if not word:
        raise HTTPException(status_code=422, detail="Type a word first.")
    if " " in word:
        raise HTTPException(status_code=422, detail="Look up one word at a time.")
    if len(word) > 40:
        raise HTTPException(
            status_code=422, detail="That's too long to look up as one word."
        )

    context = (body.context or "").strip()
    if len(context) > 300:
        context = context[:300]

    # Cache key: whitespace already collapsed above — strip edge punctuation
    # and lowercase.
    lookup = word.strip(_EDGE_PUNCT).lower()
    if not lookup:
        raise HTTPException(status_code=422, detail="Type a word first.")

    with propagate_trace_context(user_id=user_id, session_id=body.session_id), tracer.start_as_current_span("satz-gloss-request") as span:
        span.set_attribute("user.id", user_id)
        if body.session_id:
            span.set_attribute("langfuse.session.id", body.session_id)
        span.set_attribute("langfuse.observation.input", word)

        # 0) SATZ-026: closed-class function words (articles, pronouns, the
        # commonest particles) are answered from a fixed table before ANY
        # DB lookup — see the tier list in the docstring above for why this
        # has to outrank even the cache.
        function_result = function_word_gloss(word, context or word)
        if function_result is not None:
            span.set_attribute("langfuse.observation.output", function_result.lemma)
            # REL-005: no provider call on this path — one short session is
            # all this branch needs.
            async with get_sessionmaker()() as db:
                gloss_adds_remaining = max(
                    0, GLOSS_ADDS_PER_DAY - await _gloss_adds_used_today(db, user_id)
                )
            return {
                "lemma": function_result.lemma,
                "article": function_result.article,
                "gloss": function_result.gloss,
                "example": function_result.example,
                "cardId": None,
                "inDeck": False,
                "source": "function",
                # Never offer "add to deck" for a function word — this is
                # the guard that makes a wrong article/pronoun gloss
                # harmless even if the table above were ever wrong.
                "glossable": False,
                "glossAddsRemaining": gloss_adds_remaining,
            }

        # REL-005: the catalog/cache reads run in their OWN short-lived
        # session, closed (connection released back to the pool) BEFORE
        # gloss_word's LLM call below (only reached on an actual miss) — a
        # request must never hold a pooled connection across a slow
        # provider call. No ``Depends(get_db)`` on this route for that
        # reason; same pattern ``briefkasten/routes.py::get_letter`` uses.
        need_llm = False
        async with get_sessionmaker()() as db:
            # 1) Catalog hit — the hovered word is already a catalog card's
            # target exactly as typed (e.g. hovering a word already in base
            # form). No LLM, no cache write.
            catalog_card = await _find_canonical(db, word)
            if catalog_card is not None:
                lemma, article = catalog_card.target, catalog_card.article
                gloss_text, example = catalog_card.gloss, catalog_card.example
                source = "catalog"
            else:
                # 2) Cache hit.
                cached = await db.scalar(
                    select(WordGloss).where(WordGloss.lookup == lookup)
                )
                if cached is not None:
                    lemma, article = cached.lemma, cached.article
                    gloss_text, example = cached.gloss, cached.example
                    source = "cache"
                else:
                    need_llm = True
        # `db` is closed here — the pooled connection is released before the
        # gloss_word call below (REL-005).

        if need_llm:
            # 3) Miss — one fresh LLM call, rate-limited per user (only
            # this branch ever consumes a slot).
            if not gloss_try_admit(user_id):
                raise HTTPException(
                    status_code=429,
                    detail="Slow down a little — try that word again in a minute.",
                )
            try:
                result = await gloss_word(
                    word,
                    context or word,
                    user_id=user_id,
                    session_id=body.session_id,
                )
            except Exception as exc:
                span.record_exception(exc)
                logger.exception("Satz gloss call failed for {!r}", word)
                raise HTTPException(
                    status_code=502,
                    detail="The dictionary is unavailable right now — try again in a moment.",
                )
            # REL-005: a second, fresh short-lived session for the cache
            # write, opened only now that gloss_word above has returned.
            async with get_sessionmaker()() as db:
                await db.execute(
                    pg_insert(WordGloss)
                    .values(
                        id=f"wg-{uuid4().hex[:12]}",
                        lookup=lookup,
                        lemma=result.lemma,
                        article=result.article,
                        gloss=result.gloss,
                        example=result.example,
                    )
                    .on_conflict_do_nothing(index_elements=["lookup"])
                )
                await db.commit()
                # Two hovers of the same word can race the insert — re-select
                # so both callers answer from whichever row actually landed.
                winner = await db.scalar(
                    select(WordGloss).where(WordGloss.lookup == lookup)
                )
                if winner is not None:
                    lemma, article = winner.lemma, winner.article
                    gloss_text, example = winner.gloss, winner.example
                else:
                    lemma, article = result.lemma, result.article
                    gloss_text, example = result.gloss, result.example
            source = "fresh"

        # Whatever the source, try to resolve a catalog card for the LEMMA
        # too, so the frontend can offer a one-tap "add to deck". No
        # provider call remains on this path, so one more short session
        # covers this read and the trailing gloss-quota read below.
        async with get_sessionmaker()() as db:
            card_for_lemma = await _find_canonical(db, lemma)
            card_id = None
            in_deck = False
            if card_for_lemma is not None:
                card_id = card_for_lemma.id
                in_deck = (
                    await db.scalar(
                        select(UserCard).where(
                            UserCard.user_id == user_id,
                            UserCard.card_id == card_for_lemma.id,
                        )
                    )
                ) is not None

            span.set_attribute("langfuse.observation.output", lemma)

            # SATZ-013: today's remaining gloss-path allowance, computed up
            # front so the popover can render "noch N heute" / the limit
            # line BEFORE the learner ever attempts an add.
            gloss_adds_remaining = max(
                0, GLOSS_ADDS_PER_DAY - await _gloss_adds_used_today(db, user_id)
            )

    return {
        "lemma": lemma,
        "article": article,
        "gloss": gloss_text,
        "example": example,
        "cardId": card_id,
        "inDeck": in_deck,
        "source": source,
        # SATZ-026: catalog/cache/fresh hits are real content words — always
        # glossable. Only the tier-0 function-word table above sets false.
        "glossable": True,
        "glossAddsRemaining": gloss_adds_remaining,
    }


# SATZ-008 P2: learner recourse — a "this verdict seems wrong" flag lands as a
# Langfuse score (name "user_disagree") on the attempt's own trace, so flags
# double as human ground truth for the judge-evaluator work (OBS-009).
_TRACE_ID_RE = re.compile(r"^[0-9a-f]{32}$")


class FlagIn(BaseModel):
    trace_id: str
    card_id: str | None = None
    transcript: str | None = None
    # Short client-built summary of what the examiner said, for the comment.
    verdict: str | None = None
    session_id: str | None = None


@router.post("/flag")
async def flag_verdict(
    body: FlagIn,
    user_id: str = Depends(get_current_user_id),
):
    """File a disagree-flag as a Langfuse score. No DB writes — the score
    lives with the trace it judges."""
    if not _TRACE_ID_RE.fullmatch(body.trace_id):
        raise HTTPException(status_code=422, detail="Bad trace id.")
    if len(body.transcript or "") > 600 or len(body.verdict or "") > 400:
        raise HTTPException(status_code=422, detail="Flag context too long.")
    if body.session_id and len(body.session_id) > 64:
        raise HTTPException(status_code=422, detail="Bad session id.")
    if not (langfuse_public_key and langfuse_secret_key and langfuse_base_url):
        raise HTTPException(
            status_code=503, detail="Flagging isn't available right now."
        )

    comment_parts = [f"user={user_id}"]
    if body.card_id:
        comment_parts.append(f"card={body.card_id}")
    if body.verdict:
        comment_parts.append(f"verdict: {body.verdict}")
    if body.transcript:
        comment_parts.append(f"transcript: {body.transcript}")

    payload = {
        "traceId": body.trace_id,
        "name": "user_disagree",
        "value": 1,
        "dataType": "NUMERIC",
        "comment": " | ".join(comment_parts),
    }
    url = f"{langfuse_base_url.rstrip('/')}/api/public/scores"
    try:
        timeout = aiohttp.ClientTimeout(total=10)
        auth = aiohttp.BasicAuth(langfuse_public_key, langfuse_secret_key)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=payload, auth=auth) as resp:
                if resp.status >= 300:
                    detail = await resp.text()
                    logger.warning(
                        "Langfuse score rejected ({}): {}", resp.status, detail[:300]
                    )
                    raise HTTPException(
                        status_code=502, detail="Couldn't record the flag — try again."
                    )
    except HTTPException:
        raise
    except Exception:
        logger.exception("Langfuse score POST failed")
        raise HTTPException(
            status_code=502, detail="Couldn't record the flag — try again."
        )
    return {"flagged": True}
