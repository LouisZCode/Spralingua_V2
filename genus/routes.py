"""HTTP routes for Artikel-Anker: German noun gender, trained as a decision.

Beat 1 (phase="article"): the learner drags der/die/das onto the bare noun —
the physical act of attaching the gender color to the ENDING is the mnemonic.
Beat 2 (phase="phrase"): produce the nominative indefinite carrier phrase
("eine neue Wohnung") so the gender immediately does grammatical work.

Deterministic grading only — NO judge LLM. The article is a three-way choice
and the gold phrase is table-built (``genus/content.py::build_phrase``), so
string matching is the whole grader, like Zeitfärbung.

CONT-002 applies: up to half the round is the learner's own deck nouns
(``VocabCard.article`` is the truth), classified live by ending shape — no
forge, no ``user_drill_items`` rows, everything re-derivable at attempt time.

Deliberately NO grammar-ledger writes: ``grammar/taxonomy.yaml`` scopes the
ledger to structural patterns and excludes lexical slips ("a noun's gender
misremembered … belongs to the vocabulary card's own SRS"). Attempts land in
the cross-drill DATA-004 log only, as ``exercise="genus"``.
"""

import random
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agents.observability import tracer
from auth.deps import get_current_user_id
from database.connection import get_db
from database.orm import UserCard, VocabCard
from database.repository import record_drill_attempt
from genus.content import (
    ARTICLES,
    build_phrase,
    classify_noun,
    load_items,
    load_rules,
    _match_surface,
)

router = APIRouter(prefix="/genus", tags=["genus"])

ROUND_SIZE = 10
# CONT-002: the personal half of a 10-item round.
PERSONAL_MAX = 5
# Curated quotas — every round keeps "is the ending telling the truth?" a
# live question: at least a couple of traps and one pattern-free memory word.
_TRAP_QUOTA = 2
_FREE_QUOTA = 1

# Deck nouns get a semantically-neutral adjective, picked deterministically
# (never randomly) so the item is bit-identical when the attempt endpoint
# re-derives it from the card.
_DECK_ADJECTIVES = ("neu", "klein", "gut", "alt", "schön")


def _deck_item(card: VocabCard) -> dict | None:
    """Build the drill item for one deck noun, or ``None`` when the card
    can't carry the drill (multi-word target, missing/odd article)."""
    noun = (card.target or "").strip()
    if not noun or " " in noun or card.article not in ARTICLES:
        return None
    rule_id, _, trap = classify_noun(noun, card.article)
    return {
        "id": f"deck-{card.id}",
        "noun": noun,
        "article": card.article,
        "gloss": card.gloss or "",
        "rule": rule_id,
        "trap": trap,
        "adjective": _DECK_ADJECTIVES[len(noun) % len(_DECK_ADJECTIVES)],
    }


@router.get("/round")
async def get_round(
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """One practice round. Only ``{id, noun, gloss, adjective}`` ships — the
    article, rule, and anchor stay server-side until the verdict, so devtools
    can't leak the answer mid-drag."""
    personal: list[dict] = []
    try:
        cards = (
            await db.execute(
                select(VocabCard)
                .join(UserCard, UserCard.card_id == VocabCard.id)
                .where(
                    UserCard.user_id == user_id,
                    VocabCard.type == "noun",
                    VocabCard.article.is_not(None),
                )
            )
        ).scalars().all()
        candidates = [item for card in cards if (item := _deck_item(card))]
        random.shuffle(candidates)
        personal = candidates[:PERSONAL_MAX]
    except Exception:
        # A personalisation outage must never break the round.
        logger.exception("Genus deck read failed — serving a curated-only round")
        personal = []

    # A deck noun that also exists in the curated catalog must not appear
    # twice in one round — the personal copy wins.
    personal_nouns = {p["noun"].lower() for p in personal}
    catalog = [
        i for i in load_items().values() if i["noun"].lower() not in personal_nouns
    ]
    traps = [i for i in catalog if i.get("trap")]
    free = [i for i in catalog if not i.get("rule")]
    carriers = [i for i in catalog if i.get("rule") and not i.get("trap")]
    for pool in (traps, free, carriers):
        random.shuffle(pool)

    generic_size = ROUND_SIZE - len(personal)
    chosen = traps[:min(_TRAP_QUOTA, generic_size)]
    chosen += free[: max(0, min(_FREE_QUOTA, generic_size - len(chosen)))]
    chosen += carriers[: generic_size - len(chosen)]
    if len(chosen) < generic_size:
        # Thin carrier pool → pad back from the leftover traps/free words.
        used = {i["id"] for i in chosen}
        leftovers = [i for i in traps + free if i["id"] not in used]
        chosen += leftovers[: generic_size - len(chosen)]

    result = [
        {"id": i["id"], "noun": i["noun"], "gloss": i["gloss"], "adjective": i["adjective"]}
        for i in chosen + personal
    ]
    random.shuffle(result)
    return {"items": result}


_MAX_ANSWER_CHARS = 80

_EDGE_PUNCT = " .,!?;:…\"'"


def _tokens(s: str) -> list[str]:
    """Lowercased tokens with edge punctuation stripped — commas and stray
    periods never decide a verdict."""
    return [
        t for t in (tok.strip(_EDGE_PUNCT) for tok in s.lower().split()) if t
    ]


def _phrase_feedback(item: dict, got: list[str], exp: list[str]) -> tuple[str, str]:
    """Name the one thing that went wrong in a missed phrase — deterministic,
    smallest-distance-first (a wrong noun makes article/adjective moot)."""
    if len(got) != 3:
        return "shape", "Three words: ein/eine + adjective + noun."
    e_art, e_adj, e_noun = exp
    g_art, g_adj, g_noun = got
    if g_noun != e_noun:
        return "noun", f"Keep the noun as given: {item['noun']}."
    if g_art != e_art:
        return (
            "article",
            f"{item['article']}-words take {e_art} here — {e_art} {e_adj} {item['noun']}.",
        )
    if g_adj != e_adj:
        ending = e_adj[len(item["adjective"]):]
        return (
            "adjective",
            f"After {e_art}, the adjective needs -{ending}: {e_adj}.",
        )
    return "other", "Almost — check the spelling character by character."


class AttemptIn(BaseModel):
    item_id: str
    # "article" = the drag (beat 1), "phrase" = the typed production (beat 2).
    phase: Literal["article", "phrase"]
    answer: str
    # OBS-007 practice-sitting id — same contract as the sibling drills.
    session_id: str | None = Field(None, max_length=64)


async def _resolve_item(
    db: AsyncSession, user_id: str, item_id: str
) -> dict | None:
    """Catalog items by id; deck items re-derived from the card — scoped to
    the caller so one user can't probe another's deck by id."""
    if item_id.startswith("deck-"):
        card = (
            await db.execute(
                select(VocabCard)
                .join(UserCard, UserCard.card_id == VocabCard.id)
                .where(
                    UserCard.user_id == user_id,
                    VocabCard.id == item_id[len("deck-"):],
                )
            )
        ).scalars().first()
        return _deck_item(card) if card is not None else None
    return load_items().get(item_id)


@router.post("/attempts")
async def submit_attempt(
    body: AttemptIn,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Grade one beat deterministically, log it, return the verdict (+ the
    anchor scene — only now: shipping it with the round would answer beat 1)."""
    item = await _resolve_item(db, user_id, body.item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Unknown item.")
    answer = " ".join(body.answer.split())
    if not answer:
        raise HTTPException(status_code=422, detail="Type your answer first.")
    if len(answer) > _MAX_ANSWER_CHARS:
        raise HTTPException(
            status_code=422, detail="Keep it to the phrase — that looks like a paragraph."
        )

    rule = load_rules().get(item["rule"]) if item.get("rule") else None

    with tracer.start_as_current_span("genus-attempt") as attempt_span:
        attempt_span.set_attribute("user.id", user_id)
        attempt_span.set_attribute("item_id", item["id"])
        attempt_span.set_attribute("phase", body.phase)
        if body.session_id:
            attempt_span.set_attribute("langfuse.session.id", body.session_id)
        attempt_span.set_attribute("langfuse.trace.input", answer)

        if body.phase == "article":
            choice = answer.lower()
            if choice not in ARTICLES:
                raise HTTPException(status_code=422, detail="Drop der, die, or das.")
            correct = choice == item["article"]
            if not correct:
                # No reveal on a wrong drop — the learner retries. But when a
                # trap word just caught them with its fake ending, say so:
                # noticing the lie is the teachable moment.
                trapped = (
                    bool(item.get("trap"))
                    and rule is not None
                    and choice == rule["article"]
                )
                payload = {
                    "correct": False,
                    "trapped": trapped,
                    "note": "Falle! The ending is lying to you here." if trapped else None,
                }
            elif item.get("trap"):
                surface = (
                    _match_surface(item["noun"], rule, require_stem=False)
                    if rule
                    else None
                )
                why = item.get("why") or (
                    f"Falle! {item['noun']} looks like {surface or 'a pattern'} "
                    f"→ {rule['article'] if rule else '?'}, but it's "
                    f"{item['article']} {item['noun']} — a memory word."
                )
                payload = {
                    "correct": True,
                    "article": item["article"],
                    "segment": None,
                    "anchor": None,
                    "reliability": None,
                    "trap": True,
                    "note": why,
                }
            elif rule is not None:
                surface = _match_surface(item["noun"], rule, require_stem=False)
                payload = {
                    "correct": True,
                    "article": item["article"],
                    "segment": (
                        {"kind": rule["kind"], "text": surface} if surface else None
                    ),
                    "anchor": rule["anchor"],
                    "reliability": rule["reliability"],
                    "trap": False,
                    "note": None,
                }
            else:
                payload = {
                    "correct": True,
                    "article": item["article"],
                    "segment": None,
                    "anchor": None,
                    "reliability": None,
                    "trap": False,
                    "note": (
                        f"Kein Muster — just remember: "
                        f"{item['article']} {item['noun']}."
                    ),
                }
        else:  # phase == "phrase"
            expected = build_phrase(item["article"], item["adjective"], item["noun"])
            got, exp = _tokens(answer), _tokens(expected)
            correct = got == exp
            if correct:
                kind, note = "match", None
            else:
                kind, note = _phrase_feedback(item, got, exp)
            payload = {
                "correct": correct,
                "expected": expected,
                "article": item["article"],
                "kind": kind,
                "note": note,
            }

        attempt_span.set_attribute(
            "langfuse.trace.output",
            f"correct={payload['correct']} phase={body.phase}"
            + (f" — {payload['note']}" if payload.get("note") else ""),
        )
        attempt_span.set_attribute("verdict.correct", bool(payload["correct"]))

        # Cross-drill attempt log (DATA-004) — its own commit, non-fatal like
        # the sibling drills. Both beats log, distinguished via item_ref, so
        # accuracy can later split "knows the gender" from "can inflect it".
        # NO ledger write here on purpose — see the module docstring.
        try:
            await record_drill_attempt(
                db,
                user_id=user_id,
                exercise="genus",
                item_ref=f"{item['id']}:{body.phase}",
                pattern_id=None,
                correct=payload["correct"],
                modality="written",
                session_id=body.session_id,
            )
        except Exception:
            logger.exception("Drill-attempt log write failed (item {})", item["id"])

        return payload
