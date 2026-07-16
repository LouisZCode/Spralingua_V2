"""HTTP routes for Bauteil-Sätze (GRAM-002, Exercise A: build the inflected
phrase from raw parts — a WRITTEN drill, no audio, no SRS).

Two endpoints behind the same session-JWT + per-request AsyncSession
dependencies as ``satz/routes.py``:

- ``GET /bauteil/round`` — ten items, weighted toward the learner's hot open
  ledger patterns (``load_grammar_focus``) but never monopolized by them:
  deciding *which* structure applies is part of the drill (design rule 1).
- ``POST /bauteil/attempts`` — judge one typed phrase. A deterministic string
  check green-lights exact (or sentence-embedded) matches at zero LLM cost;
  only misses pay for the diagnosis call. Every attempt then feeds the
  grammar-error ledger (design rule 4): a miss is a slip, a green credits the
  retire streak — both tagged ``source="bauteil"``, both non-fatal.
"""

import random
import re

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from agents.observability import tracer
from auth.deps import get_current_user_id
from bauteil.content import TARGET_PATTERNS, load_items
from bauteil.judge import judge_attempt
from database.connection import get_db
from database.repository import (
    credit_pattern_success,
    load_grammar_focus,
    record_drill_attempt,
    record_grammar_error,
)

router = APIRouter(prefix="/bauteil", tags=["bauteil"])

ROUND_SIZE = 10
# Hot ledger patterns lead the round but never fill it: at least 4 of 10 items
# come from outside the learner's weak spots so the mix keeps "which rule even
# applies here?" a live question instead of telegraphing the answer.
MAX_HOT = 6


@router.get("/round")
async def get_round(
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """One practice round. The answer never leaves the server — checking is
    the POST's job, so devtools can't leak the solution mid-drill."""
    items = list(load_items().values())
    try:
        focus = await load_grammar_focus(db, user_id=user_id, limit=10)
        hot = {f["pattern_id"] for f in focus} & set(TARGET_PATTERNS)
    except Exception:
        # Ledger outage = a less personalised round, never a failed one.
        logger.exception("Bauteil focus read failed — serving an unweighted round")
        hot = set()

    hot_items = [i for i in items if i["pattern_id"] in hot]
    rest = [i for i in items if i["pattern_id"] not in hot]
    random.shuffle(hot_items)
    random.shuffle(rest)
    chosen = (hot_items[:MAX_HOT] + rest)[:ROUND_SIZE]
    if len(chosen) < ROUND_SIZE:
        # Every pattern is hot → `rest` ran short; pad back from the hot pool.
        chosen += hot_items[MAX_HOT : MAX_HOT + ROUND_SIZE - len(chosen)]
    random.shuffle(chosen)
    return {
        "items": [
            {"id": i["id"], "parts": i["parts"], "frame": i["frame"], "hint": i["hint"]}
            for i in chosen
        ]
    }


# Learners type one phrase; anything longer than a generous full sentence is
# a paste accident, not an attempt.
_MAX_ANSWER_CHARS = 120

_EDGE_PUNCT = " .,!?;:…\"'"


def _normalize(s: str) -> str:
    return " ".join(s.split()).strip(_EDGE_PUNCT).lower()


def _matches(typed: str, expected: str, frame: str) -> bool:
    """Deterministic green: the exact phrase, or the phrase embedded in the
    typed-out sentence. Containment alone would be a hole — extra NON-frame
    words around the phrase (an added pronoun, a different preposition) must
    fall through to the judge — so whatever surrounds the match must consist
    of frame words only (i.e. the learner typed the sentence, nothing else)."""
    t, e = _normalize(typed), _normalize(expected)
    if t == e:
        return True
    m = re.search(rf"(?<!\w){re.escape(e)}(?!\w)", t)
    if m is None:
        return False
    frame_words = set(_normalize(frame.replace("___", " ")).split())
    leftover = (t[: m.start()] + " " + t[m.end():]).split()
    return all(w in frame_words for w in leftover)


class AttemptIn(BaseModel):
    item_id: str
    answer: str
    # OBS-007: frontend-minted practice-sitting id — one Langfuse Session per
    # trainer visit, same contract as /satz/attempts. Optional so curl works.
    session_id: str | None = Field(None, max_length=64)


@router.post("/attempts")
async def submit_attempt(
    body: AttemptIn,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Judge one typed phrase, feed the ledger, return the two-axis verdict."""
    item = load_items().get(body.item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Unknown item.")
    answer = " ".join(body.answer.split())
    if not answer:
        raise HTTPException(status_code=422, detail="Type your answer first.")
    if len(answer) > _MAX_ANSWER_CHARS:
        raise HTTPException(
            status_code=422, detail="Keep it to the phrase — that looks like a paragraph."
        )

    # OBS-007: one Langfuse trace per judged attempt, grouped into the
    # practice sitting. Deterministic greens trace too (no `llm` child) —
    # a session should show every attempt, not just the ones that cost tokens.
    with tracer.start_as_current_span("bauteil-attempt") as attempt_span:
        attempt_span.set_attribute("user.id", user_id)
        attempt_span.set_attribute("item_id", item["id"])
        if body.session_id:
            attempt_span.set_attribute("langfuse.session.id", body.session_id)
        attempt_span.set_attribute("langfuse.trace.input", answer)

        if _matches(answer, item["answer"], item["frame"]):
            correct, case_ok, carrier_ok, note = True, True, True, None
            # TASK 2: a deterministic green never calls the LLM judge — flag
            # that explicitly so Langfuse can tell "judged correct" apart
            # from "matched without a judge call".
            attempt_span.set_attribute("judge_skipped", True)
        else:
            try:
                diag = await judge_attempt(item, answer)
            except Exception as exc:
                attempt_span.record_exception(exc)
                logger.exception("Bauteil judge call failed (item {})", item["id"])
                raise HTTPException(
                    status_code=502,
                    detail="The judge is unavailable right now — try again in a moment.",
                )
            correct, case_ok, carrier_ok, note = (
                diag.correct,
                diag.case_ok,
                diag.carrier_ok,
                diag.note,
            )

        attempt_span.set_attribute(
            "langfuse.trace.output",
            f"correct={correct} caseOk={case_ok} carrierOk={carrier_ok}"
            + (f" — {note}" if note else ""),
        )
        # Structured verdict attributes so Langfuse can filter without
        # string-parsing the free-text trace.output above.
        attempt_span.set_attribute("verdict.correct", bool(correct))
        attempt_span.set_attribute("verdict.case_ok", bool(case_ok))
        attempt_span.set_attribute("verdict.carrier_ok", bool(carrier_ok))

        # Feed the grammar-error ledger (design rule 4) — non-fatal, so a DB
        # outage can never break the attempt it rides on. A drilled green
        # credits the retire streak; if the pattern still breaks in speech,
        # the spoken harvesters reopen it — the ledger self-corrects.
        try:
            if correct:
                await credit_pattern_success(
                    db,
                    user_id=user_id,
                    pattern_id=item["pattern_id"],
                    session_id=body.session_id,
                    source="bauteil",
                )
            else:
                await record_grammar_error(
                    db,
                    user_id=user_id,
                    pattern_id=item["pattern_id"],
                    sentence=item["frame"].replace("___", answer),
                    corrected=item["frame"].replace("___", item["answer"]),
                    note=note,
                    source="bauteil",
                    session_id=body.session_id,
                )
        except Exception:
            logger.exception(
                "Bauteil ledger write failed (pattern {})", item["pattern_id"]
            )

        # Append to the cross-drill attempt log (DATA-004) — its own commit,
        # non-fatal like the ledger write above. Deterministic greens count
        # as `correct=True` here too, same as the ledger credit above.
        try:
            await record_drill_attempt(
                db,
                user_id=user_id,
                exercise="bauteil",
                item_ref=item["id"],
                pattern_id=item["pattern_id"],
                correct=correct,
                modality="written",
                session_id=body.session_id,
            )
        except Exception:
            logger.exception("Drill-attempt log write failed (item {})", item["id"])

        # camelCase like every other practice payload.
        return {
            "correct": correct,
            "expected": item["answer"],
            "caseOk": case_ok,
            "carrierOk": carrier_ok,
            "note": note,
        }
