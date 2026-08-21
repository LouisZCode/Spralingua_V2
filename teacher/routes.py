"""HTTP routes for Clara's interactive-exercise loop (AGENT-00X backend
half). Two endpoints:

- ``GET /teacher/exercise?pattern=<taxonomy id>`` — one random item for that
  pattern, normalized into a generic card the frontend can render without
  knowing which of the five underlying drills it came from.
- ``POST /teacher/exercise/attempts`` — grade one typed/typed-order answer
  using that drill's own deterministic check + judge (see teacher/registry.py).

Both endpoints sit behind the same session-JWT dependency every drill router
uses; the POST also shares the drills' per-user rate limiter (the judge calls
it may fire are exactly as expensive as a normal drill attempt).
"""

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from pydantic import BaseModel, Field

from agents.observability import (
    get_trace_session,
    mark_span_error,
    propagate_trace_context,
    tracer,
)
from auth.deps import get_current_user_id
from security import drill_try_admit
from teacher.registry import get_adapter, pick_random_item

router = APIRouter(prefix="/teacher", tags=["teacher"])

_MAX_ANSWER_CHARS = 200  # generous: a satzbau order retypes several words


@router.get("/exercise")
async def get_exercise(
    pattern: str,
    user_id: str = Depends(get_current_user_id),
):
    """One random item for ``pattern``, normalized to one generic card shape.
    404 when no covered drill targets this pattern — Clara's own prompt is
    told to only ever name an id from her rendered focus list, but a stale
    or hallucinated id must still fail closed here, not 500."""
    # Joins Clara's live conversation Session in Langfuse (server-side lookup;
    # the frontend carries no session id). One trace per dealt exercise =
    # Clara's deal rate is countable per session/user. A 404 is stamped as an
    # ERROR trace deliberately: it means Clara named a pattern her exercise
    # catalog doesn't cover — an agent-quality signal, not client noise.
    session_id = get_trace_session(user_id)
    with tracer.start_as_current_span("teacher-exercise-deal") as span:
        span.set_attribute("user.id", user_id)
        if session_id:
            span.set_attribute("langfuse.session.id", session_id)
        span.set_attribute("langfuse.observation.input", pattern)
        span.set_attribute("langfuse.observation.metadata.pattern", pattern)
        picked = pick_random_item(pattern)
        if picked is None:
            mark_span_error(span, "no exercise for this pattern (stale/hallucinated id?)")
            raise HTTPException(status_code=404, detail="no exercise for this pattern")
        drill_name, item = picked
        adapter = get_adapter(drill_name)
        card = adapter.build_card(item)
        span.set_attribute("drill", drill_name)
        span.set_attribute("item_id", item["id"])
        span.set_attribute("langfuse.observation.output", f"{drill_name}:{item['id']}")
        return {
            "drill": drill_name,
            "itemId": item["id"],
            "patternId": item["pattern_id"],
            "instruction": card["instruction"],
            "prompt": card["prompt"],
            "expected_shape": card.get("expected_shape"),
        }


class AttemptIn(BaseModel):
    drill: str
    itemId: str
    answer: str = Field(max_length=2000)


@router.post("/exercise/attempts")
async def submit_exercise_attempt(
    body: AttemptIn,
    user_id: str = Depends(get_current_user_id),
):
    """Grade one attempt with that drill's own deterministic check + judge.

    ============================================================================
    ABSOLUTE INVARIANT — THIS ROUTE WRITES NOTHING, EVER.
    No `record_grammar_error`, no `credit_pattern_success`, no
    `record_drill_attempt`, no daily-mode credit, no DB session at all. This is
    the approved design decision for v1 ("stay silent") — Clara's room is
    deliberately exempt from every evaluator (see ARCHITECTURE.md's teacher
    branch), and a practice item handed out INSIDE that room must not become a
    side-channel into the same learning-state tables the exemption exists to
    keep her out of. If a future version wants these writes, that's a new,
    deliberate decision — not a bug fix to this comment.
    ============================================================================
    """
    if not drill_try_admit(user_id):
        raise HTTPException(
            status_code=429,
            detail="You're going very fast — take a short break and try again in a few minutes.",
        )
    adapter = get_adapter(body.drill)
    if adapter is None:
        raise HTTPException(status_code=404, detail="Unknown item.")
    item = adapter.load_items().get(body.itemId)
    if item is None:
        raise HTTPException(status_code=404, detail="Unknown item.")

    answer = " ".join(body.answer.split())
    if not answer:
        raise HTTPException(status_code=422, detail="Type your answer first.")
    if len(answer) > _MAX_ANSWER_CHARS:
        raise HTTPException(
            status_code=422,
            detail="Keep it to the answer — that looks like a paragraph.",
        )

    # Same tracing shape as every drill's /attempts: a root span carrying the
    # answer as root-observation input and the verdict as output, wrapped in
    # propagate_trace_context so the judge generation the grade may fire
    # nests under it WITH user/session (it was an orphan before this span
    # existed). `langfuse.session.id` joins Clara's live conversation Session
    # server-side. Tracing only — the WRITES-NOTHING invariant above is
    # untouched; nothing here goes near the ledger.
    session_id = get_trace_session(user_id)
    with propagate_trace_context(user_id=user_id, session_id=session_id), \
            tracer.start_as_current_span("teacher-exercise-attempt") as span:
        span.set_attribute("user.id", user_id)
        if session_id:
            span.set_attribute("langfuse.session.id", session_id)
        span.set_attribute("drill", body.drill)
        span.set_attribute("item_id", item["id"])
        span.set_attribute("langfuse.observation.input", answer)
        # Filterable metadata (langfuse.observation.metadata.*) — the fields
        # OBS-009-style evaluation will want to slice by.
        span.set_attribute("langfuse.observation.metadata.pattern", item["pattern_id"])
        try:
            correct, expected, note = await adapter.grade(item, answer)
        except Exception:
            logger.exception(
                "Teacher exercise judge call failed (drill {} item {})", body.drill, item["id"]
            )
            mark_span_error(span, "judge unavailable")
            raise HTTPException(
                status_code=502,
                detail="The judge is unavailable right now — try again in a moment.",
            )
        span.set_attribute("langfuse.observation.metadata.correct", str(correct))
        span.set_attribute(
            "langfuse.observation.output",
            f"correct={correct} expected={expected}" + (f" note={note}" if note else ""),
        )
        return {"correct": correct, "expected": expected, "note": note}
