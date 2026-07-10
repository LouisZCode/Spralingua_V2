"""HTTP routes for Feste Verbindungen (GRAM-002, Exercise D: complete the
fixed verb chunk — reflexive pronoun, fixed preposition, governed case —
with reflexive and non-reflexive verbs MIXED so nothing is predictable).

Same shape as ``bauteil/routes.py``: ledger-weighted round, deterministic
check first, one diagnosis call on misses, non-fatal ledger feedback, OBS-007
tracing under the frontend-minted practice-session id.
"""

import random
import re

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from agents.observability import tracer
from auth.deps import get_current_user_id
from database.connection import get_db
from database.repository import (
    credit_pattern_success,
    load_grammar_focus,
    record_grammar_error,
)
from verbindungen.content import TARGET_PATTERNS, load_items
from verbindungen.judge import judge_chunk

router = APIRouter(prefix="/verbindungen", tags=["verbindungen"])

ROUND_SIZE = 10
# Hot patterns lead but never fill the round — the reflexive/non-reflexive
# MIX is the drill's whole mechanism, so decoys must always be present.
MAX_HOT = 6


@router.get("/round")
async def get_round(
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """One practice round; answers and chunks stay server-side (the chunk
    line would answer the item, so it only ships with the verdict)."""
    items = list(load_items().values())
    try:
        focus = await load_grammar_focus(db, user_id=user_id, limit=10)
        hot = {f["pattern_id"] for f in focus} & set(TARGET_PATTERNS)
    except Exception:
        logger.exception("Verbindungen focus read failed — serving an unweighted round")
        hot = set()

    hot_items = [i for i in items if i["pattern_id"] in hot]
    rest = [i for i in items if i["pattern_id"] not in hot]
    random.shuffle(hot_items)
    random.shuffle(rest)
    chosen = (hot_items[:MAX_HOT] + rest)[:ROUND_SIZE]
    if len(chosen) < ROUND_SIZE:
        chosen += hot_items[MAX_HOT : MAX_HOT + ROUND_SIZE - len(chosen)]
    random.shuffle(chosen)
    return {
        "items": [
            {"id": i["id"], "frame": i["frame"], "hint": i["hint"]} for i in chosen
        ]
    }


_MAX_ANSWER_CHARS = 120

# Same deterministic-match contract as bauteil/routes.py (kept local — each
# exercise module is self-contained like satz/ and bauteil/ are).
_EDGE_PUNCT = " .,!?;:…\"'"


def _normalize(s: str) -> str:
    return " ".join(s.split()).strip(_EDGE_PUNCT).lower()


def _matches(typed: str, expected: str, frame: str) -> bool:
    """Exact match, or the answer embedded in the typed-out sentence — but
    ONLY frame words may surround it. Plain containment would defeat the
    decoys: "mich auf" contains the decoy answer "auf", yet the extra
    pronoun is exactly the error the mix exists to catch — it must reach
    the judge, never green deterministically."""
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
    # OBS-007 practice-sitting id — same contract as the sibling drills.
    session_id: str | None = Field(None, max_length=64)


@router.post("/attempts")
async def submit_attempt(
    body: AttemptIn,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Judge one typed chunk completion, feed the ledger, return the verdict
    + the canonical chunk to memorize (only now — it would answer the item)."""
    item = load_items().get(body.item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Unknown item.")
    answer = " ".join(body.answer.split())
    if not answer:
        raise HTTPException(status_code=422, detail="Type your answer first.")
    if len(answer) > _MAX_ANSWER_CHARS:
        raise HTTPException(
            status_code=422, detail="Keep it to the missing words — that looks like a paragraph."
        )

    with tracer.start_as_current_span("verbindungen-attempt") as attempt_span:
        attempt_span.set_attribute("user.id", user_id)
        attempt_span.set_attribute("item_id", item["id"])
        if body.session_id:
            attempt_span.set_attribute("langfuse.session.id", body.session_id)
        attempt_span.set_attribute("langfuse.trace.input", answer)

        if _matches(answer, item["answer"], item["frame"]):
            correct, note = True, None
        else:
            try:
                diag = await judge_chunk(item, answer)
            except Exception:
                logger.exception("Verbindungen judge call failed (item {})", item["id"])
                raise HTTPException(
                    status_code=502,
                    detail="The judge is unavailable right now — try again in a moment.",
                )
            correct, note = diag.correct, diag.note

        attempt_span.set_attribute(
            "langfuse.trace.output",
            f"correct={correct}" + (f" — {note}" if note else ""),
        )

        # Feed the ledger (design rule 4) — non-fatal, same self-correcting
        # contract as bauteil: a drill-retired pattern that still breaks in
        # speech gets reopened by the spoken harvesters.
        try:
            if correct:
                await credit_pattern_success(
                    db,
                    user_id=user_id,
                    pattern_id=item["pattern_id"],
                    session_id=body.session_id,
                    source="verbindungen",
                )
            else:
                await record_grammar_error(
                    db,
                    user_id=user_id,
                    pattern_id=item["pattern_id"],
                    sentence=item["frame"].replace("___", answer),
                    corrected=item["frame"].replace("___", item["answer"]),
                    note=note,
                    source="verbindungen",
                    session_id=body.session_id,
                )
        except Exception:
            logger.exception(
                "Verbindungen ledger write failed (pattern {})", item["pattern_id"]
            )

        return {
            "correct": correct,
            "expected": item["answer"],
            "chunk": item["chunk"],
            "note": note,
        }
