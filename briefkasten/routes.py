"""HTTP routes for Briefkasten — the letter-writing exercise.

A letter arrives from someone; the learner writes back in German; two graded
attempts follow (hints, then corrections). The design is ported from
Spralingua v1's email exercise; none of its code is — v1 kept the letter in a
Flask session, scraped JSON out of prose with regex, and carried four
languages' worth of tables. Here the letter is generated per request, the
judges return structured output, and the client echoes the letter back on
submit, exactly like ``szenario/routes.py`` echoes its question.

Why the echo rather than a DB row: the exercise is stateless by nature (one
letter, two attempts, done) and nothing here is worth persisting yet. The
learner can only ever tamper with their OWN feedback quality, which is the
same trust model Szenario already runs on.
"""

import asyncio
import random

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from agents.error_extractor import extract_errors
from agents.observability import propagate_trace_context, tracer
from auth.deps import get_current_user_id
from database.connection import get_db, get_sessionmaker
from database.orm import User
from database.repository import (
    complete_daily_mode,
    load_user_level,
    load_vocab_words,
    record_drill_attempt,
    record_grammar_error,
)
from briefkasten.content import POINT_COUNT, load_seeds, seeds_for_level, word_target
from briefkasten.germanizer import germanize_letter
from briefkasten.judge import feedback_pass, hint_pass
from drills.copy import JUDGE_UNAVAILABLE
from briefkasten.writer import VOCAB_LIMIT, write_letter
from security import drill_try_admit

from coins.gate import admit_coins_or_402
from coins.prices import LETTER

router = APIRouter(prefix="/briefkasten", tags=["briefkasten"])

# A letter and a reply are both long-form by design, but neither is a novel.
# The ceiling is generous enough that no honest B2 letter ever hits it and
# tight enough that nobody funnels a book through the judge on our budget.
_MAX_TEXT_CHARS = 3000
_MAX_POINT_CHARS = 200

# Strong refs to in-flight background harvests so the event loop never
# garbage-collects one mid-run — the same fire-and-forget discipline
# ``szenario/routes.py`` uses. Each task discards itself on completion.
_background_tasks: set[asyncio.Task] = set()


async def _background_harvest(text: str, session_id: str | None, user_id: str) -> None:
    """Fire-and-forget grammar harvest off the response's critical path.

    Fed by attempt ONE, deliberately. Attempt two is written after the learner
    has been told where to look, so harvesting it would record what survives
    coaching rather than what they actually get wrong — the ledger would
    quietly flatter itself. Attempt one is the uncoached production.

    Opens its own DB session: the request-scoped one closes the instant the
    response returns. Same non-fatal, log-and-swallow contract as every other
    ledger write in the repo.
    """
    try:
        extraction = await extract_errors(transcript=text, session_id=session_id, user_id=user_id)
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
                        source="briefkasten",
                        session_id=session_id,
                    )
                except Exception:  # noqa: BLE001 — one row must not block the rest
                    logger.exception(
                        "Briefkasten ledger write failed (pattern {})", err.pattern_id
                    )
    except Exception:  # noqa: BLE001 — the harvest must never break the attempt
        logger.warning("Briefkasten grammar extraction failed (non-fatal)")


def _pick_seed(seeds: list[dict], seen_ids: list[str]) -> tuple[dict, bool]:
    """Pick one seed, favoring those absent from ``seen_ids`` (client-echoed,
    same VARY-001 contract as Szenario). Unknown ids are silently dropped.

    Never repeats the last seed served while any unseen one remains. When the
    pool is exhausted it resets for this pick — reported via ``cycle_reset``
    so the UI can say "you've been through them all" rather than silently
    looping.
    """
    by_id = {s["id"]: s for s in seeds}
    seen = [sid for sid in seen_ids if sid in by_id]
    last_id = seen[-1] if seen else None

    seen_set = set(seen)
    unseen = [s for s in seeds if s["id"] not in seen_set]

    cycle_reset = not unseen
    if cycle_reset:
        unseen = seeds

    others = [s for s in unseen if s["id"] != last_id]
    pool = others or unseen
    return random.choice(pool), cycle_reset


@router.get("/letter")
async def get_letter(
    # VARY-001: comma-separated seed ids the client has already been served
    # this pool cycle. Optional — absent behaves like a plain random draw.
    seen: str | None = None,
    # "informal" / "formal" filter the pool; absent or anything else serves
    # the whole pool. Both registers ship on purpose: a learner who only ever
    # writes to friends never practises the register they need for an Amt.
    register: str | None = None,
    # OBS-007 practice-sitting id — same contract as the attempt route below
    # (``AttemptIn.session_id``), just carried as a query param here since
    # this is a GET. Threads through so the ``briefkasten-writer`` generation
    # lands inside the learner's practice sitting instead of session-less.
    session_id: str | None = None,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Mint one incoming letter: pick a seed, write it around the learner's
    own deck words, hand back the writing brief with it."""
    # This endpoint makes an LLM call, so it is rate-limited like an attempt —
    # an unthrottled generator is a cost-abuse hole, not just a slow page.
    if not drill_try_admit(user_id):
        raise HTTPException(
            status_code=429,
            detail="You're going very fast — take a short break and try again in a few minutes.",
        )
    # PAY-002: one charge per letter cycle, at creation (not at the attempts).
    # The two attempts + germanize call that follow ride on this single 15-coin
    # ticket; the POST /attempts site is deliberately free.
    try:
        await admit_coins_or_402(db, user_id=user_id, price=LETTER, kind="spend_letter")
    except HTTPException as _e:
        if _e.status_code == 402:
            raise
        raise HTTPException(status_code=503, detail="billing temporarily unavailable")

    seeds = list(load_seeds().values())

    # LEVEL-001: a learner who declared a level draws only from that level's
    # seeds; no level (NULL / "not sure") keeps the whole pool. Never fatal —
    # a level read that fails serves the unfiltered pool and logs, the same
    # contract drills/leveling.py gives the grammar drills.
    try:
        level = await load_user_level(db, user_id=user_id)
    except Exception:
        logger.exception("Briefkasten level read failed — serving every level")
        level = None
    seeds = seeds_for_level(seeds, level)

    if register in ("informal", "formal"):
        # The register is a client hint layered on top of the level, never
        # instead of it: if the level's pool has no seed in that register
        # (A1 has no formal seed today) the register drops, the level stays.
        by_register = [s for s in seeds if s["register"] == register]
        seeds = by_register or seeds
    seed, cycle_reset = _pick_seed(seeds, seen.split(",") if seen else [])
    logger.info(
        "Briefkasten letter: level={} pool={} seed={}", level, len(seeds), seed["id"]
    )

    try:
        vocab = await load_vocab_words(db, user_id=user_id, limit=VOCAB_LIMIT)
    except Exception:
        # A deck outage must never cost the learner their letter — the writer
        # handles an empty list as a first-session no-op.
        logger.exception("Briefkasten vocab read failed — writing without deck words")
        vocab = []

    # The learner's own first name, so the greeting reads like a real letter
    # rather than a form letter. Null for the demo user and for Google
    # profiles without a name — the writer greets without one in that case.
    try:
        user = await db.get(User, user_id)
        reader_name = (user.name or "").strip().split(" ")[0] or None if user else None
    except Exception:
        logger.exception("Briefkasten name read failed — greeting without a name")
        reader_name = None

    with propagate_trace_context(user_id=user_id, session_id=session_id), tracer.start_as_current_span("briefkasten-letter") as span:
        span.set_attribute("user.id", user_id)
        span.set_attribute("seed_id", seed["id"])
        span.set_attribute("register", seed["register"])
        span.set_attribute("user.level", level or "")
        if session_id:
            span.set_attribute("langfuse.session.id", session_id)
        span.set_attribute(
            "langfuse.observation.input", f"seed={seed['id']} register={seed['register']}"
        )
        try:
            draft = await write_letter(seed, vocab, reader_name, user_id=user_id)
        except Exception as exc:
            span.record_exception(exc)
            logger.exception("Briefkasten letter generation failed (seed {})", seed["id"])
            raise HTTPException(
                status_code=502,
                detail="Couldn't write your letter just now — try again in a moment.",
            )
        span.set_attribute("langfuse.observation.output", draft.betreff)

    min_words, max_words = word_target(seed["level"])
    return {
        "seedId": seed["id"],
        "register": seed["register"],
        "level": seed["level"],
        "sender": {
            "name": seed["sender"]["name"],
            "relation": seed["sender"]["relation"],
        },
        "betreff": draft.betreff,
        "body": draft.body,
        "points": seed["points"],
        "wordTarget": {"min": min_words, "max": max_words},
        "cycleReset": cycle_reset,
    }


class AttemptIn(BaseModel):
    seed_id: str
    # The letter and the brief ride back from the client — see the module
    # docstring on why this is an echo rather than server state.
    letter_body: str
    points: list[str]
    response: str
    # Required when attempt == 2: the judge compares the two drafts.
    first_attempt: str | None = None
    attempt: int = 1
    # OBS-007 practice-sitting id — same contract as the sibling drills.
    session_id: str | None = Field(None, max_length=64)


@router.post("/attempts")
async def submit_attempt(
    body: AttemptIn,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Judge one draft. Attempt 1 returns hints only; attempt 2 returns the
    full correction pass, and only then is the exercise logged."""
    if not drill_try_admit(user_id):
        raise HTTPException(
            status_code=429,
            detail="You're going very fast — take a short break and try again in a few minutes.",
        )

    seed = load_seeds().get(body.seed_id)
    if seed is None:
        raise HTTPException(status_code=404, detail="Unknown letter.")
    if body.attempt not in (1, 2):
        raise HTTPException(status_code=422, detail="Unknown attempt.")

    response = body.response.strip()
    if not response:
        raise HTTPException(status_code=422, detail="Write your reply first.")
    letter_body = body.letter_body.strip()
    if not letter_body:
        raise HTTPException(status_code=422, detail="The letter is missing.")
    if len(response) > _MAX_TEXT_CHARS or len(letter_body) > _MAX_TEXT_CHARS:
        raise HTTPException(
            status_code=413,
            detail="That's longer than this exercise expects — keep it to a letter.",
        )
    if len(body.points) != POINT_COUNT or any(
        len(p) > _MAX_POINT_CHARS for p in body.points
    ):
        raise HTTPException(status_code=422, detail="The writing brief is malformed.")

    first_attempt = (body.first_attempt or "").strip()
    if body.attempt == 2 and not first_attempt:
        raise HTTPException(
            status_code=422, detail="We lost your first draft — start a new letter."
        )
    if len(first_attempt) > _MAX_TEXT_CHARS:
        raise HTTPException(
            status_code=413,
            detail="That's longer than this exercise expects — keep it to a letter.",
        )

    with propagate_trace_context(user_id=user_id, session_id=body.session_id), tracer.start_as_current_span("briefkasten-attempt") as attempt_span:
        attempt_span.set_attribute("user.id", user_id)
        attempt_span.set_attribute("seed_id", seed["id"])
        attempt_span.set_attribute("attempt", body.attempt)
        attempt_span.set_attribute("register", seed["register"])
        if body.session_id:
            attempt_span.set_attribute("langfuse.session.id", body.session_id)
        attempt_span.set_attribute("langfuse.observation.input", response)

        try:
            if body.attempt == 1:
                hints = await hint_pass(
                    letter=letter_body,
                    points=body.points,
                    response=response,
                    register=seed["register"],
                    level=seed["level"],
                    sender_name=seed["sender"]["name"],
                    user_id=user_id,
                )
            else:
                verdict = await feedback_pass(
                    letter=letter_body,
                    points=body.points,
                    first_attempt=first_attempt,
                    second_attempt=response,
                    register=seed["register"],
                    level=seed["level"],
                    sender_name=seed["sender"]["name"],
                    user_id=user_id,
                )
        except Exception as exc:
            attempt_span.record_exception(exc)
            logger.exception(
                "Briefkasten judge call failed (seed {}, attempt {})",
                seed["id"],
                body.attempt,
            )
            raise HTTPException(
                status_code=502,
                detail=JUDGE_UNAVAILABLE,
            )

        # Runs only after feedback_pass has already succeeded, sequentially
        # (it reads `verdict.corrected_text`, not the raw response) and in
        # its OWN try/except: a Germanize outage must never fail the whole
        # attempt the way a feedback_pass outage does above — the learner
        # still gets their score and corrections, just no rewrite.
        natural_version = None
        if body.attempt == 2:
            try:
                natural_version = await germanize_letter(
                    corrected_text=verdict.corrected_text,
                    task_context=letter_body,
                    register=seed["register"],
                    user_id=user_id,
                )
            except Exception:
                logger.warning(
                    "Briefkasten germanize failed (seed {}) — no naturalVersion this attempt",
                    seed["id"],
                )

        if body.attempt == 1:
            attempt_span.set_attribute(
                "langfuse.observation.output", f"hints={len(hints.items)}"
            )
            attempt_span.set_attribute("verdict.hint_count", len(hints.items))
            result = {
                "attempt": 1,
                "items": [
                    {"category": i.category, "text": i.text, "hint": i.hint}
                    for i in hints.items
                ],
                "message": hints.message,
                # Defensive: the judge is told "exactly 4" but the schema
                # can't enforce a length (Cerebras strict bans minItems), and
                # the UI indexes this against a fixed checklist.
                "coveredPoints": (hints.covered_points + [False] * POINT_COUNT)[
                    :POINT_COUNT
                ],
            }
        else:
            attempt_span.set_attribute(
                "langfuse.observation.output", f"score={verdict.score}"
            )
            attempt_span.set_attribute("verdict.score", verdict.score)
            attempt_span.set_attribute(
                "verdict.natural_offered", natural_version is not None
            )
            result = {
                "attempt": 2,
                "correctedText": verdict.corrected_text,
                "explanations": [
                    {"error": e.error, "correction": e.correction, "why": e.why}
                    for e in verdict.explanations
                ],
                "focusPoints": verdict.focus_points,
                "score": verdict.score,
                "feedback": verdict.feedback,
                "improvementsFromFirst": verdict.improvements_from_first,
                "naturalVersion": natural_version,
            }

    if body.attempt == 2:
        # Cross-drill attempt log (DATA-004) — one row per completed letter,
        # written only now because attempt 1 is half an exercise. `correct` is
        # NULL on purpose: a letter earns a score, not a pass/fail, and we are
        # not inventing a threshold to squeeze it into a boolean. Non-fatal
        # like every other log write in the repo.
        try:
            await record_drill_attempt(
                db,
                user_id=user_id,
                exercise="briefkasten",
                item_ref=seed["id"],
                pattern_id=None,
                correct=None,
                modality="written",
                session_id=body.session_id,
            )
        except Exception:
            logger.exception("Drill-attempt log write failed (seed {})", seed["id"])
        # GAME-001 v2: one completed letter *is* the whole exercise — there's
        # no separate "end panel" moment to POST from like satz/flow have, so
        # attempt 2 landing here IS the natural completion point. Non-fatal
        # like the attempt-log write above it.
        try:
            await complete_daily_mode(db, user_id=user_id, mode="briefkasten")
        except Exception:
            logger.exception("Daily-mode completion write failed (seed {})", seed["id"])
    else:
        # SILENT grammar enrichment (GRAM-001) off the critical path. Created
        # only after the span above closes: asyncio.create_task snapshots the
        # CURRENT contextvars, so spawning it inside the `with` would parent
        # the harvest's generation span under an attempt span that has already
        # returned. With no span current it becomes its own Langfuse root
        # trace, grouped by session_id — exactly like Szenario's harvest.
        harvest = asyncio.create_task(
            _background_harvest(response, body.session_id, user_id)
        )
        _background_tasks.add(harvest)
        harvest.add_done_callback(_background_tasks.discard)

    return result
