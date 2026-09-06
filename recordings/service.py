"""Orchestrates one voice-recording write: bucket upload, then the DB row
(REL-001). The one place every hook site calls into — the seven drill audio
routes (satz, sprechen, szenario, verbformen, both interview rounds, Clara's
speaking card), ``main.py``'s ``POST /tandem/say-audio`` (a Practice-mode
clip in a live session) and ``pipeline/factory.py``'s voice-session
disconnect path (every session MP3, the demo's included since the REL-001
follow-up of 2026-09-05) — so the upload-then-record ordering and the
non-fatal contract live in one spot instead of being re-implemented nine
times.

Two entry points, same underlying work, different scheduling:

- :func:`schedule_recording` — for an HTTP route. Takes the route's own
  ``BackgroundTasks`` and queues the work to run AFTER the response is
  already on the wire, so a slow upload or DB write never touches the
  learner's latency (REL-001 build note 4).
- :func:`save_recording_now` — for ``pipeline/factory.py``, which has no
  ``BackgroundTasks`` (it's not a route). Callers there wrap this in their
  own ``asyncio.create_task(...)`` — the same fire-and-forget discipline
  ``szenario/routes.py`` and ``interview/routes.py`` already use for their
  background harvests — so the disconnect path is never held up either.

Both are no-ops (return immediately, nothing scheduled, nothing logged
beyond the ONE warning ``recordings/store.py`` already emits on first use)
when the bucket isn't configured — REL-001's "absent -> inert, never an
error, never a slower attempt" contract.

``ref_kind`` is free text (``database/orm.py``'s ``VoiceRecording.ref_kind``
column, no DB enum/CHECK constraint), not a ``Literal`` — a call site can
introduce a new kind without touching this module. The vocabulary in use
today, one call site per kind:

  - ``"drill_attempt"`` — ``ref_id`` is ``drill_attempts.id`` (as text).
    satz/sprechen/szenario submit routes, the interview round-2 harvest.
  - ``"activity_session"`` — ``ref_id`` is the bare session hex.
    ``pipeline/factory.py``'s voice-session disconnect hook.
  - ``"teacher_exercise"`` — ``ref_id`` is the Clara exercise item's own
    id (no DB row — that room writes nothing to learning state).
    ``teacher/routes.py``'s attempts-audio route.
  - ``"interview_comprehension"`` — ``ref_id`` is the chunk id. Round 1
    ("listen & retell") persists no grading row of its own, so every
    clip is kept and the chunk id is the only thing to point at.
  - ``"interview_answer"`` — ``ref_id`` is the chunk id, for EVERY round-2
    answer, clean or with a harvested slip. Deliberately not linked to
    the harvested slip's own ``drill_attempt`` row — join by user + time
    + chunk id instead if that's ever needed.
  - ``"satz_rehearsal"`` — ``ref_id`` is the card id. A ``rehearsal=True``
    attempt writes no ``drill_attempts`` row by design (SATZ-015), so
    every rehearsal clip points at the card instead.
  - ``"session_turn"`` (REL-001 follow-up, P2-IMPL) — ``ref_id`` is the bare
    session hex, same identifier ``"activity_session"`` uses, but ONE row
    per Practice-mode clip rather than one for the whole session's MP3.
    ``item_id`` is the 1-based exchange number so clips sort in spoken
    order. ``main.py``'s ``POST /tandem/say-audio`` — a mic-in clip for a
    live tandem/teacher session, injected as text rather than streamed, so
    it would otherwise never reach the archive at all.
"""

from __future__ import annotations

from typing import Optional
from uuid import uuid4

from fastapi import BackgroundTasks
from loguru import logger
from sqlalchemy.exc import SQLAlchemyError

from database.connection import get_sessionmaker
from database.repository import record_voice_recording

from . import store


async def _upload_and_record(
    *,
    user_id: str,
    surface: str,
    exercise: Optional[str],
    ref_kind: str,
    ref_id: str,
    item_id: Optional[str],
    data: bytes,
    content_type: Optional[str],
    duration_ms: Optional[int],
    transcript: Optional[str],
) -> None:
    """The actual upload-then-record work, run off whatever critical path
    the caller already got us off of. Never raises — a recording write must
    never surface anywhere a learner (or another background task) can see
    it fail."""
    try:
        recording_id = uuid4().hex
        ext = store.ext_for_content_type(content_type)
        key = store.build_key(user_id, surface, recording_id, ext)
        ok = await store.upload_bytes(key, data, content_type or "application/octet-stream")
        if not ok:
            return
        # REL-005: a fresh, short-lived session for this one INSERT — never
        # the request's own `db` (long since closed by the time a
        # BackgroundTasks job runs) and never held open around the upload
        # above, which already finished.
        async with get_sessionmaker()() as db:
            await record_voice_recording(
                db,
                recording_id=recording_id,
                user_id=user_id,
                surface=surface,
                exercise=exercise,
                ref_kind=ref_kind,
                ref_id=ref_id,
                item_id=item_id,
                bucket_key=key,
                content_type=content_type or "application/octet-stream",
                size_bytes=len(data),
                duration_ms=duration_ms,
                transcript=transcript,
            )
    except SQLAlchemyError as exc:
        logger.warning(f"voice recording DB write failed (non-fatal): {exc}")
    except Exception as exc:  # noqa: BLE001 -- a recording write must never break anything downstream
        logger.warning(f"voice recording save failed (non-fatal): {type(exc).__name__}: {exc}")


def schedule_recording(
    background_tasks: BackgroundTasks,
    *,
    user_id: str,
    surface: str,
    exercise: Optional[str] = None,
    ref_kind: str,
    ref_id: str,
    item_id: Optional[str] = None,
    data: bytes,
    content_type: Optional[str],
    duration_ms: Optional[int] = None,
    transcript: Optional[str] = None,
) -> None:
    """Queue the upload + DB write as a FastAPI background task. Call this
    ONLY after the response is fully decided (a graded verdict, not a 404/
    402/422 — "never store a clip for a request that was rejected before
    grading", REL-001) — by construction, the caller has already read
    `data` for STT, so no extra work happens on the request path here
    beyond the `is_configured()` check and queueing itself.
    """
    if not store.is_configured():
        return
    background_tasks.add_task(
        _upload_and_record,
        user_id=user_id,
        surface=surface,
        exercise=exercise,
        ref_kind=ref_kind,
        ref_id=ref_id,
        item_id=item_id,
        data=data,
        content_type=content_type,
        duration_ms=duration_ms,
        transcript=transcript,
    )


async def save_recording_now(
    *,
    user_id: str,
    surface: str,
    exercise: Optional[str] = None,
    ref_kind: str,
    ref_id: str,
    item_id: Optional[str] = None,
    data: bytes,
    content_type: Optional[str],
    duration_ms: Optional[int] = None,
    transcript: Optional[str] = None,
) -> None:
    """Same work as :func:`schedule_recording`, but awaited directly rather
    than queued via ``BackgroundTasks`` — for non-route callers (the voice-
    session disconnect path in ``pipeline/factory.py``). The caller is
    responsible for not awaiting this on its own critical path: wrap it in
    ``asyncio.create_task(...)`` at the call site instead."""
    if not store.is_configured():
        return
    await _upload_and_record(
        user_id=user_id,
        surface=surface,
        exercise=exercise,
        ref_kind=ref_kind,
        ref_id=ref_id,
        item_id=item_id,
        data=data,
        content_type=content_type,
        duration_ms=duration_ms,
        transcript=transcript,
    )
