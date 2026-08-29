"""HTTP routes for Clara's interactive-exercise loop (AGENT-00X backend
half, rebuilt for CLARA-13 — "Clara mounts the real drill trainers"; CLARA-14
adds the speaking drill), plus the cold-start topic-screen endpoint:

- ``GET /teacher/starters`` — three curated starter topics for a learner
  whose ``user_errors`` ledger is still empty (see ``teacher/starters.py``).
- ``GET /teacher/exercise?pattern=<taxonomy id>`` — one random item for that
  pattern, served in that drill's NATIVE round-item shape (exactly what
  ``GET /<drill>/round`` would emit for it) so the frontend mounts the same
  trainer component Flow does, dealt Flow-style with a round of one.
- ``POST /teacher/exercise/attempts`` — grade one typed/typed-order/produced
  answer using that drill's own deterministic check + judge, returning that
  drill's NATIVE verdict shape (see teacher/registry.py). For the sprechen
  (speech) adapter this route only accepts a give-up — a real attempt has
  audio to transcribe first, which is what the next route is for. For the
  live-forged ``drill: "produce"`` (CLARA-16) it grades a TYPED sentence
  against the forged task via ``teacher/forge.py::grade_produced``.
- ``POST /teacher/exercise/attempts-audio`` — CLARA-14, extended by CLARA-16
  for a second job: multipart audio in, that drill's NATIVE verdict shape
  out. sprechen (default ``drill`` value, unchanged since CLARA-14):
  transcribe then grade, same pipeline ``POST /sprechen/attempts`` uses.
  produce (CLARA-16, ``drill: "produce"``): transcribe a SPOKEN answer to a
  live-forged task, then grade it via
  ``teacher/forge.py::grade_produced(spoken=True)`` — the same judge the
  typed path above uses, told the sentence came from speech recognition.
- ``POST /teacher/exercise/forge`` — CLARA-15 P3, developer-only; CLARA-16
  replaced the forged format entirely — draft + verify ONE fresh German
  PRODUCTION task live for a free-text topic (``teacher/forge.py``) when
  nothing printed fits it: an English instruction the learner answers with
  ONE original German sentence of their own, typed or spoken,
  constraint-judged with no accept-list at all (v1's single-gap fill-in and
  its accepts list are gone). 403s any non-developer — real learners never
  reach this path at all, since Clara's prompt only offers the
  ``[[ÜBUNG-NEU: ...]]`` marker that leads here when ``Context.forge_enabled``
  is True. The two routes above grade ``drill: "produce"`` attempts against
  the same in-memory store.

All five sit behind the same session-JWT dependency every drill router uses;
the attempts/attempts-audio/forge routes also share the drills' per-user rate
limiter (the transcription/judge/forge calls they may fire are exactly as
expensive as a normal drill attempt).
"""

from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from loguru import logger
from pydantic import BaseModel, Field

from agents.observability import (
    get_trace_session,
    mark_span_error,
    propagate_trace_context,
    tracer,
)
from auth.deps import get_current_user_id
from satz.examiner import transcribe_attempt
from security import drill_try_admit
from sprechen import grading as sprechen_grading
from sprechen.routes import _MAX_AUDIO_BYTES  # SAME cap POST /sprechen/attempts enforces — not re-derived
from teacher.forge import forge_item, get_item as get_forged_item, grade_produced, store_item
from teacher.registry import get_adapter, pick_random_item
from teacher.starters import starters_for_level
from drills.copy import JUDGE_UNAVAILABLE

router = APIRouter(prefix="/teacher", tags=["teacher"])


@router.get("/starters")
async def teacher_starters(user_id: str = Depends(get_current_user_id)):
    """Cold-start starter topics for the topic screen (AGENT-00X follow-up).

    Read-only: no writes, no coin/gate interaction — same three ids the
    factory falls back to when a learner's ``user_errors`` ledger is empty,
    so what's offered here always matches what Clara can actually deal."""
    from database.connection import get_sessionmaker
    from database.repository import load_user_level

    async with get_sessionmaker()() as db:
        level = await load_user_level(db, user_id=user_id)
    return {"starters": starters_for_level(level), "seeded": True}


@router.get("/balance")
async def teacher_balance(user_id: str = Depends(get_current_user_id)):
    """Daily Clara talk allowance for the picker header.

    free 0/day, basic 1/day, premium 3/day, developer ∞. Window is the coin
    day (05:00 local via coins/engine). Counts activity_session rows with
    lesson_id='teacher' in [day_start, nextResetAt)."""
    from datetime import timedelta as _td
    from sqlalchemy import func as _func, select as _select

    from coins.engine import next_reset_at as _nra
    from database.connection import get_sessionmaker
    from database.orm import ActivitySession as _AS, User as _User

    async with get_sessionmaker()() as db:
        user = await db.scalar(_select(_User).where(_User.id == user_id))
        if user is None:
            return {"tier": "free", "limit": 0, "used": 0, "remaining": 0, "nextResetAt": _nra(None).isoformat()}
        if (user.role or "") == "developer":
            nxt = _nra(user.timezone)
            return {"tier": user.tier or "free", "limit": 99, "used": 0, "remaining": 99, "nextResetAt": nxt.isoformat(), "bypass": True}
        tier = user.tier or "free"
        limit = {"free": 0, "basic": 1, "premium": 3}.get(tier, 0)
        nxt = _nra(user.timezone)
        day_start = nxt - _td(days=1)
        ds_naive = day_start.replace(tzinfo=None)
        nr_naive = nxt.replace(tzinfo=None)
        cnt = await db.scalar(
            _select(_func.count()).select_from(_AS).where(
                _AS.user_id == user_id,
                _AS.lesson_id == "teacher",
                _AS.started_at >= ds_naive,
                _AS.started_at < nr_naive,
            )
        )
        used = int(cnt or 0)
        return {"tier": tier, "limit": limit, "used": used, "remaining": max(0, limit - used), "nextResetAt": nxt.isoformat()}

@router.get("/exercise")
async def get_exercise(
    pattern: str,
    user_id: str = Depends(get_current_user_id),
):
    """One random item for ``pattern``, served in that drill's NATIVE
    round-item shape (CLARA-13) — the frontend mounts the same trainer
    component Flow does, so the item must be indistinguishable from a
    Flow-dealt one. 404 when no covered drill targets this pattern —
    Clara's own prompt is told to only ever name an id that's actually
    printed on her page, whether from her rendered focus sections or the
    full exercise catalog (CLARA-16), but a stale or hallucinated id must
    still fail closed here, not 500."""
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
        native_item = adapter.serve(item)
        span.set_attribute("drill", drill_name)
        span.set_attribute("item_id", item["id"])
        span.set_attribute("langfuse.observation.output", f"{drill_name}:{item['id']}")
        return {
            "drill": drill_name,
            "itemId": item["id"],
            "patternId": item["pattern_id"],
            "item": native_item,
        }


class ForgeIn(BaseModel):
    topic: str = Field(max_length=200)  # stripped + re-checked to 1-80 below


@router.post("/exercise/forge")
async def forge_exercise(
    body: ForgeIn,
    user_id: str = Depends(get_current_user_id),
):
    """CLARA-15 P3, developer-only; CLARA-16 rewrote the forged format:
    draft + verify ONE fresh German PRODUCTION task live for ``body.topic``
    (``teacher/forge.py``) — an English instruction the learner answers with
    ONE original German sentence, typed or spoken, mounted by the frontend's
    ``ProduceCard`` component. Real learners never reach this: Clara's
    prompt only offers the ``[[ÜBUNG-NEU: ...]]`` marker that leads here when
    ``Context.forge_enabled`` is True (``role == "developer"``), and this
    route re-checks that same condition itself as an independent, defense-in-
    depth gate — a prompt regression must not turn into an open forge for
    every learner.

    ============================================================================
    ABSOLUTE INVARIANT — THIS ROUTE WRITES NOTHING, EVER.
    No `record_grammar_error`, no `credit_pattern_success`, no
    `record_drill_attempt`, no daily-mode credit, no DB session at all. This is
    the approved design decision for v1 ("stay silent") — Clara's room is
    deliberately exempt from every evaluator (see ARCHITECTURE.md's teacher
    branch), and a practice item handed out INSIDE that room must not become a
    side-channel into the same learning-state tables the exemption exists to
    keep her out of. If a future version wants these writes, that's a new,
    deliberate decision — not a bug fix to this comment. (The one DB read this
    route performs — the role check below — is read-only, not a write.)
    ============================================================================
    """
    topic = body.topic.strip()
    if not (1 <= len(topic) <= 80):
        raise HTTPException(status_code=422, detail="Give a short topic (1-80 characters).")

    from sqlalchemy import select
    from database.connection import get_sessionmaker
    from database.orm import User

    async with get_sessionmaker()() as db:
        user = await db.scalar(select(User).where(User.id == user_id))
    if user is None or (user.role or "") != "developer":
        raise HTTPException(status_code=403, detail={"code": "forge_locked"})

    if not drill_try_admit(user_id):
        raise HTTPException(
            status_code=429,
            detail="You're going very fast — take a short break and try again in a few minutes.",
        )

    # Same session-joining shape as the deal span (GET /teacher/exercise)
    # above: get_trace_session + propagate_trace_context so this route's
    # forge/verify judge generations nest under Clara's live conversation
    # Session. DEFAULT Langfuse environment — never "forge" (that env is
    # background content-forge volume; this is learner— well, developer-
    # facing, not batch content generation).
    session_id = get_trace_session(user_id)
    with propagate_trace_context(user_id=user_id, session_id=session_id), \
            tracer.start_as_current_span("teacher-exercise-forge") as span:
        span.set_attribute("user.id", user_id)
        if session_id:
            span.set_attribute("langfuse.session.id", session_id)
        span.set_attribute("langfuse.observation.input", topic)
        span.set_attribute("langfuse.observation.metadata.topic", topic)
        try:
            item = await forge_item(topic)
        except Exception:
            logger.exception(f"Teacher forge failed (topic={topic!r})")
            mark_span_error(span, "forge unavailable")
            raise HTTPException(status_code=502, detail={"code": "FORGE_UNAVAILABLE"})
        store_item(item)
        span.set_attribute("drill", "produce")
        span.set_attribute("item_id", item["id"])
        span.set_attribute("langfuse.observation.output", f"produce:{item['id']}")
        return {
            "drill": "produce",
            "itemId": item["id"],
            "topic": topic,
            # `example` (the model answer) is deliberately withheld here —
            # answers never get served pre-attempt. `hint` reuses the item's
            # `rule_note` — the same "why this practices what it does" copy
            # v1 served as `hint` off `hint_en`.
            "item": {
                "id": item["id"],
                "task": item["task"],
                "target": item["target"],
                "hint": item["rule_note"],
            },
        }


class AttemptIn(BaseModel):
    drill: str
    itemId: str
    give_up: bool = False
    answer: str | None = Field(default=None, max_length=2000)  # text drills
    order: list[str] | None = None  # satzbau


@router.post("/exercise/attempts")
async def submit_exercise_attempt(
    body: AttemptIn,
    user_id: str = Depends(get_current_user_id),
):
    """Grade one attempt with that drill's own deterministic check + judge,
    returning that drill's NATIVE verdict shape (CLARA-13) — byte-compatible
    with what ``POST /<drill>/attempts`` returns for the same item+answer.

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

    if body.drill == "produce":
        # CLARA-16: a live-forged production item is process-local
        # dev-preview state (teacher/forge.py's in-memory store), not a
        # drill catalog — no `get_adapter` entry exists for it. No role
        # check here on purpose: the item only exists at all if a developer
        # forged it via POST /teacher/exercise/forge, so a stale/missing id
        # already 404s everyone, developer or not (D7-style uniform
        # stale-item behavior). v1's "forge" drill value is dropped
        # entirely — this is a dev-only feature, no compat shim for the old
        # gap-fill shape.
        item = get_forged_item(body.itemId)
        if item is None:
            raise HTTPException(
                status_code=404,
                detail="That exercise expired — ask Clara for a fresh one.",
            )
        answer_payload = " ".join((body.answer or "").split())
        if not body.give_up and not answer_payload:
            raise HTTPException(status_code=422, detail="Type your answer first.")

        session_id = get_trace_session(user_id)
        with propagate_trace_context(user_id=user_id, session_id=session_id), \
                tracer.start_as_current_span("teacher-exercise-attempt") as span:
            span.set_attribute("user.id", user_id)
            if session_id:
                span.set_attribute("langfuse.session.id", session_id)
            span.set_attribute("drill", "produce")
            span.set_attribute("item_id", item["id"])
            span.set_attribute("langfuse.observation.metadata.topic", item.get("topic", ""))
            span.set_attribute("langfuse.observation.input", answer_payload)
            if body.give_up:
                span.set_attribute("gave_up", True)
            try:
                verdict, _judge_skipped = await grade_produced(
                    item, answer_payload, spoken=False, give_up=body.give_up
                )
            except Exception:
                logger.exception(
                    "Teacher forge judge call failed (item {})", item["id"]
                )
                mark_span_error(span, "judge unavailable")
                raise HTTPException(status_code=502, detail=JUDGE_UNAVAILABLE)
            correct = verdict["correct"]
            note = verdict.get("note")
            span.set_attribute("langfuse.observation.metadata.correct", str(correct))
            span.set_attribute(
                "langfuse.observation.output",
                f"correct={correct}" + (f" note={note}" if note else ""),
            )
            return verdict

    adapter = get_adapter(body.drill)
    if adapter is None:
        raise HTTPException(status_code=404, detail="Unknown item.")
    item = adapter.load_items().get(body.itemId)
    if item is None:
        raise HTTPException(status_code=404, detail="Unknown item.")

    if adapter.speech:
        # CLARA-14: sprechen's real attempt is audio, not typed/ordered text
        # — this JSON route can only ever grade a give-up for it (nothing to
        # transcribe). A non-give-up JSON attempt is a frontend bug (or a
        # stale client), not a learner error, so it's rejected 422 rather
        # than silently misgraded as an empty typed answer.
        if not body.give_up:
            raise HTTPException(
                status_code=422,
                detail="Speaking exercises need audio — use POST /teacher/exercise/attempts-audio.",
            )
        session_id = get_trace_session(user_id)
        with propagate_trace_context(user_id=user_id, session_id=session_id), \
                tracer.start_as_current_span("teacher-exercise-attempt") as span:
            span.set_attribute("user.id", user_id)
            if session_id:
                span.set_attribute("langfuse.session.id", session_id)
            span.set_attribute("drill", body.drill)
            span.set_attribute("item_id", item["id"])
            span.set_attribute("langfuse.observation.metadata.pattern", item["pattern_id"])
            span.set_attribute("gave_up", True)
            verdict, _judge_skipped = await adapter.grade(item, "", give_up=True)
            span.set_attribute("langfuse.observation.metadata.correct", str(verdict["passed"]))
            span.set_attribute("langfuse.observation.output", "passed=False (gave up)")
            return verdict

    # satzbau grades an `order: list[str]`; the other four grade a typed
    # `answer: str` — the request carries whichever field applies (D3),
    # validated 422 when it's missing and this isn't a give-up.
    if adapter.uses_order:
        if not body.give_up and body.order is None:
            raise HTTPException(status_code=422, detail="Place the chips first.")
        payload: Any = body.order if body.order is not None else []
    else:
        if not body.give_up and body.answer is None:
            raise HTTPException(status_code=422, detail="Type your answer first.")
        payload = " ".join((body.answer or "").split())

    if not body.give_up:
        reason = adapter.validate(item, payload)
        if reason:
            raise HTTPException(status_code=422, detail=reason)

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
        span.set_attribute(
            "langfuse.observation.input",
            " ".join(payload) if adapter.uses_order else payload,
        )
        # Filterable metadata (langfuse.observation.metadata.*) — the fields
        # OBS-009-style evaluation will want to slice by.
        span.set_attribute("langfuse.observation.metadata.pattern", item["pattern_id"])
        if body.give_up:
            span.set_attribute("gave_up", True)
        try:
            verdict, _extra = await adapter.grade(item, payload, give_up=body.give_up)
        except Exception:
            logger.exception(
                "Teacher exercise judge call failed (drill {} item {})", body.drill, item["id"]
            )
            mark_span_error(span, "judge unavailable")
            raise HTTPException(
                status_code=502,
                detail=JUDGE_UNAVAILABLE,
            )
        correct = verdict["correct"]
        note = verdict.get("note")
        span.set_attribute("langfuse.observation.metadata.correct", str(correct))
        span.set_attribute(
            "langfuse.observation.output",
            f"correct={correct} expected={verdict.get('expected')}"
            + (f" note={note}" if note else ""),
        )
        return verdict


@router.post("/exercise/attempts-audio")
async def submit_exercise_attempt_audio(
    itemId: str = Form(...),
    audio: UploadFile = File(...),
    # CLARA-16: which item kind `itemId` belongs to. Defaults to "sprechen"
    # so every pre-CLARA-16 caller — which never sent this field at all —
    # keeps hitting the exact same branch with byte-identical behavior.
    # "produce" is the new job this route picked up: the SPOKEN sibling of
    # POST /teacher/exercise/attempts's own `drill: "produce"` (typed) branch,
    # for a live-forged production task's answer.
    drill: str = Form("sprechen"),
    user_id: str = Depends(get_current_user_id),
):
    """Grade one SPOKEN attempt. Two jobs share this multipart audio route
    now (CLARA-16), picked by the `drill` field:

    - sprechen (CLARA-14, unchanged): an item dealt via
      ``GET /teacher/exercise``. sprechen's audio -> transcript -> judge
      pipeline doesn't fit ``AttemptIn``'s typed/ordered-text shape
      (``answer``/``order``), so it gets its own multipart sibling route —
      the same relationship ``POST /sprechen/give-up`` has to ``POST
      /sprechen/attempts``, just flipped (there the JSON route is the odd
      one out; here the multipart route is). Returns sprechen's NATIVE
      verdict shape — byte-compatible with what ``POST /sprechen/attempts``
      returns for the same item+audio (``sprechen/grading.py::grade``,
      shared by both).
    - produce (CLARA-16): a live-forged production task
      (``teacher/forge.py``), answered by SPEAKING instead of typing.
      Transcribes then grades via
      ``teacher/forge.py::grade_produced(spoken=True)`` — the same judge
      ``POST /teacher/exercise/attempts``'s `drill: "produce"` branch uses
      for a typed answer, told the sentence came from speech recognition so
      it forgives spelling/punctuation/homophones. Returns that same verdict
      dict plus ``transcript`` — seeing what you actually said is part of
      the format, same philosophy as sprechen.

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

    if drill == "sprechen":
        adapter = get_adapter("sprechen")
        item = adapter.load_items().get(itemId) if adapter is not None else None
        if item is None:
            raise HTTPException(status_code=404, detail="Unknown item.")
    elif drill == "produce":
        item = get_forged_item(itemId)
        if item is None:
            raise HTTPException(
                status_code=404,
                detail="That exercise expired — ask Clara for a fresh one.",
            )
    else:
        raise HTTPException(status_code=404, detail="Unknown item.")

    # Same audio validation/limits as POST /sprechen/attempts (sprechen/
    # routes.py) — the cap itself is imported, not re-derived, above. One
    # shared code path for both drill kinds, not duplicated per-branch.
    data = await audio.read()
    if not data:
        raise HTTPException(status_code=422, detail="We didn't get any audio — try again.")
    if len(data) > _MAX_AUDIO_BYTES:
        raise HTTPException(
            status_code=413,
            detail="That recording is too long — a few sentences is enough.",
        )

    if drill == "sprechen":
        # Same tracing shape as submit_exercise_attempt: a root span carrying
        # the transcript as root-observation input and the verdict as output,
        # wrapped in propagate_trace_context so the STT + judge generations
        # transcribe_attempt/grade fire nest under it WITH user/session.
        # `langfuse.session.id` joins Clara's live conversation Session
        # server-side, same lookup as the JSON route (there is no
        # client-supplied session_id here, unlike /sprechen/attempts's own
        # form field). Tracing only — the WRITES-NOTHING invariant above is
        # untouched.
        session_id = get_trace_session(user_id)
        with propagate_trace_context(user_id=user_id, session_id=session_id), \
                tracer.start_as_current_span("teacher-exercise-attempt") as span:
            span.set_attribute("user.id", user_id)
            if session_id:
                span.set_attribute("langfuse.session.id", session_id)
            span.set_attribute("drill", "sprechen")
            span.set_attribute("item_id", item["id"])
            span.set_attribute("langfuse.observation.metadata.pattern", item["pattern_id"])
            span.set_attribute("langfuse.observation.metadata.drill", "sprechen")

            try:
                # STT cost stamping (agents/audio_costs.py) happens INSIDE
                # transcribe_attempt on its own "stt" span — same as
                # POST /sprechen/attempts, nothing extra to do here.
                transcript = await transcribe_attempt(
                    data, audio.content_type, keyterms=item.get("keyterms")
                )
            except Exception as exc:
                span.record_exception(exc)
                logger.exception("Teacher sprechen transcription failed (item {})", itemId)
                # Mirrors POST /sprechen/attempts's own STT failure mapping
                # exactly (sprechen/routes.py) — NOT the judge's JUDGE_UNAVAILABLE
                # detail, which is a different failure with a different message.
                raise HTTPException(
                    status_code=502,
                    detail="Couldn't process the audio — try again in a moment.",
                )
            if not transcript:
                raise HTTPException(
                    status_code=422,
                    detail="We couldn't hear anything — try again a bit closer to the mic.",
                )
            span.set_attribute("langfuse.observation.input", transcript)

            try:
                verdict, _judge_skipped = await sprechen_grading.grade(item, transcript, give_up=False)
            except Exception:
                logger.exception("Teacher sprechen judge call failed (item {})", itemId)
                mark_span_error(span, "judge unavailable")
                raise HTTPException(
                    status_code=502,
                    detail=JUDGE_UNAVAILABLE,
                )

            span.set_attribute("langfuse.observation.metadata.correct", str(verdict["passed"]))
            span.set_attribute(
                "langfuse.observation.output",
                f"passed={verdict['passed']} constraintMet={verdict['constraintMet']} "
                f"hits={verdict['hits']} slips={len(verdict['slips'])}",
            )
            return verdict

    # drill == "produce" (CLARA-16): same tracing shape as the sprechen
    # branch above and as submit_exercise_attempt's own produce branch —
    # a root span carrying the transcript as input and the verdict as
    # output. Tracing only — the WRITES-NOTHING invariant above is untouched.
    session_id = get_trace_session(user_id)
    with propagate_trace_context(user_id=user_id, session_id=session_id), \
            tracer.start_as_current_span("teacher-exercise-attempt") as span:
        span.set_attribute("user.id", user_id)
        if session_id:
            span.set_attribute("langfuse.session.id", session_id)
        span.set_attribute("drill", "produce")
        span.set_attribute("item_id", item["id"])
        span.set_attribute("langfuse.observation.metadata.topic", item.get("topic", ""))

        try:
            # keyterms biases Deepgram toward the target structure — the one
            # phrase this attempt is most likely to hinge on, same nova-3
            # keyterm-prompting convention as every other caller of
            # transcribe_attempt.
            transcript = await transcribe_attempt(
                data, audio.content_type, keyterms=item["target"].split()
            )
        except Exception as exc:
            span.record_exception(exc)
            logger.exception("Teacher produce transcription failed (item {})", itemId)
            raise HTTPException(
                status_code=502,
                detail="Couldn't process the audio — try again in a moment.",
            )
        if not transcript:
            raise HTTPException(
                status_code=422,
                detail="We couldn't hear anything — try again a bit closer to the mic.",
            )
        span.set_attribute("langfuse.observation.input", transcript)

        try:
            verdict, _judge_skipped = await grade_produced(item, transcript, spoken=True)
        except Exception:
            logger.exception("Teacher produce judge call failed (item {})", itemId)
            mark_span_error(span, "judge unavailable")
            raise HTTPException(
                status_code=502,
                detail=JUDGE_UNAVAILABLE,
            )

        correct = verdict["correct"]
        note = verdict.get("note")
        span.set_attribute("langfuse.observation.metadata.correct", str(correct))
        span.set_attribute(
            "langfuse.observation.output",
            f"correct={correct}" + (f" note={note}" if note else ""),
        )
        # `transcript` rides alongside the verdict — seeing what you actually
        # said is part of the format, same philosophy as sprechen's own
        # response above.
        return {**verdict, "transcript": transcript}
