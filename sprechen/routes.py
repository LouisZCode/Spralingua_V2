"""HTTP routes for Sprechen & transkribieren (GRAM-002, Exercise B: a
constrained speaking prompt, the learner speaks, the raw transcription is
judged against the target structure — speaking reveals load where writing
hides it).

Reuses the Satzschmiede attempt machinery wholesale: the browser records one
clip, ``satz/examiner.py::transcribe_attempt`` turns it into a raw Deepgram
transcript (with per-task keyterm biasing), and one structured-output call
judges constraint + target structure. Ledger + tracing contracts mirror
``bauteil/routes.py``.
"""

import random
import time

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from agents.observability import tracer
from auth.deps import get_current_user_id
from database.connection import get_db
from database.repository import (
    credit_pattern_success,
    load_grammar_focus,
    record_grammar_error,
)
from satz.examiner import transcribe_attempt
from sprechen.content import TARGET_PATTERNS, load_tasks
from sprechen.judge import judge_spoken

router = APIRouter(prefix="/sprechen", tags=["sprechen"])

# Three tasks per round: each is a multi-sentence production, so a round is
# already a real workout — and one task per pattern keeps the variety up.
ROUND_SIZE = 3

# Multi-sentence answers, but still under a minute of speech — same ceiling
# as the Satzschmiede attempt cap (opus/aac stays well under this).
_MAX_AUDIO_BYTES = 2_500_000


@router.get("/round")
async def get_round(
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Three tasks, at most one per pattern, the learner's hot ledger patterns
    first. The `forces` field stays server-side — the learner sees the task,
    the judge sees the rubric."""
    tasks = list(load_tasks().values())
    try:
        focus = await load_grammar_focus(db, user_id=user_id, limit=10)
        hot = {f["pattern_id"] for f in focus} & set(TARGET_PATTERNS)
    except Exception:
        logger.exception("Sprechen focus read failed — serving an unweighted round")
        hot = set()

    # One variant per pattern (each pattern has 2+ task phrasings), then hot
    # patterns lead the pick.
    by_pattern: dict[str, list[dict]] = {}
    for t in tasks:
        by_pattern.setdefault(t["pattern_id"], []).append(t)
    candidates = [random.choice(v) for v in by_pattern.values()]
    random.shuffle(candidates)
    candidates.sort(key=lambda t: t["pattern_id"] not in hot)  # stable: hot first
    chosen = candidates[:ROUND_SIZE]
    return {
        "tasks": [
            {"id": t["id"], "title": t["title"], "prompt": t["prompt"]}
            for t in chosen
        ]
    }


@router.post("/attempts")
async def submit_attempt(
    task_id: str = Form(...),
    audio: UploadFile = File(...),
    # OBS-007: frontend-minted practice-sitting id — one Langfuse Session per
    # trainer visit, same contract as /satz/attempts and /bauteil/attempts.
    session_id: str | None = Form(None, max_length=64),
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Transcribe one spoken clip, judge constraint + target structure, feed
    the ledger, return transcript + verdict (the raw transcript is part of
    the format — seeing what you actually said is the exercise)."""
    task = load_tasks().get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Unknown task.")

    data = await audio.read()
    if not data:
        raise HTTPException(status_code=422, detail="We didn't get any audio — try again.")
    if len(data) > _MAX_AUDIO_BYTES:
        raise HTTPException(
            status_code=413,
            detail="That recording is too long — a few sentences is enough.",
        )

    with tracer.start_as_current_span("sprechen-attempt") as attempt_span:
        attempt_span.set_attribute("user.id", user_id)
        attempt_span.set_attribute("task_id", task["id"])
        if session_id:
            attempt_span.set_attribute("langfuse.session.id", session_id)

        t0 = time.perf_counter()
        try:
            transcript = await transcribe_attempt(
                data, audio.content_type, keyterms=task.get("keyterms")
            )
        except Exception as exc:
            attempt_span.record_exception(exc)
            logger.exception("Sprechen transcription failed (task {})", task_id)
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
            verdict = await judge_spoken(task, transcript)
        except Exception as exc:
            attempt_span.record_exception(exc)
            logger.exception("Sprechen judge call failed (task {})", task_id)
            raise HTTPException(
                status_code=502,
                detail="The judge is unavailable right now — try again in a moment.",
            )
        logger.info(
            "Sprechen attempt timing: stt={:.2f}s llm={:.2f}s (task {})",
            t_stt - t0,
            time.perf_counter() - t_stt,
            task_id,
        )

        passed = verdict.constraint_met and not verdict.slips
        attempt_span.set_attribute(
            "langfuse.trace.output",
            f"passed={passed} constraintMet={verdict.constraint_met} "
            f"hits={verdict.hits} slips={len(verdict.slips)}",
        )
        # Structured verdict attributes so Langfuse can filter without
        # string-parsing the free-text trace.output above.
        attempt_span.set_attribute("verdict.constraint_met", bool(verdict.constraint_met))
        attempt_span.set_attribute("verdict.hits", verdict.hits)
        attempt_span.set_attribute("verdict.slips_count", len(verdict.slips))
        attempt_span.set_attribute("verdict.pattern_id", task["pattern_id"])

        # Feed the ledger (design rule 4) — non-fatal. One write per attempt:
        # the ledger tracks patterns, not slip counts within one production.
        try:
            if passed:
                await credit_pattern_success(
                    db,
                    user_id=user_id,
                    pattern_id=task["pattern_id"],
                    session_id=session_id,
                    source="sprechen",
                )
            elif verdict.slips:
                first = verdict.slips[0]
                await record_grammar_error(
                    db,
                    user_id=user_id,
                    pattern_id=task["pattern_id"],
                    sentence=first.quote,
                    corrected=first.corrected,
                    note=first.note,
                    source="sprechen",
                    session_id=session_id,
                )
            # Constraint not met with zero slips = the learner dodged the task
            # (didn't attempt the structure) — no evidence either way, no write.
        except Exception:
            logger.exception(
                "Sprechen ledger write failed (pattern {})", task["pattern_id"]
            )

        return {
            "transcript": transcript,
            "passed": passed,
            "constraintMet": verdict.constraint_met,
            "constraintNote": verdict.constraint_note,
            "hits": verdict.hits,
            "slips": [
                {"quote": s.quote, "corrected": s.corrected, "note": s.note}
                for s in verdict.slips
            ],
        }
