"""HTTP route for the on-demand "how would a German say this?" rephrase
(IDIOM-002 Proposal-1).

One POST per learner tap, one judge call per POST, zero cost otherwise.
Deliberately writes NOTHING — no ledger, no drill_attempts, no streak
credit: this is read-only advice on a line the learner already produced,
and the surfaces that show it (verdict cards, tandem bubbles) keep their
own judges' verdicts untouched.
"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from agents.observability import tracer
from auth.deps import get_current_user_id
from idiom.judge import germanize
from security import drill_try_admit

router = APIRouter(prefix="/idiom", tags=["idiom"])

# One spoken turn or one drill attempt — a paragraph means the client sent
# the wrong thing (the letter surface has its own natural_version pass).
_MAX_TEXT_CHARS = 800
_MAX_CONTEXT_CHARS = 800


class RephraseIn(BaseModel):
    # `register` on the wire, `register_` here — the bare name shadows a
    # BaseModel attribute and Pydantic warns at import.
    model_config = ConfigDict(populate_by_name=True)

    # The learner's own line — spoken transcript or typed turn.
    text: str
    # What the line answered (partner's turn / task prompt). Context only —
    # the judge is told it is not theirs to grade.
    context: str | None = None
    register_: Literal["informal", "formal"] = Field("informal", alias="register")
    # OBS-007 practice-sitting id — same contract as the sibling drills.
    session_id: str | None = Field(None, max_length=64)


@router.post("/rephrase")
async def rephrase(
    body: RephraseIn,
    user_id: str = Depends(get_current_user_id),
):
    """Return the plain-but-native version of one learner line, or nulls
    when their German already sounds German (the silence case)."""
    if not drill_try_admit(user_id):
        raise HTTPException(
            status_code=429,
            detail="You're going very fast — take a short break and try again in a few minutes.",
        )
    text = " ".join(body.text.split())
    if not text:
        raise HTTPException(status_code=422, detail="Nothing to rephrase.")
    if len(text) > _MAX_TEXT_CHARS:
        raise HTTPException(
            status_code=422,
            detail="One turn at a time — that looks like a whole letter.",
        )
    context = " ".join(body.context.split()) if body.context else None
    if context and len(context) > _MAX_CONTEXT_CHARS:
        context = context[:_MAX_CONTEXT_CHARS]

    with tracer.start_as_current_span("idiom-rephrase") as span:
        span.set_attribute("user.id", user_id)
        if body.session_id:
            span.set_attribute("session.id", body.session_id)
        try:
            result = await germanize(text, context, body.register_)
        except Exception:
            # Both judge legs down (or a schema mismatch survived retries) —
            # advice is optional, so fail soft with a retryable status
            # rather than a 500 the surface renders as a crash.
            logger.exception("idiom rephrase failed for user {}", user_id)
            raise HTTPException(
                status_code=503,
                detail="The judge is busy — try again in a moment.",
            )
        span.set_attribute("idiom.rewrote", result.natural is not None)

    return {"natural": result.natural, "explanation": result.explanation}
