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

import random
import time

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from agents.error_extractor import extract_errors
from agents.observability import tracer
from auth.deps import get_current_user_id
from database.connection import get_db
from database.repository import record_grammar_error
from satz.examiner import transcribe_attempt
from szenario.content import load_scenarios
from szenario.judge import judge_structure

router = APIRouter(prefix="/szenario", tags=["szenario"])

# One spoken answer per attempt — a single unpredictable question, not a
# multi-sentence production, so the ceiling can stay tight (same cap
# sprechen/routes.py uses for its multi-sentence tasks).
_MAX_AUDIO_BYTES = 2_500_000


@router.get("/scenario")
async def get_scenario(
    user_id: str = Depends(get_current_user_id),
):
    """Pick one scenario and one of its questions at random.

    The `questions` list stays server-side beyond the one chosen — the
    learner sees only the picked question, so the drill stays unpredictable
    on repeat visits to the same scenario.
    """
    scenarios = list(load_scenarios().values())
    scenario = random.choice(scenarios)
    question = random.choice(scenario["questions"])
    persona = scenario["persona"]
    return {
        "scenarioId": scenario["id"],
        "persona": {
            "name": persona["name"],
            "role": persona["role"],
            "attitude": persona["attitude"],
        },
        "kontext": scenario["kontext"],
        "question": question,
        "zielVokabular": scenario["ziel_vokabular"],
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
    db: AsyncSession = Depends(get_db),
):
    """Transcribe one spoken answer, judge its STRUCTURE, silently feed the
    grammar ledger, return transcript + structure verdict. Grammar never
    reaches this response — it's a separate, invisible harvest."""
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
        except Exception:
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
        except Exception:
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
            f"anchorPresent={verdict.anchor_present} closeClean={verdict.close_clean}",
        )

        # SILENT grammar enrichment (GRAM-001) — must NEVER fail the response.
        # Same extractor + ledger contract as pipeline/factory.py's post-session
        # Harvester B: one structured-output pass classifies the transcript's
        # grammar slips against the fixed taxonomy, deduplicated by pattern.
        # extract_errors carries no "clean pattern" signal (no target
        # pattern_id, no passed flag, unlike sprechen's task-targeted judge),
        # so — same as Harvester B — there is nothing to credit via
        # credit_pattern_success here, only errors to record.
        try:
            extraction = await extract_errors(transcript=transcript, session_id=sessionId)
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
                        session_id=sessionId,
                    )
                except Exception:  # noqa: BLE001 — one ledger row must not block the rest
                    logger.exception(
                        "Szenario ledger write failed (pattern {})", err.pattern_id
                    )
        except Exception:  # noqa: BLE001 — the harvest must never break the attempt response
            logger.warning("Szenario grammar extraction failed (non-fatal)")

        return {
            "transcript": transcript,
            "anchor": {
                "present": verdict.anchor_present,
                "note": verdict.anchor_note,
            },
            "sentences": [
                {"text": s.text, "color": s.color, "cut": s.cut}
                for s in verdict.sentences
            ],
            "close": {
                "clean": verdict.close_clean,
                "note": verdict.close_note,
            },
            "skeleton": {
                "kern": verdict.skeleton.kern,
                "punkte": verdict.skeleton.punkte,
                "absprung": verdict.skeleton.absprung,
                "vokabelAnker": verdict.skeleton.vokabel_anker,
            },
            "takeaway": verdict.takeaway,
        }
