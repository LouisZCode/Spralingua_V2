"""HTTP routes for the interview exercise (INTV-003 slice 2).

Serves the personal audio pool slice 1 registered into Postgres
(``audio_items``/``audio_chunks``, imported by
``scripts/import_interviews.py`` from the local-only workbench,
``interview_local/``) plus the two-round "listen & retell" / "read &
answer" exercise ported from ``interview_local/app.py``. Every route is
scoped to the caller's own rows: an item/chunk owned by someone else 404s
(unknown id) or 403s (known id, wrong owner) — same convention
``GET /sessions/{session_id}`` in ``main.py`` uses.

Persistence is intentionally thin (v1 scope): no per-attempt rows for
Round 1 (the retell is graded on content only, never grammar) and no
streak credit for either round. Round 2's answer writes to the shared
grammar-error ledger AND, per harvested slip, a ``drill_attempts`` row
(``pattern_id`` + ``correct=False``) — the same shape the single-grammar
drills use — so an interview mistake surfaces in ``/me/stats``' topErrors
and count7d recommendation, not just the ledger-driven focus list. See
``_background_harvest``.
"""

import asyncio
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import RedirectResponse
from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agents.error_extractor import extract_errors
from agents.observability import tracer
from auth.deps import get_current_user_id
from database.connection import get_db, get_sessionmaker
from database.orm import AudioChunk, AudioItem
from database.repository import record_drill_attempt, record_grammar_error
from security import drill_try_admit

from .bucket import presigned_audio_url
from .display import classify_kind, company_display, dir_name_from_storage_prefix, interviewer_display
from .judges.comprehension import judge_comprehension
from .judges.goal_coverage import judge_goal_coverage
from .judges.grammar import judge_grammar
from .judges.idiom import judge_idiom
from .transcribe import TRANSCRIPT_MAX_LEN, keyword_boosts, transcribe_answer, transcribe_comprehension

router = APIRouter(prefix="/interview", tags=["interview"])

# One whole spoken retell/answer per upload — same order-of-magnitude cap as
# /tandem/say-audio's _TANDEM_AUDIO_MAX_BYTES (main.py). The workbench had no
# cap at all (a local-only, single-operator tool); this is a slice-2 addition
# for the same cost-abuse reason every other paid drill route in this repo
# caps its upload.
_MAX_AUDIO_BYTES = 4_000_000

# Strong refs to in-flight background harvests so the event loop never
# garbage-collects one mid-run — same fire-and-forget discipline
# szenario/routes.py and briefkasten/routes.py use. Each task discards
# itself on completion.
_background_tasks: set[asyncio.Task] = set()


async def _background_harvest(
    transcript: str, session_id: Optional[str], user_id: str, chunk_id: str
) -> None:
    """Fire-and-forget grammar harvest off the answer response's critical
    path (INTV-003 slice 2's ledger write, slice 3B's per-slip
    ``drill_attempts`` write).

    Reuses ``agents/error_extractor.py::extract_errors`` — the SAME
    situation-drill harvester ``pipeline/factory.py`` (post-session),
    ``szenario/routes.py`` and ``briefkasten/routes.py`` already use — so a
    wrong taxonomy pattern id can never reach ``user_errors`` from an
    invented free-text mapping (CLAUDE.md: "a wrong pattern id written to
    the ledger is worse than a wrong verdict"). Opens its own DB session:
    the request-scoped one closes the instant the response returns. Same
    non-fatal, log-and-swallow contract as every other ledger write in the
    repo.

    Each harvested slip also writes a ``drill_attempts`` row
    (``pattern_id`` + ``correct=False``) — unlike szenario/briefkasten,
    whose harvests only ever call ``record_grammar_error`` with
    ``pattern_id`` left NULL on their own attempt-log rows, so their slips
    populate the ledger-driven ``focus`` list but never
    ``load_top_errors``/``load_focus_with_recency``'s ``count7d``, which
    both require a ``drill_attempts`` row with a non-null ``pattern_id``
    and ``correct=False`` (``database/repository.py``). This is the
    approved SLICE B change: interview is the first harvested-error
    surface to write that row, so its mistakes actually surface in
    ``/me/stats``.
    """
    try:
        extraction = await extract_errors(transcript=transcript, session_id=session_id)
        async with get_sessionmaker()() as db:
            for err in extraction.errors:
                try:
                    await record_grammar_error(
                        db,
                        user_id=user_id,
                        pattern_id=err.pattern_id,
                        sentence=err.sentence,
                        corrected=err.corrected,
                        note=err.note,
                        source="interview",
                        session_id=session_id,
                    )
                except Exception:  # noqa: BLE001 — one row must not block the rest
                    logger.exception(
                        "Interview ledger write failed (pattern {})", err.pattern_id
                    )
                # DATA-004, independent of the ledger write above (its own
                # try/except — one failing must never block the other):
                # the row that makes this pattern count toward topErrors
                # and count7d, same shape faelle/verbindungen/etc. write
                # per graded attempt.
                try:
                    await record_drill_attempt(
                        db,
                        user_id=user_id,
                        exercise="interview",
                        item_ref=chunk_id,
                        pattern_id=err.pattern_id,
                        correct=False,
                        modality="spoken",
                        session_id=session_id,
                    )
                except Exception:  # noqa: BLE001 — one row must not block the rest
                    logger.exception(
                        "Interview drill-attempt log write failed (pattern {})",
                        err.pattern_id,
                    )
    except Exception:  # noqa: BLE001 — the harvest must never break the attempt
        logger.warning("Interview grammar extraction failed (non-fatal)")


async def _get_owned_chunk(db: AsyncSession, chunk_id: str, user_id: str) -> tuple[AudioChunk, AudioItem]:
    """Look up one chunk + its parent item, 404 if either doesn't exist,
    403 if the item belongs to someone else — same convention
    ``GET /sessions/{session_id}`` (main.py) uses for a mismatched owner."""
    chunk = await db.get(AudioChunk, chunk_id)
    if chunk is None:
        raise HTTPException(status_code=404, detail="chunk not found")
    item = await db.get(AudioItem, chunk.item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="chunk not found")
    if item.owner_user_id != user_id:
        raise HTTPException(status_code=403, detail="not your interview")
    return chunk, item


async def _run_judges(
    question: str,
    transcript: str,
    goals: Optional[list[str]] = None,
    goal_question: Optional[str] = None,
) -> tuple[
    Optional[dict], Optional[str], Optional[dict], Optional[str], Optional[list], Optional[str]
]:
    """Run grammar + idiom concurrently, same as always, PLUS goal-coverage
    when `goals` is non-empty (round 2 answer against a briefed chunk).
    Ported verbatim from ``interview_local/app.py::_run_judges``. Each leg
    is wrapped so one failing judge degrades gracefully -- the OTHER
    judges' results still come back, plus a short error string for the leg
    that failed. Returns (grammar_dict, grammar_error, idiom_dict,
    idiom_error, goal_coverage_list, goal_coverage_error); the last two are
    (None, None) whenever `goals` is falsy, so a brief-less chunk's
    response shape is byte-identical to before goal coverage existed.
    """

    async def _safe_grammar():
        try:
            verdict = await judge_grammar(question, transcript)
            return verdict.model_dump(), None
        except Exception as exc:  # noqa: BLE001 -- one failing judge must not sink the others
            logger.warning(f"grammar judge failed: {exc}")
            return None, str(exc)

    async def _safe_idiom():
        try:
            verdict = await judge_idiom(question, transcript)
            return verdict.model_dump(), None
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"idiom judge failed: {exc}")
            return None, str(exc)

    async def _safe_goal_coverage():
        try:
            verdict = await judge_goal_coverage(transcript, goals, goal_question or question)
            return [item.model_dump() for item in verdict.items], None
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"goal coverage judge failed: {exc}")
            return None, str(exc)

    tasks = [_safe_grammar(), _safe_idiom()]
    if goals:
        tasks.append(_safe_goal_coverage())

    results = await asyncio.gather(*tasks)
    (grammar_result, grammar_error), (idiom_result, idiom_error) = results[0], results[1]
    if goals:
        goal_coverage_result, goal_coverage_error = results[2]
    else:
        goal_coverage_result, goal_coverage_error = None, None
    return grammar_result, grammar_error, idiom_result, idiom_error, goal_coverage_result, goal_coverage_error


@router.get("/items")
async def list_items(
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Caller's own audio_items, with a company/interviewer display label
    (see interview/display.py) and a chunk count per item."""
    rows = (
        await db.execute(
            select(AudioItem, func.count(AudioChunk.id))
            .outerjoin(AudioChunk, AudioChunk.item_id == AudioItem.id)
            .where(AudioItem.owner_user_id == user_id)
            .group_by(AudioItem.id)
            .order_by(AudioItem.created_at)
        )
    ).all()

    out = []
    for item, n_chunks in rows:
        dir_name = dir_name_from_storage_prefix(item.storage_prefix)
        out.append(
            {
                "id": item.id,
                "title": item.title,
                "company": company_display(dir_name),
                "interviewer": interviewer_display(dir_name),
                "level": item.level,
                "language": item.language,
                "n_chunks": n_chunks,
                "created_at": item.created_at.isoformat() if item.created_at else None,
            }
        )
    return out


@router.get("/items/{item_id}")
async def get_item(
    item_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """One item's full detail + its chunks, ordered by position, each with
    its heuristically-classified `kind` (interview/display.py::classify_kind)."""
    item = await db.get(AudioItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="interview not found")
    if item.owner_user_id != user_id:
        raise HTTPException(status_code=403, detail="not your interview")

    chunks = (
        await db.execute(
            select(AudioChunk).where(AudioChunk.item_id == item_id).order_by(AudioChunk.position)
        )
    ).scalars().all()

    dir_name = dir_name_from_storage_prefix(item.storage_prefix)
    return {
        "id": item.id,
        "title": item.title,
        "company": company_display(dir_name),
        "interviewer": interviewer_display(dir_name),
        "level": item.level,
        "language": item.language,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "chunks": [
            {
                "id": c.id,
                "position": c.position,
                "transcript": c.transcript,
                "segments": c.segments,
                "brief": c.brief,
                "duration_s": c.duration_s,
                "kind": classify_kind(c.transcript, c.duration_s),
            }
            for c in chunks
        ],
    }


@router.get("/audio/{chunk_id}")
async def get_audio(
    chunk_id: str,
    redirect: bool = True,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """307-redirect to a ~15-min presigned GET of the chunk's mp3 in the
    Railway Bucket. 503 (not a disk fallback — see interview/bucket.py) if
    the bucket is unconfigured or the presign call fails.

    ``?redirect=0`` returns ``{"url": ...}`` instead: the session JWT rides
    an Authorization header (AUTH-001), which an ``<audio>`` element can't
    send — so the frontend fetches the URL with the header and hands it to
    the media element, whose (non-CORS-gated) load streams straight from
    the bucket, Range/seek included."""
    chunk, _item = await _get_owned_chunk(db, chunk_id, user_id)

    url = presigned_audio_url(chunk.storage_key)
    if url is None:
        raise HTTPException(
            status_code=503,
            detail="Audio storage is temporarily unavailable — try again in a moment.",
        )
    if not redirect:
        return {"url": url}
    return RedirectResponse(url=url, status_code=307)


@router.post("/comprehension/{chunk_id}")
async def post_comprehension(
    chunk_id: str,
    audio: UploadFile = File(...),
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Round 1 ("listen & retell"): the learner hears one chunk WITHOUT
    ever seeing the transcript and retells in German what was said (and,
    if there's a question, what's being asked). Ported from
    ``interview_local/app.py::post_comprehension``. No persistence (v1
    scope) — response shape matches the workbench's exactly."""
    if not drill_try_admit(user_id):
        raise HTTPException(
            status_code=429,
            detail="You're going very fast — take a short break and try again in a few minutes.",
        )

    chunk, _item = await _get_owned_chunk(db, chunk_id, user_id)

    brief = chunk.brief
    if not brief:
        raise HTTPException(status_code=422, detail="chunk has no brief; round 1 is not available")
    summary = (brief.get("summary") or "").strip()
    if not summary:
        raise HTTPException(status_code=422, detail="brief has no summary; round 1 is not available")

    shape = brief.get("shape") or ""
    # Only an EXTRACTED question was actually spoken in this chunk's own
    # audio -- a "generated" one is a brief-writer's invented round-2
    # follow-up the learner never heard in round 1, so the retell must
    # never be graded on catching it.
    brief_question = brief.get("question") or {}
    question_text = brief_question.get("text") or "" if brief_question.get("source") == "extracted" else ""

    audio_bytes = await audio.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="empty audio upload")
    if len(audio_bytes) > _MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="That recording is too long — try a shorter retell.")

    try:
        keywords = keyword_boosts(chunk.transcript or "", brief_question.get("text") or "")
    except Exception as exc:  # noqa: BLE001 -- keyword-boost construction must never break round 1
        logger.warning(f"keyword boost construction failed for chunk {chunk_id}: {exc}")
        keywords = []

    try:
        transcript = await transcribe_comprehension(audio_bytes, audio.content_type, keywords)
    except Exception as exc:  # noqa: BLE001 -- a Deepgram outage must 502, not 500
        logger.warning(f"interview comprehension transcription failed: {exc}")
        raise HTTPException(status_code=502, detail="Couldn't process the audio — try again in a moment.")
    if not transcript:
        return {"transcript": "", "error": "no_speech"}
    if len(transcript) > TRANSCRIPT_MAX_LEN:
        raise HTTPException(status_code=422, detail="transcript too long")

    response = {"transcript": transcript}
    with tracer.start_as_current_span("interview-comprehension") as span:
        span.set_attribute("user.id", user_id)
        span.set_attribute("chunk_id", chunk_id)
        span.set_attribute("langfuse.trace.input", transcript)
        try:
            verdict = await judge_comprehension(transcript, shape, summary, question_text)
            response["comprehension"] = verdict.model_dump()
            span.set_attribute("verdict.passed", verdict.passed)
        except Exception as exc:  # noqa: BLE001 -- a failing judge must not crash round 1
            span.record_exception(exc)
            logger.warning(f"interview comprehension judge failed: {exc}")
            response["comprehension_error"] = str(exc)
    return response


@router.post("/answer/{chunk_id}")
async def post_answer(
    chunk_id: str,
    audio: UploadFile = File(...),
    # Optional client-minted practice-sitting id (OBS-007 convention, same
    # as satz/bauteil/sprechen/szenario) — rides into the grammar-harvest
    # Langfuse trace grouping and the ledger's last_session_id, purely for
    # observability; nothing here requires it.
    session_id: Optional[str] = Form(None, max_length=64),
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Round 2 ("read & answer"): transcribe one spoken answer to a chunk,
    judge grammar + idiom (+ goal coverage when the chunk's brief has
    goals), and silently harvest grammar errors into the shared ledger
    plus a per-slip ``drill_attempts`` row. Ported from
    ``interview_local/app.py::post_answer``. Still v1 scope beyond that:
    no streak credit, and Round 1's retell writes neither."""
    if not drill_try_admit(user_id):
        raise HTTPException(
            status_code=429,
            detail="You're going very fast — take a short break and try again in a few minutes.",
        )

    chunk, _item = await _get_owned_chunk(db, chunk_id, user_id)

    audio_bytes = await audio.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="empty audio upload")
    if len(audio_bytes) > _MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="That recording is too long — one answer is enough.")

    try:
        transcript = await transcribe_answer(audio_bytes, audio.content_type)
    except Exception as exc:  # noqa: BLE001 -- a Deepgram outage must 502, not 500
        logger.warning(f"interview answer transcription failed: {exc}")
        raise HTTPException(status_code=502, detail="Couldn't process the audio — try again in a moment.")
    if not transcript:
        return {"transcript": "", "error": "no_speech"}
    if len(transcript) > TRANSCRIPT_MAX_LEN:
        raise HTTPException(status_code=422, detail="transcript too long")

    question = chunk.transcript or ""
    brief = chunk.brief
    if not brief:
        logger.warning(f"legacy round-2 fallback (no brief): chunk {chunk_id}")
    goals = (brief or {}).get("goals") or []
    goal_question = ((brief or {}).get("question") or {}).get("text") or question

    with tracer.start_as_current_span("interview-answer") as span:
        span.set_attribute("user.id", user_id)
        span.set_attribute("chunk_id", chunk_id)
        if session_id:
            span.set_attribute("langfuse.session.id", session_id)
        span.set_attribute("langfuse.trace.input", transcript)

        (
            grammar_result,
            grammar_error,
            idiom_result,
            idiom_error,
            goal_coverage_result,
            goal_coverage_error,
        ) = await _run_judges(question, transcript, goals or None, goal_question)

    response = {
        "transcript": transcript,
        "grammar": grammar_result,
        "idiom": idiom_result,
        "at": datetime.now(timezone.utc).isoformat(),
    }
    if grammar_error:
        response["grammar_error"] = grammar_error
    if idiom_error:
        response["idiom_error"] = idiom_error
    if goals:
        response["goal_coverage"] = goal_coverage_result
        if goal_coverage_error:
            response["goal_coverage_error"] = goal_coverage_error

    # SILENT grammar enrichment — the one place this exercise writes
    # learning state (ledger + per-slip drill_attempts, SLICE B). Fire-and-
    # forget, off the response's critical path, same as szenario/
    # briefkasten's own background harvests.
    harvest_task = asyncio.create_task(
        _background_harvest(transcript, session_id, user_id, chunk_id)
    )
    _background_tasks.add(harvest_task)
    harvest_task.add_done_callback(_background_tasks.discard)

    return response
