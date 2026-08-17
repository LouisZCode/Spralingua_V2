"""Forge extra leveled example sentences per vocabulary card (SATZ-017).

Every card carries ONE example (hand-authored in the packs, enricher-written
for community adds), so the learner re-reads the same sentence on every
encounter. This forge writes ``cards.examples`` — up to three fresh natural
sentences at ranged difficulty (simple ~A2 / mid ~B1 / richer ~B1+) — once
per card, so the frontend can rotate a different example per encounter.

Content is CARD-level (the shared catalog), not per-user: one forge ever per
card, visible to everyone, exactly like the rest of the card's fields.

Gold-answer guardrail (CONT-002 precedent — a wrong example teaches wrong
German): every drafted sentence must pass (1) the examiner's deterministic
target-presence scan and (2) a verify-LLM gate (correct, natural, word used
in its taught meaning). Fewer than two survivors → the card keeps SQL NULL
and a later backfill retries with a fresh draft; like drills/forge, one
in-call retry smooths over a flaky first draft.

Runs strictly off the request path: fired via ``asyncio.create_task`` from
the add-word route and from ``GET /satz/deck``'s lazy backfill. Never
raises — a forge failure must never surface anywhere a learner can see.
"""

from typing import Optional

from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy import select

from agents.observability import (
    generation_span,
    record_generation_output,
    unwrap_structured_output,
)
from agents.openrouter_llm import structured_judge_llm
from database.connection import get_sessionmaker
from database.orm import UserCard, VocabCard
from satz.examiner import _target_evidence

FORGE_MODEL = "openai/gpt-oss-120b"


class ExampleDraft(BaseModel):
    simple: str = Field(
        description="One SHORT, simple natural German sentence (~A2) using the word: one main clause, everyday topic"
    )
    mid: str = Field(
        description="One natural German sentence (~B1) using the word: a bit longer, e.g. with a time/place phrase or two coordinated clauses"
    )
    rich: str = Field(
        description="One more complex natural German sentence (~B1+) using the word: include a subordinate clause or a more nuanced everyday context"
    )


class ExamplesVerify(BaseModel):
    ok: list[bool] = Field(
        description="One boolean per numbered sentence, in order: true ONLY if the sentence is fully correct, natural German AND uses the target word correctly in its stated meaning"
    )


DRAFT_PROMPT = """# Role
You write example sentences for a German vocabulary card. The learner sees ONE of them next to the word as a model of natural usage.

# The card
{facts}

# Rules
- Every sentence MUST contain the target word (an inflected form is fine).
- The three sentences must differ from each other AND from the existing example in topic and structure — three genuinely different ways to use the word.
- Everyday, spoken-leaning German; no poetry, no idioms that obscure the word's meaning.
- Standard German orthography: capitalize the sentence start and every noun.
"""

VERIFY_PROMPT = """# Role
You are a strict German-language reviewer. For each numbered sentence, answer whether it is fully correct, natural German AND uses the target word correctly in its stated meaning. Reject anything ungrammatical, unnatural, or using the word in a wrong sense.

# The card
{facts}

# Sentences
{sentences}
"""


def _facts(card: VocabCard) -> str:
    lines = [f"- word to use: {card.target} ({card.type})", f"- meaning: {card.gloss}"]
    if card.article:
        lines.append(f"- it is a noun; its article is: {card.article}")
    if card.reflexive:
        lines.append(
            "- it is a REFLEXIVE verb: every sentence must pair it with the "
            "matching reflexive pronoun (mich/dich/sich/uns/euch)"
        )
    if card.tense == "past":
        lines.append(
            "- this card teaches the SPOKEN PAST: every sentence must use the "
            "verb in a past tense (Perfekt or Präteritum both count)"
        )
        if card.tense_form:
            lines.append(f"- its natural spoken past: {card.tense_form}")
    if card.type == "adverb":
        lines.append(
            "- it is an adverb: show it in three DIFFERENT positions across "
            "the three sentences (front field, mid-field, right after the "
            "verb), never as a predicate adjective"
        )
    if card.note:
        lines.append(f"- grammar note: {card.note}")
    if card.example:
        lines.append(f"- existing example (write three DIFFERENT sentences): {card.example}")
    return "\n".join(lines)


async def _draft_and_verify(card: VocabCard, user_id: Optional[str]) -> Optional[list[str]]:
    """One draft + verify round. Returns the surviving sentences (may be
    fewer than 3) or None when nothing usable came back."""
    facts = _facts(card)
    prompt = DRAFT_PROMPT.replace("{facts}", facts)

    # JUDGE-001 (2026-08-15): drafts three example sentences, not a verdict —
    # temperature=None keeps provider-default sampling so the three drafts
    # (and repeat runs for the same card) aren't identical. The verify LLM
    # below is a verdict and keeps the new temperature=0 default.
    llm = structured_judge_llm(ExampleDraft, temperature=None)
    with generation_span(
        "satz-example-forge", model=FORGE_MODEL, input_text=prompt, user_id=user_id
    ) as span:
        result, usage, response_metadata = unwrap_structured_output(await llm.ainvoke(prompt))
        record_generation_output(span, result.model_dump_json(), usage, response_metadata)

    extra_forms = [card.tense_form] if card.tense_form else None
    seen_lower = {(card.example or "").strip().lower()}
    candidates: list[str] = []
    for raw in (result.simple, result.mid, result.rich):
        s = " ".join((raw or "").split())
        if not s or s.lower() in seen_lower:
            continue
        # Deterministic presence gate — a sentence without the target word
        # can never reach a learner, whatever the verify LLM thinks.
        if not _target_evidence(card, s, extra_forms=extra_forms):
            logger.warning("Example forge: target {!r} absent from draft {!r}", card.target, s)
            continue
        seen_lower.add(s.lower())
        candidates.append(s)

    if not candidates:
        return None

    numbered = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(candidates))
    verify_llm = structured_judge_llm(ExamplesVerify)
    verify_prompt = VERIFY_PROMPT.replace("{facts}", facts).replace("{sentences}", numbered)
    with generation_span(
        "satz-example-forge-verify",
        model=FORGE_MODEL,
        input_text=verify_prompt,
        user_id=user_id,
    ) as span:
        vresult, vusage, vmeta = unwrap_structured_output(await verify_llm.ainvoke(verify_prompt))
        record_generation_output(span, vresult.model_dump_json(), vusage, vmeta)

    kept = [
        s
        for s, ok in zip(candidates, vresult.ok)
        if ok is True
    ]
    return kept or None


async def forge_card_examples(card_id: str, user_id: Optional[str] = None) -> None:
    """Background entry point (fired via ``asyncio.create_task`` — never
    awaited on the request path). Opens its own DB session; never raises."""
    try:
        sessionmaker = get_sessionmaker()
        async with sessionmaker() as db:
            card = await db.get(VocabCard, card_id)
            if card is None or card.examples is not None:
                return

            kept: list[str] = []
            for attempt in (1, 2):
                round_kept = await _draft_and_verify(card, user_id)
                if round_kept:
                    kept = round_kept
                if len(kept) >= 2:
                    break
                logger.warning(
                    "Example forge round {} kept {} sentence(s) for {!r}",
                    attempt, len(kept), card.target,
                )

            if len(kept) < 2:
                # Leave SQL NULL so a later backfill retries — never assign
                # Python None (the CONT-002 none_as_null gotcha).
                logger.warning("Example forge gave up on {!r} for now", card.target)
                return

            card.examples = kept[:3]
            await db.commit()
            logger.info("Forged {} examples for card {}", len(kept[:3]), card_id)
    except Exception:
        logger.exception("Example forge failed (card {})", card_id)


async def backfill_card_examples(user_id: str, limit: int = 2) -> None:
    """Lazy backfill fired from ``GET /satz/deck`` (``asyncio.create_task``,
    never awaited): forge examples for up to ``limit`` of the caller's cards
    that have none yet — covers the whole pre-SATZ-017 catalog over normal
    usage. Fully guarded; a failure must never surface to the deck fetch."""
    try:
        sessionmaker = get_sessionmaker()
        async with sessionmaker() as db:
            card_ids = (
                await db.execute(
                    select(VocabCard.id)
                    .join(UserCard, UserCard.card_id == VocabCard.id)
                    .where(
                        UserCard.user_id == user_id,
                        VocabCard.examples.is_(None),
                    )
                    .order_by(UserCard.added_at)
                    .limit(limit)
                )
            ).scalars().all()
        for card_id in card_ids:
            await forge_card_examples(card_id, user_id)
    except Exception:
        logger.exception("Example backfill failed (user {})", user_id)
