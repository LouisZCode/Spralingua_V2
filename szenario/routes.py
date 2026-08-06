"""HTTP routes for Szenario-Sparring (P1, thin slice): an in-character
persona asks the learner ONE unpredictable German question, the learner
speaks ONE answer, and a judge reviews the answer's STRUCTURE — never its
grammar.

Reuses the Satzschmiede attempt machinery for transcription
(``satz/examiner.py::transcribe_attempt``) exactly like ``sprechen/routes.py``.
Grammar is mined silently in the background from the same transcript via the
situation-drill harvester (``agents/error_extractor.py::extract_errors``,
the same function ``pipeline/factory.py``'s post-session Harvester B uses)
and credited to the shared grammar ledger with ``source="szenario"`` — that
part never reaches the response.
"""

import asyncio
import random
import time

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from loguru import logger

from agents.error_extractor import extract_errors
from agents.observability import tracer
from auth.deps import get_current_user_id
from database.connection import get_sessionmaker
from database.repository import record_drill_attempt, record_grammar_error
from satz.examiner import transcribe_attempt
from security import drill_try_admit
from szenario.content import load_scenarios
from szenario.judge import judge_structure

router = APIRouter(prefix="/szenario", tags=["szenario"])

# One spoken answer per attempt — a single unpredictable question, not a
# multi-sentence production, so the ceiling can stay tight (same cap
# sprechen/routes.py uses for its multi-sentence tasks).
_MAX_AUDIO_BYTES = 2_500_000

# TASK 4: strong refs to in-flight background harvests, so the event loop
# never garbage-collects a task mid-run — the standard fire-and-forget
# pattern (a bare `asyncio.create_task(...)` with no other reference is only
# weakly held by the loop). Each task discards itself on completion.
_background_tasks: set[asyncio.Task] = set()


async def _background_harvest(transcript: str, session_id: str, user_id: str) -> None:
    """Fire-and-forget grammar harvest (TASK 4).

    Runs AFTER ``submit_attempt`` has already returned its response — moved
    off the critical path because Langfuse showed it roughly doubling
    attempt latency (a 70s attempt was 32s judge + 37s harvest). Opens its
    OWN DB session (the request-scoped ``db`` from ``Depends(get_db)``
    closes the instant the response returns, so it can never be reused
    here) via the same standalone-session pattern
    ``pipeline/factory.py``'s disconnect block uses for its own post-session
    harvesters. Because the attempt span has already closed by the time this
    runs, ``extract_errors``' own generation span becomes its own root trace
    — grouped into the same Langfuse session via ``session_id`` — exactly
    like the conversation flow's Harvester B. Same non-fatal, log-and-
    swallow contract as the inline block this replaces."""
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
                        source="szenario",
                        session_id=session_id,
                    )
                except Exception:  # noqa: BLE001 — one ledger row must not block the rest
                    logger.exception(
                        "Szenario ledger write failed (pattern {})", err.pattern_id
                    )
    except Exception:  # noqa: BLE001 — the harvest must never break the attempt response
        logger.warning("Szenario grammar extraction failed (non-fatal)")


def _pick_scenario(
    scenarios: list[dict], seen_tokens: list[str]
) -> tuple[dict, int, bool]:
    """Pick one (scenario, questionIndex) pair, favoring pairs absent from
    ``seen_tokens`` (VARY-001) — client-echoed "scenarioId:questionIndex"
    strings. A malformed token, or one naming an unknown scenario or an
    out-of-range question index, is silently dropped.

    No-immediate-repeat: never reuse the LAST seen token's scenario while any
    other scenario still has an unseen question. If every pair has already
    been seen, reset the pool for this pick (the no-immediate-repeat rule
    still applies against the reset pool) — reported via the returned
    cycle_reset flag.
    """
    by_id = {s["id"]: s for s in scenarios}

    seen_pairs: list[tuple[str, int]] = []
    for tok in seen_tokens:
        parts = tok.split(":")
        if len(parts) != 2:
            continue
        scenario_id, idx_str = parts
        scenario = by_id.get(scenario_id)
        if scenario is None:
            continue
        try:
            idx = int(idx_str)
        except ValueError:
            continue
        if not (0 <= idx < len(scenario["questions"])):
            continue
        seen_pairs.append((scenario_id, idx))

    last_scenario_id = seen_pairs[-1][0] if seen_pairs else None

    all_pairs = [
        (s["id"], i) for s in scenarios for i in range(len(s["questions"]))
    ]
    seen_set = set(seen_pairs)
    unseen = [p for p in all_pairs if p not in seen_set]

    cycle_reset = not unseen
    if cycle_reset:
        unseen = all_pairs

    # Prefer a different scenario than the one just served, as long as one
    # with an unseen question remains in the (possibly just-reset) pool.
    other_scenario = [p for p in unseen if p[0] != last_scenario_id]
    pool = other_scenario or unseen

    chosen_id, chosen_idx = random.choice(pool)
    return by_id[chosen_id], chosen_idx, cycle_reset


@router.get("/scenario")
async def get_scenario(
    # VARY-001: comma-separated "scenarioId:questionIndex" tokens the client
    # has already been served this pool cycle. Optional — absent behaves
    # exactly like the old stateless draw (full backward compatibility).
    seen: str | None = None,
    # SZEN-005: "b2" serves each scene's harder questions_b2 tier; anything
    # else (absent, "b1", junk) serves the base tier. The seen-token contract
    # is per-tier — the client keeps separate lists.
    level: str | None = None,
    user_id: str = Depends(get_current_user_id),
):
    """Pick one scenario and one of its questions, avoiding repeats the
    client has already seen (VARY-001).

    The `questions` list stays server-side beyond the one chosen — the
    learner sees only the picked question, so the drill stays unpredictable
    on repeat visits to the same scenario.
    """
    scenarios = list(load_scenarios().values())
    if level == "b2":
        scenarios = [{**s, "questions": s["questions_b2"]} for s in scenarios]
    seen_tokens = seen.split(",") if seen else []
    scenario, question_index, cycle_reset = _pick_scenario(scenarios, seen_tokens)
    question = scenario["questions"][question_index]
    persona = scenario["persona"]
    return {
        "scenarioId": scenario["id"],
        "questionIndex": question_index,
        "persona": {
            "name": persona["name"],
            "role": persona["role"],
            "attitude": persona["attitude"],
        },
        "kontext": scenario["kontext"],
        "question": question,
        "zielVokabular": scenario["ziel_vokabular"],
        "cycleReset": cycle_reset,
    }


@router.post("/attempts")
async def submit_attempt(
    audio: UploadFile = File(...),
    scenarioId: str = Form(...),
    question: str = Form(...),
    # OBS-007: frontend-minted practice-sitting id — one Langfuse Session per
    # trainer visit, same contract as /satz/attempts, /bauteil/attempts and
    # /sprechen/attempts.
    sessionId: str = Form(..., max_length=64),
    user_id: str = Depends(get_current_user_id),
):
    """Transcribe one spoken answer, judge its STRUCTURE, silently feed the
    grammar ledger, return transcript + structure verdict. Grammar never
    reaches this response — it's a separate, invisible harvest that now runs
    in the background (TASK 4), off the response's critical path."""
    if not drill_try_admit(user_id):
        raise HTTPException(
            status_code=429,
            detail="You're going very fast — take a short break and try again in a few minutes.",
        )
    scenario = load_scenarios().get(scenarioId)
    if scenario is None:
        raise HTTPException(status_code=404, detail="Unknown scenario.")

    data = await audio.read()
    if not data:
        raise HTTPException(status_code=422, detail="We didn't get any audio — try again.")
    if len(data) > _MAX_AUDIO_BYTES:
        raise HTTPException(
            status_code=413,
            detail="That recording is too long — one answer is enough.",
        )

    with tracer.start_as_current_span("szenario-attempt") as attempt_span:
        attempt_span.set_attribute("user.id", user_id)
        attempt_span.set_attribute("scenario_id", scenario["id"])
        attempt_span.set_attribute("langfuse.session.id", sessionId)

        t0 = time.perf_counter()
        try:
            transcript = await transcribe_attempt(
                data, audio.content_type, keyterms=scenario.get("ziel_vokabular")
            )
        except Exception as exc:
            attempt_span.record_exception(exc)
            logger.exception("Szenario transcription failed (scenario {})", scenarioId)
            raise HTTPException(
                status_code=502,
                detail="Couldn't process the audio — try again in a moment.",
            )
        t_stt = time.perf_counter()
        if not transcript:
            raise HTTPException(
                status_code=422,
                detail="We couldn't hear anything — try again.",
            )
        attempt_span.set_attribute("langfuse.trace.input", transcript)

        try:
            verdict = await judge_structure(
                question=question,
                transcript=transcript,
                ziel_vokabular=scenario["ziel_vokabular"],
                user_id=user_id,
            )
        except Exception as exc:
            attempt_span.record_exception(exc)
            logger.exception("Szenario judge call failed (scenario {})", scenarioId)
            raise HTTPException(
                status_code=502,
                detail="The judge is unavailable right now — try again in a moment.",
            )
        t_llm = time.perf_counter()
        logger.info(
            "Szenario attempt timing: stt={:.2f}s llm={:.2f}s (scenario {})",
            t_stt - t0,
            t_llm - t_stt,
            scenarioId,
        )
        attempt_span.set_attribute(
            "langfuse.trace.output",
            f"verdict={verdict.verdict} level={verdict.level_read}",
        )
        # Structured verdict attributes so Langfuse can filter without
        # string-parsing the free-text trace.output above.
        attempt_span.set_attribute("verdict.verdict", verdict.verdict)
        attempt_span.set_attribute("verdict.level_read", verdict.level_read)

        response = {
            "transcript": transcript,
            "verdict": verdict.verdict,
            "levelRead": verdict.level_read,
            "coachMessage": verdict.coach_message,
            "sentences": [
                {"text": s.text, "weight": s.weight, "simpler": s.simpler}
                for s in verdict.sentences
            ],
            "skeleton": {
                "kern": verdict.skeleton.kern,
                "punkte": verdict.skeleton.punkte,
                "absprung": verdict.skeleton.absprung,
                "vokabelAnker": verdict.skeleton.vokabel_anker,
            },
        }

    # Append to the cross-drill attempt log (DATA-004). Szenario has no
    # request-scoped `db` (removed when the harvest went background, see
    # `_background_harvest`'s docstring) — open a standalone session for
    # this one INSERT, same pattern the background harvest itself uses. A
    # single insert is ~ms, so it rides inline here rather than inside the
    # background task: the attempt row must exist even if the (much slower,
    # LLM-backed) grammar harvest never completes. Non-fatal like every
    # other ledger/log write in this file — pattern_id/correct are always
    # NULL for Szenario (a coach, not a grader; see the migration docstring).
    try:
        async with get_sessionmaker()() as attempt_db:
            await record_drill_attempt(
                attempt_db,
                user_id=user_id,
                exercise="szenario",
                item_ref=scenarioId,
                pattern_id=None,
                correct=None,
                modality="spoken",
                session_id=sessionId,
            )
    except Exception:
        logger.exception("Drill-attempt log write failed (scenario {})", scenarioId)

    # SILENT grammar enrichment (GRAM-001) — must NEVER fail, and (TASK 4)
    # must never ride on the response latency either: fire-and-forget, kicked
    # off only AFTER the `with` block above exits. asyncio.create_task copies
    # the CURRENT contextvars snapshot at creation time, not at execution
    # time — creating it while `attempt_span` was still the current span
    # would silently parent the harvest's generation span under it even
    # though it runs later. Waiting until here means no span is current, so
    # extract_errors' own generation span becomes its own Langfuse ROOT
    # trace, grouped only by session_id — exactly like the conversation
    # flow's post-session Harvester B (pipeline/factory.py), which also runs
    # with no span current. Same extractor + ledger contract as that
    # harvester: one structured-output pass classifies the transcript's
    # grammar slips against the fixed taxonomy, deduplicated by pattern.
    # extract_errors carries no "clean pattern" signal (no target pattern_id,
    # no passed flag, unlike sprechen's task-targeted judge), so — same as
    # Harvester B — there is nothing to credit via credit_pattern_success
    # here, only errors to record. See _background_harvest for the DB-session
    # and non-fatal contract.
    harvest_task = asyncio.create_task(
        _background_harvest(transcript, sessionId, user_id)
    )
    _background_tasks.add(harvest_task)
    harvest_task.add_done_callback(_background_tasks.discard)

    return response
