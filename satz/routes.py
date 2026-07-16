"""HTTP routes for Satzschmiede (SATZ-002 — Phase 1: browse packs + read deck;
Phase 2: forge your own word + remove a card from the pool; Phase 3: speak a
sentence, get an examiner verdict; Phase 4: expanding-interval scheduling).

Every route resolves the caller via the session JWT (``get_current_user_id``)
and gets one ``AsyncSession`` per request (``get_db``) — the two dependencies
AUTH-001/DATA-001 left ready for exactly this. CORS is app-wide in ``main.py``,
so the Next.js origin needs no extra wiring here.
"""

import re
import time
from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import and_, delete, func, literal, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from agents.observability import tracer
from auth.deps import get_current_user_id
from database.connection import get_db
from database.orm import Pack, PackCard, User, UserCard, VocabCard
from database.repository import record_drill_attempt, record_grammar_error
from satz.content import _validate_card
from satz.enricher import EnrichedCard, enrich_word
from satz.examiner import examine_attempt, transcribe_attempt
from satz.scheduler import schedule

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
    if c.level:
        payload["level"] = c.level
    return payload


def _srs_payload(uc: UserCard, now: datetime) -> dict:
    """Per-user schedule state riding on each deck card (frontend ``CardSrs``).
    ``status`` is computed server-side so the client never compares clocks:
    "new" = never practiced, "due" = practice today, "later" = scheduled ahead.
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
_ID_PREFIX = {"noun": "n", "verb": "v", "phrase": "p", "adjective": "adj", "preposition": "prep"}


def _type_hint(word: str) -> str | None:
    """Best-effort card-type hint from the RAW input, to tell homographs apart
    (verb ``unternehmen`` vs. noun ``Unternehmen``). Returns ``"noun"``,
    ``"not-noun"`` (verb/adjective/preposition/phrase), or ``None`` when the
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


async def _forge_card(
    db: AsyncSession, word: str, user_id: str, *, session_id: str | None = None
) -> tuple[VocabCard, EnrichedCard]:
    """Catalog miss → enrich via LLM, validate against the curated card rules,
    insert a ``community`` canonical row. Flushed but not committed — the
    caller commits together with the pool link. Returns the enrichment too so
    a verb's past sibling can be forged without a second LLM call."""
    try:
        enriched = await enrich_word(word, user_id=user_id, session_id=session_id)
    except Exception:
        logger.exception("Satz enrichment call failed for {!r}", word)
        raise HTTPException(
            status_code=502,
            detail="The word forge is unavailable right now — try again in a moment.",
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
    rather than failing the add: the present card alone is still a win."""
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
        try:
            enriched = await enrich_word(
                present.target, user_id=user_id, session_id=session_id
            )
        except Exception:
            logger.exception(
                "Satz past-sibling enrichment failed for {!r}", present.target
            )
            return None
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

    added = 0
    for c in to_link:
        result = await db.execute(
            pg_insert(UserCard)
            .values(user_id=user_id, card_id=c.id)
            .on_conflict_do_nothing(index_elements=["user_id", "card_id"])
        )
        added += result.rowcount
    payload = _card_payload(card)  # serialize before commit expires the ORM row
    await db.commit()

    pool_size = await db.scalar(
        select(func.count()).select_from(UserCard).where(UserCard.user_id == user_id)
    )
    return {
        "card": payload,
        "created": created,
        "added": added,
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
    # OBS-007: frontend-minted practice-sitting id — groups every attempt of
    # one VocabTrainer mount into a single Langfuse Session (the analog of
    # the conversation's connect→disconnect session id). Optional so older
    # clients and curl keep working.
    session_id: str | None = Form(None, max_length=64),
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
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
    """
    row = (
        await db.execute(
            select(VocabCard, UserCard)
            .join(UserCard, UserCard.card_id == VocabCard.id)
            .where(UserCard.user_id == user_id, VocabCard.id == card_id)
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="That card isn't in your pool.")
    card, user_card = row

    data = await audio.read()
    if not data:
        raise HTTPException(status_code=422, detail="We didn't get any audio — try again.")
    if len(data) > _MAX_AUDIO_BYTES:
        raise HTTPException(
            status_code=413,
            detail="That recording is too long — keep it to one sentence.",
        )

    # STT-003 P2: the card names the exact word we expect to hear — the ideal
    # keyterm. For a past-tense card add the spoken past form too (the learner
    # says "ist gegangen", not the lemma "gehen").
    card_keyterms = [card.target]
    if card.tense_form:
        card_keyterms.append(card.tense_form)

    # OBS-006: one Langfuse trace per judged attempt. The `stt` and `llm`
    # child generations (opened inside transcribe_attempt / examine_attempt
    # — start_as_current_span nests them here automatically) split the
    # attempt's highly variable latency per stage; the perf_counter log line
    # below answers the same question locally when Langfuse is off.
    with tracer.start_as_current_span("satz-attempt") as attempt_span:
        attempt_span.set_attribute("user.id", user_id)
        attempt_span.set_attribute("card_id", card_id)
        if session_id:
            attempt_span.set_attribute("langfuse.session.id", session_id)

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
        attempt_span.set_attribute("langfuse.trace.input", transcript)

        try:
            judgement = await examine_attempt(card, transcript)
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
            "langfuse.trace.output",
            f"wordOk={judgement.word_ok} grammarOk={judgement.grammar_ok}"
            + (f" → {judgement.corrected}" if judgement.corrected else ""),
        )
        # Structured verdict attributes so Langfuse can filter without
        # string-parsing the free-text trace.output above.
        attempt_span.set_attribute("verdict.word_ok", bool(judgement.word_ok))
        attempt_span.set_attribute("verdict.grammar_ok", bool(judgement.grammar_ok))
        if judgement.pattern_id:
            attempt_span.set_attribute("verdict.pattern_id", judgement.pattern_id)

        # Record the outcome. A miss lands on (0, now) — still due, retryable
        # this session; a hit climbs the interval ladder.
        interval, due_at = schedule(
            judgement.word_ok, user_card.interval_days, datetime.now()
        )
        user_card.interval_days = interval
        user_card.due_at = due_at
        user_card.reps += 1
        user_card.last_score = 1 if judgement.word_ok else 0
        try:
            await db.commit()
        except Exception:
            logger.exception("Satz schedule commit failed (card {})", card_id)
            await db.rollback()

        # Harvest into the grammar-error ledger (GRAM-001) — its own commit,
        # after the schedule is safe; a ledger failure only logs.
        if judgement.pattern_id:
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

        # Append to the cross-drill attempt log (DATA-004) — its own commit,
        # non-fatal like the ledger write above; an attempt-log outage must
        # never break the practice attempt it rides on.
        try:
            await record_drill_attempt(
                db,
                user_id=user_id,
                exercise="satz",
                item_ref=card_id,
                pattern_id=judgement.pattern_id,
                correct=judgement.word_ok and judgement.grammar_ok,
                modality="spoken",
                session_id=session_id,
            )
        except Exception:
            logger.exception("Drill-attempt log write failed (card {})", card_id)

        # camelCase like every other satz payload (poolSize, cardCount, …).
        return {
            "transcript": transcript,
            "wordOk": judgement.word_ok,
            "grammarOk": judgement.grammar_ok,
            "error": judgement.error,
            "corrected": judgement.corrected,
            "dueInDays": interval,
        }


@router.get("/deck")
async def get_deck(
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """The caller's whole pool, oldest-added first, each card carrying its
    schedule state. One payload serves both trainer modes: the frontend builds
    today's practice queue from the due/new cards and keeps browse-all over
    the full list."""
    now = datetime.now()
    rows = (
        await db.execute(
            select(VocabCard, UserCard)
            .join(UserCard, UserCard.card_id == VocabCard.id)
            .where(UserCard.user_id == user_id)
            .order_by(UserCard.added_at, VocabCard.id)
        )
    ).all()
    return {
        "cards": [
            {**_card_payload(c), "srs": _srs_payload(uc, now)} for c, uc in rows
        ]
    }


@router.post("/deck/{card_id}/reveal")
async def reveal_card(
    card_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """The learner peeked at the example instead of attempting — a lapse.

    Drops the card to "due now" so the peek can't silently keep a long
    interval alive (the next green restarts the ladder honestly at 1 day).
    ``reps``/``last_score`` stay untouched: a reveal isn't a graded attempt.
    """
    user_card = await db.scalar(
        select(UserCard).where(
            UserCard.user_id == user_id, UserCard.card_id == card_id
        )
    )
    if user_card is None:
        raise HTTPException(status_code=404, detail="That card isn't in your pool.")
    user_card.interval_days = 0
    user_card.due_at = datetime.now()
    await db.commit()
    return {"dueInDays": 0}
