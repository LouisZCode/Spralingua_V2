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
from agents.observability import propagate_trace_context, tracer
from auth.deps import get_current_user_id
from database.connection import get_sessionmaker
from database.repository import (
    load_user_level,
    record_drill_attempt,
    record_grammar_error,
)
from grammar.levels import bucket_of
from satz.examiner import transcribe_attempt
from security import drill_try_admit

from coins.gate import admit_coins_or_402
from coins.prices import SATZ_ATTEMPT
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
        extraction = await extract_errors(transcript=transcript, session_id=session_id, user_id=user_id)
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


# SZEN-007: account-level bucket (grammar.levels.bucket_of) -> (tier label
# echoed to the client, the scenario field that tier serves from). B1 and
# "no level known" deliberately land on the same entry — the base tier is
# what every pre-LEVEL-001 account already gets.
_TIER_BY_BUCKET: dict[str, tuple[str, str]] = {
    "A1": ("a1", "questions_a1"),
    "A2": ("a2", "questions_a2"),
    "B1": ("b1", "questions"),
    "B2+": ("b2", "questions_b2"),
}
_DEFAULT_TIER: tuple[str, str] = ("b1", "questions")


async def _serving_tier(user_id: str) -> tuple[str, str]:
    """Resolve the tier to serve `user_id` (SZEN-007), shared by both
    `get_scenario` and `get_round` so they always agree on which tier a
    learner is served.

    Reads the account-level bucket (`users.level`, LEVEL-001) via
    `load_user_level` + `grammar.levels.bucket_of`, then maps it to
    `(tier_label, questions_field)` via `_TIER_BY_BUCKET`. Any failure (DB
    unreachable, no level set, etc.) is swallowed and logged — leveling must
    never break the drill — falling back to `_DEFAULT_TIER` ("b1",
    "questions"), same "leveling is never fatal" contract `drills/leveling.py`
    gives the grammar drills.
    """
    bucket = None
    try:
        async with get_sessionmaker()() as db:
            bucket = bucket_of(await load_user_level(db, user_id=user_id))
    except Exception:  # noqa: BLE001 — leveling must never break the drill
        logger.warning("Szenario level read failed (non-fatal) — serving base tier")

    return _TIER_BY_BUCKET.get(bucket, _DEFAULT_TIER)


@router.get("/scenario")
async def get_scenario(
    # VARY-001: comma-separated "scenarioId:questionIndex" tokens the client
    # has already been served this pool cycle FOR THE TIER IT EXPECTS
    # (SZEN-007) — a token's question index only makes sense against the
    # question list it was drawn from, so the seen-token contract is
    # per-tier. The client sends the list for the tier its own account-level
    # guess maps to; the server is authoritative about which tier it
    # actually served (see `"tier"` in the response below) and the client
    # re-keys its stored seen-tokens against THAT, not its guess. Optional —
    # absent behaves exactly like the old stateless draw (full backward
    # compatibility).
    seen: str | None = None,
    user_id: str = Depends(get_current_user_id),
):
    """Pick one scenario and one of its questions, avoiding repeats the
    client has already seen (VARY-001).

    SZEN-007: the question tier served is no longer a client toggle
    (SZEN-005 is superseded) — it is read from the learner's account-level
    bucket (`users.level`, LEVEL-001) via `load_user_level` +
    `grammar.levels.bucket_of`, then mapped straight to a question-list
    field: A1 -> questions_a1, A2 -> questions_a2, B1 -> questions, B2+ ->
    questions_b2. No level set, or a failed read, falls back to the base
    "questions" tier — same "leveling is never fatal" contract
    `drills/leveling.py` gives the grammar drills: a personalisation outage
    must never take the drill dark. The tier actually served is echoed back
    as `"tier"` so the client's per-tier seen-token storage stays correct
    even if its own guess was wrong or stale.

    The `questions` list stays server-side beyond the one chosen — the
    learner sees only the picked question, so the drill stays unpredictable
    on repeat visits to the same scenario.
    """
    tier, questions_key = await _serving_tier(user_id)

    scenarios = [
        {**s, "questions": s[questions_key]} for s in load_scenarios().values()
    ]
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
        "tier": tier,
    }


@router.get("/round")
async def get_round(
    # Flow prefetch size (FLOW-006) — how many items to draw in one call
    # instead of the standalone page's one-at-a-time GET /scenario. Clamped
    # to 1..5 server-side; an out-of-range value is silently clamped rather
    # than rejected, matching this router's "never take the drill dark"
    # posture (see _serving_tier / the tier fallback above).
    n: int = 3,
    # Same per-tier "scenarioId:questionIndex" seen-token contract as
    # GET /scenario's `seen` param (VARY-001, SZEN-007) — see that route's
    # docstring for the full rationale. Here it seeds the FIRST draw of the
    # batch; each subsequent draw in the batch is seeded by this plus every
    # token already drawn earlier in the same call (see below).
    seen: str | None = None,
    user_id: str = Depends(get_current_user_id),
):
    """Draw a small batch of scenario/question pairs for the Flow's prefetch
    buffer (FLOW-006). Szenario-Sparring is joining the Flow, which prefetches
    a few items ahead per drill rather than fetching one at a time — this is
    that batch fetch, sitting alongside the existing single-draw
    `GET /scenario` (still used by the standalone Szenario page).

    Tier resolution is identical to `GET /scenario` (`_serving_tier`,
    SZEN-007): read from the account-level bucket, echoed back as the
    envelope's top-level `"tier"` (not per-item — every item in one batch is
    drawn from the same tier). `seen` carries the same comma-separated
    "scenarioId:questionIndex" tokens as `GET /scenario`, FOR THE TIER THE
    CLIENT EXPECTS — the server is authoritative about which tier it actually
    served, so the client re-keys its stored per-tier seen-tokens against the
    returned `"tier"` if that differs from its own guess.

    Items are drawn by calling `_pick_scenario` `n` times in a row: the first
    draw is seeded with the caller's `seen` tokens, and after each draw the
    just-drawn "scenarioId:questionIndex" token is appended to the working
    list before the next draw. This is exactly what a client calling
    `GET /scenario` `n` times in a row, re-submitting an ever-growing `seen`
    list each time, would produce — so `_pick_scenario`'s no-immediate-repeat
    and cycle-reset semantics fall out for free: consecutive items in the
    batch never share a scenario (unless the pool is exhausted), and pairs
    within one batch are never repeated. If a draw's `cycleReset` fires
    mid-batch, the working list is restarted to hold just that draw's own
    token — mirroring the seen-history a sequential client would have right
    after a reset — so later draws in the same batch treat that as the sole
    seen pair rather than carrying the pre-reset history forward.

    Each item has the same shape as `GET /scenario`'s response minus the
    top-level `tier` field (which moves up to the envelope, shared by the
    whole batch), items ordered as drawn. The client is expected to process
    `cycleReset` sequentially, in item order, when folding this batch's
    tokens into its own stored per-tier seen list — same contract as
    consuming `GET /scenario` one draw at a time.
    """
    n = max(1, min(5, n))
    tier, questions_key = await _serving_tier(user_id)

    scenarios = [
        {**s, "questions": s[questions_key]} for s in load_scenarios().values()
    ]
    working_tokens = seen.split(",") if seen else []

    items = []
    for _ in range(n):
        scenario, question_index, cycle_reset = _pick_scenario(
            scenarios, working_tokens
        )
        question = scenario["questions"][question_index]
        persona = scenario["persona"]

        token = f"{scenario['id']}:{question_index}"
        working_tokens = [token] if cycle_reset else working_tokens + [token]

        items.append(
            {
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
        )

    return {"tier": tier, "items": items}


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
    # PAY-002: 404 BEFORE the charge — a stale scenario id must not burn coins.
    # Szenario has no db param, so open a short-lived session just for the coin
    # gate (same pattern as briefkasten background harvest). Still BEFORE any
    # STT/judge work. Fail closed: a DB outage 503s rather than admitting free.
    from database.connection import get_sessionmaker as _get_sm
    from sqlalchemy.exc import SQLAlchemyError as _SAE
    try:
        async with _get_sm()() as _db:
            try:
                await admit_coins_or_402(_db, user_id=user_id, price=SATZ_ATTEMPT, kind="spend_attempt")
            except HTTPException as _e:
                if _e.status_code == 402:
                    raise
                raise HTTPException(status_code=503, detail="billing temporarily unavailable")
            await _db.commit()
    except HTTPException:
        raise
    except _SAE:
        raise HTTPException(status_code=503, detail="billing temporarily unavailable")

    data = await audio.read()
    if not data:
        raise HTTPException(status_code=422, detail="We didn't get any audio — try again.")
    if len(data) > _MAX_AUDIO_BYTES:
        raise HTTPException(
            status_code=413,
            detail="That recording is too long — one answer is enough.",
        )

    with propagate_trace_context(user_id=user_id, session_id=sessionId), tracer.start_as_current_span("szenario-attempt") as attempt_span:
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
        attempt_span.set_attribute("langfuse.observation.input", transcript)

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
            "langfuse.observation.output",
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
