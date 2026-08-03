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
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from agents.observability import tracer
from auth.deps import get_current_user_id
from database.connection import get_db
from database.repository import (
    credit_pattern_success,
    load_grammar_focus,
    load_recent_attempt_item_ids,
    record_drill_attempt,
    record_grammar_error,
)
from satz.examiner import transcribe_attempt
from sprechen.content import TARGET_PATTERNS, load_tasks
from sprechen.judge import judge_spoken
from sprechen.nudge import suggest_vocab
from vocab_nudge import filter_picks, load_deck

router = APIRouter(prefix="/sprechen", tags=["sprechen"])

# Three tasks per round: each is a multi-sentence production, so a round is
# already a real workout — and one task per pattern keeps the variety up.
ROUND_SIZE = 3

# Multi-sentence answers, but still under a minute of speech — same ceiling
# as the Satzschmiede attempt cap (opus/aac stays well under this).
_MAX_AUDIO_BYTES = 2_500_000

# VARY-002: tasks attempted this recently count as "seen" server-side, so the
# same prompt doesn't resurface day after day — regardless of what (if
# anything) the client echoes back as seenTasks.
_COOLDOWN_HOURS = 48


def _pick_round(
    tasks: list[dict], hot: set[str], seen_task_ids: list[str]
) -> tuple[list[dict], bool]:
    """Pick ROUND_SIZE tasks, at most one per pattern, favoring phrasings the
    client hasn't already been served this pool cycle (VARY-001) ahead of the
    learner's hot ledger patterns. ``seen_task_ids`` are client-echoed task
    ids; unknown ids are silently dropped.

    If every phrasing of every pattern has been seen, the pool resets for
    this pick (``seen_task_ids`` is treated as empty) — reported via the
    returned cycle_reset flag.
    """
    known_ids = {t["id"] for t in tasks}
    seen_set = {tid for tid in seen_task_ids if tid in known_ids}

    # One variant per pattern (each pattern has 2+ task phrasings).
    by_pattern: dict[str, list[dict]] = {}
    for t in tasks:
        by_pattern.setdefault(t["pattern_id"], []).append(t)

    # Reset only when literally nothing is left unseen — "every pattern has
    # a seen task" is implied by this, so it needs no separate check.
    cycle_reset = all(t["id"] in seen_set for t in tasks)
    if cycle_reset:
        seen_set = set()

    def pick_variant(variants: list[dict]) -> dict:
        unseen = [t for t in variants if t["id"] not in seen_set]
        return random.choice(unseen or variants)

    def seen_grade(pattern_id: str) -> int:
        seen_flags = [v["id"] in seen_set for v in by_pattern[pattern_id]]
        if not any(seen_flags):
            return 0  # untouched — no phrasing served yet
        if not all(seen_flags):
            return 1  # partly served — still holds an unseen phrasing
        return 2  # exhausted — every phrasing already served

    candidates = [pick_variant(v) for v in by_pattern.values()]
    random.shuffle(candidates)
    candidates.sort(key=lambda t: t["pattern_id"] not in hot)  # stable: hot first
    # Outermost, applied last so it dominates while preserving the hot-first
    # order within each grade (stable sort): patterns with unseen phrasings
    # must drain before any exhausted pattern repeats — a repeat can only
    # appear once fewer than ROUND_SIZE patterns still hold unseen phrasings
    # (the unavoidable final partial round).
    candidates.sort(key=lambda t: seen_grade(t["pattern_id"]))
    return candidates[:ROUND_SIZE], cycle_reset


@router.get("/round")
async def get_round(
    # VARY-001: comma-separated task ids the client has already been served
    # this pool cycle. Optional — absent behaves exactly like the old
    # stateless draw (full backward compatibility).
    seenTasks: str | None = None,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Three tasks, at most one per pattern, favoring phrasings the client
    hasn't seen this pool cycle (VARY-001) and the learner's hot ledger
    patterns. The `forces` field stays server-side — the learner sees the
    task, the judge sees the rubric."""
    tasks = list(load_tasks().values())
    try:
        focus = await load_grammar_focus(db, user_id=user_id, limit=10)
        hot = {f["pattern_id"] for f in focus} & set(TARGET_PATTERNS)
    except Exception:
        logger.exception("Sprechen focus read failed — serving an unweighted round")
        hot = set()

    try:
        since = datetime.now(timezone.utc) - timedelta(hours=_COOLDOWN_HOURS)
        recent_refs = await load_recent_attempt_item_ids(
            db, user_id=user_id, exercise="sprechen", since=since
        )
        # /give-up rows store "{task_id}:giveup" (DATA-004 convention); strip
        # the suffix so a given-up task lands in the same id namespace
        # _pick_round uses (raw task ids), not a string it'll never match.
        recent_ids = {ref.removesuffix(":giveup") for ref in recent_refs}
    except Exception:
        logger.exception("Sprechen recent-attempt read failed — cooldown skipped this round")
        recent_ids = set()

    seen_task_ids = seenTasks.split(",") if seenTasks else []
    # 48h cooldown (VARY-002): union server-known recent attempts into the
    # seen set so a task the user attempted recently doesn't resurface,
    # regardless of what the client echoes — covers Flow's amnesiac fetches
    # and every device the user practices from.
    seen_task_ids = list(set(seen_task_ids) | recent_ids)
    chosen, cycle_reset = _pick_round(tasks, hot, seen_task_ids)
    return {
        "tasks": [
            {"id": t["id"], "title": t["title"], "prompt": t["prompt"]}
            for t in chosen
        ],
        "cycleReset": cycle_reset,
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

        # Append to the cross-drill attempt log (DATA-004) — its own commit,
        # non-fatal like the ledger write above. `correct` mirrors the same
        # "clean attempt" condition that decides credit vs. record above.
        try:
            await record_drill_attempt(
                db,
                user_id=user_id,
                exercise="sprechen",
                item_ref=task["id"],
                pattern_id=task["pattern_id"],
                correct=passed,
                modality="spoken",
                session_id=session_id,
            )
        except Exception:
            logger.exception("Drill-attempt log write failed (task {})", task_id)

        return {
            "transcript": transcript,
            "passed": passed,
            "constraintMet": verdict.constraint_met,
            "constraintNote": verdict.constraint_note,
            "hits": verdict.hits,
            # Display-only, symmetric to slips (SPRECH-001) — defensive
            # getattr/or-empty so a judge response missing the field can't 500.
            "hitQuotes": getattr(verdict, "hit_quotes", None) or [],
            "slips": [
                {"quote": s.quote, "corrected": s.corrected, "note": s.note}
                for s in verdict.slips
            ],
        }


class GiveUpIn(BaseModel):
    task_id: str
    # OBS-007 practice-sitting id — same contract as the sibling drills.
    session_id: str | None = Field(None, max_length=64)


@router.post("/give-up")
async def give_up_attempt(
    body: GiveUpIn,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """FLOW-002 escape hatch: record a deliberate concession as a failed
    attempt WITHOUT audio. Unlike the other four item drills this can't just
    add a `give_up` flag to /attempts — that endpoint's body IS the audio
    upload — so it's a tiny sibling route instead. There's also no
    transcription/judge call to skip (nothing was recorded) and, since
    sprechen is open-ended, no single correct answer to reveal — this only
    logs the miss and hands back a modest verdict shape the frontend renders
    as-is."""
    task = load_tasks().get(body.task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Unknown task.")

    with tracer.start_as_current_span("sprechen-attempt") as attempt_span:
        attempt_span.set_attribute("user.id", user_id)
        attempt_span.set_attribute("task_id", task["id"])
        attempt_span.set_attribute("gave_up", True)
        if body.session_id:
            attempt_span.set_attribute("langfuse.session.id", body.session_id)
        attempt_span.set_attribute("langfuse.trace.output", "gave_up=True")

        # Feed the ledger (design rule 4) — non-fatal, same contract as a
        # real slip. There's no transcript to quote, so `sentence` carries a
        # sentinel instead — the give-up marker itself — and `corrected` is
        # left null rather than fabricating a "correct" spoken answer.
        try:
            await record_grammar_error(
                db,
                user_id=user_id,
                pattern_id=task["pattern_id"],
                sentence="(gave up)",
                corrected=None,
                note="Gave up — no recording submitted.",
                source="sprechen",
                session_id=body.session_id,
            )
        except Exception:
            logger.exception(
                "Sprechen ledger write failed (pattern {})", task["pattern_id"]
            )

        # Append to the cross-drill attempt log (DATA-004) — its own commit,
        # non-fatal like the ledger write above. ":giveup" suffix marks the
        # concession without a new column, same convention as the other
        # four drills.
        try:
            await record_drill_attempt(
                db,
                user_id=user_id,
                exercise="sprechen",
                item_ref=f"{task['id']}:giveup",
                pattern_id=task["pattern_id"],
                correct=False,
                modality="spoken",
                session_id=body.session_id,
            )
        except Exception:
            logger.exception("Drill-attempt log write failed (task {})", task["id"])

        return {
            "transcript": "",
            "passed": False,
            "constraintMet": False,
            "constraintNote": "You gave up — no recording was judged.",
            "hits": 0,
            "hitQuotes": [],
            "slips": [],
            "gaveUp": True,
        }


class NudgeIn(BaseModel):
    task_id: str
    # OBS-007 practice-sitting id — groups the nudge trace with the attempts.
    session_id: str | None = Field(None, max_length=64)


@router.post("/nudge")
async def vocab_nudge(
    body: NudgeIn,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """The retrieval cue before recording: which of the learner's OWN deck
    words would fit an answer to this task. The frontend shows only the
    count as a pill; clicking reveals ``words`` (the graduated hint).

    Decorative by contract — every failure path (empty deck, judge outage,
    hallucinated picks) returns ``{"words": []}``, never an error the drill
    would have to handle. Nothing here is an attempt: no DATA-004 row."""
    task = load_tasks().get(body.task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Unknown task.")

    deck = await load_deck(db, user_id)
    if not deck:
        return {"words": []}

    with tracer.start_as_current_span("sprechen-nudge") as span:
        span.set_attribute("user.id", user_id)
        span.set_attribute("task_id", task["id"])
        if body.session_id:
            span.set_attribute("langfuse.session.id", body.session_id)
        span.set_attribute("langfuse.trace.input", task["title"])
        span.set_attribute("deck_size", len(deck))
        try:
            pick = await suggest_vocab(task, list(deck.values()))
        except Exception as exc:
            span.record_exception(exc)
            logger.warning("Sprechen nudge judge failed (task {}) — serving none", task["id"])
            return {"words": []}

        words = filter_picks(pick, deck)
        span.set_attribute(
            "langfuse.trace.output",
            ", ".join(w["word"] for w in words) or "(none)",
        )
        return {"words": words}
