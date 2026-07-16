"""HTTP routes for Verbformen (GRAM-002, Exercise C: spoken past forms of
the learner's own verbs — the "13 triplets" flashcard drill).

The deck AUTO-FEEDS from the Satzschmiede pool: every verb added there brings
its spoken-past sibling card, and that sibling appears here with no extra
step. But the drill's state is LOCAL — schedule, reps and removals live on
the ``user_verbformen`` overlay, never on ``user_cards`` — so greening a verb
here doesn't silently advance its Satzschmiede schedule, and hiding one here
doesn't shrink the Satzschmiede pool. Attempt judging reuses the satz
examiner wholesale (same transcription, same "Perfekt OR Präteritum both
pass" verdict, same expanding-interval ladder); only where the schedule is
written differs. Slips feed the grammar-error ledger with
``source="verbformen"`` (design rule 4).
"""

import time
from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from loguru import logger
from sqlalchemy import and_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from agents.observability import tracer
from auth.deps import get_current_user_id
from database.connection import get_db
from database.orm import UserCard, UserVerbformen, VocabCard
from database.repository import record_grammar_error
from satz.examiner import examine_attempt, transcribe_attempt
from satz.scheduler import schedule

router = APIRouter(prefix="/verbformen", tags=["verbformen"])


def _card_payload(c: VocabCard) -> dict:
    """Serialize to the frontend ``Card`` contract — same shape as the satz
    deck (modules stay self-contained, like satz/ and bauteil/ are), so
    VocabTrainer renders these cards unchanged."""
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


def _srs_payload(uvf: UserVerbformen | None, now: datetime) -> dict:
    """Drill-local schedule state (frontend ``CardSrs``). No overlay row =
    the card just arrived from the auto-feed and was never drilled here —
    "new", regardless of how mature its Satzschmiede schedule is."""
    if uvf is None or uvf.due_at is None:
        status = "new"
    elif uvf.due_at <= now:
        status = "due"
    else:
        status = "later"
    return {
        "status": status,
        "dueAt": uvf.due_at.isoformat() if uvf and uvf.due_at else None,
        "intervalDays": uvf.interval_days if uvf else None,
        "reps": uvf.reps if uvf else 0,
    }


def _deck_row_query(user_id: str):
    """The auto-feed + overlay join: the caller's past-tense verb siblings,
    each with its (possibly absent) drill-local overlay row, hidden ones out."""
    return (
        select(VocabCard, UserVerbformen)
        .join(UserCard, UserCard.card_id == VocabCard.id)
        .outerjoin(
            UserVerbformen,
            and_(
                UserVerbformen.user_id == UserCard.user_id,
                UserVerbformen.card_id == VocabCard.id,
            ),
        )
        .where(
            UserCard.user_id == user_id,
            VocabCard.type == "verb",
            VocabCard.tense == "past",
            # IS NOT TRUE: no overlay row (NULL) and hidden=false both pass.
            UserVerbformen.hidden.isnot(True),
        )
    )


@router.get("/deck")
async def get_deck(
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """The Verbformen deck: every spoken-past sibling in the caller's pool
    (minus drill-local removals), oldest-added first, each carrying its
    OWN schedule — not the Satzschmiede one."""
    now = datetime.now()
    rows = (
        await db.execute(
            _deck_row_query(user_id).order_by(UserCard.added_at, VocabCard.id)
        )
    ).all()
    return {
        "cards": [
            {**_card_payload(c), "srs": _srs_payload(uvf, now)} for c, uvf in rows
        ]
    }


# Same single-spoken-sentence cap as /satz/attempts.
_MAX_AUDIO_BYTES = 2_500_000


@router.post("/attempts")
async def submit_attempt(
    card_id: str = Form(...),
    audio: UploadFile = File(...),
    # OBS-007: frontend-minted practice-sitting id ("vf-…") — groups every
    # attempt of one trainer mount into a single Langfuse Session.
    session_id: str | None = Form(None, max_length=64),
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Judge one spoken past form — the satz pipeline end to end (transcribe →
    examine → verdict), with the schedule written to the ``user_verbformen``
    overlay instead of the shared pool row."""
    row = (
        await db.execute(_deck_row_query(user_id).where(VocabCard.id == card_id))
    ).first()
    if row is None:
        raise HTTPException(
            status_code=404, detail="That verb isn't in your Verbformen deck."
        )
    card, uvf = row

    data = await audio.read()
    if not data:
        raise HTTPException(status_code=422, detail="We didn't get any audio — try again.")
    if len(data) > _MAX_AUDIO_BYTES:
        raise HTTPException(
            status_code=413,
            detail="That recording is too long — keep it to one sentence.",
        )

    # STT-003 P2: the learner says the past form ("ist gegangen"), not the
    # lemma — bias the transcription toward both.
    card_keyterms = [card.target]
    if card.tense_form:
        card_keyterms.append(card.tense_form)

    # OBS-007: one trace per judged attempt, named for the exercise like the
    # A/B/D siblings; `stt`/`llm` children nest automatically.
    with tracer.start_as_current_span("verbformen-attempt") as attempt_span:
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
            logger.exception("Verbformen transcription failed (card {})", card_id)
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
            # TASK 1: named span so this examiner call stops colliding with
            # Satzschmiede's "satz-judge" generation in Langfuse.
            judgement = await examine_attempt(
                card, transcript, span_name="verbformen-judge"
            )
        except Exception as exc:
            attempt_span.record_exception(exc)
            logger.exception("Verbformen examiner call failed (card {})", card_id)
            raise HTTPException(
                status_code=502,
                detail="The examiner is unavailable right now — try again in a moment.",
            )
        t_llm = time.perf_counter()
        logger.info(
            "Verbformen attempt timing: stt={:.2f}s llm={:.2f}s (card {})",
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

        # Record the outcome on the OVERLAY row — upserted, since the auto-fed
        # card may never have been drilled here before. `hidden` is left alone
        # on conflict (a hidden card can't be served, so it can't be attempted).
        interval, due_at = schedule(
            judgement.word_ok, uvf.interval_days if uvf else None, datetime.now()
        )
        await db.execute(
            pg_insert(UserVerbformen)
            .values(
                user_id=user_id,
                card_id=card_id,
                interval_days=interval,
                due_at=due_at,
                reps=1,
            )
            .on_conflict_do_update(
                index_elements=["user_id", "card_id"],
                set_={
                    "interval_days": interval,
                    "due_at": due_at,
                    "reps": UserVerbformen.reps + 1,
                },
            )
        )
        try:
            await db.commit()
        except Exception:
            logger.exception("Verbformen schedule commit failed (card {})", card_id)
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
                    source="verbformen",
                    session_id=session_id,
                )
            except Exception:
                logger.exception(
                    "Grammar-ledger write failed (pattern {})", judgement.pattern_id
                )

        # Same payload contract as /satz/attempts — VocabTrainer is reused.
        return {
            "transcript": transcript,
            "wordOk": judgement.word_ok,
            "grammarOk": judgement.grammar_ok,
            "error": judgement.error,
            "corrected": judgement.corrected,
            "dueInDays": interval,
        }


@router.delete("/deck/{card_id}")
async def remove_card(
    card_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Hide a verb from the Verbformen deck. Drill-local: the overlay row is
    flagged ``hidden``; the Satzschmiede pool row is NOT touched, so the verb
    keeps its place (and schedule) there."""
    row = (
        await db.execute(_deck_row_query(user_id).where(VocabCard.id == card_id))
    ).first()
    if row is None:
        raise HTTPException(
            status_code=404, detail="That verb isn't in your Verbformen deck."
        )
    await db.execute(
        pg_insert(UserVerbformen)
        .values(user_id=user_id, card_id=card_id, hidden=True)
        .on_conflict_do_update(
            index_elements=["user_id", "card_id"], set_={"hidden": True}
        )
    )
    await db.commit()
    return {"removed": 1}


@router.post("/deck/{card_id}/reveal")
async def reveal_card(
    card_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """The learner peeked at the example instead of attempting — a lapse,
    recorded on the overlay (drill-local): the card drops to "due now" HERE,
    while its Satzschmiede schedule keeps whatever interval it had."""
    row = (
        await db.execute(_deck_row_query(user_id).where(VocabCard.id == card_id))
    ).first()
    if row is None:
        raise HTTPException(
            status_code=404, detail="That verb isn't in your Verbformen deck."
        )
    now = datetime.now()
    await db.execute(
        pg_insert(UserVerbformen)
        .values(user_id=user_id, card_id=card_id, interval_days=0, due_at=now)
        .on_conflict_do_update(
            index_elements=["user_id", "card_id"],
            set_={"interval_days": 0, "due_at": now},
        )
    )
    await db.commit()
    return {"dueInDays": 0}
