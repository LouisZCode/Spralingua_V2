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
    classify_noun,
    load_items,
    load_rules,
    phrase_forms,
    trap_why,
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
        # Famous ending-liars get their hand-written teaching line from the
        # exception lexicon (or the curated catalog, when the learner decked
        # a noun we also curate); unknown traps fall back to the generic
        # line built at attempt time.
        "why": trap_why(noun, card.article) if trap else None,
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


@router.get("/rules")
async def get_rules(user_id: str = Depends(get_current_user_id)):
    """The ending cheat sheet for the intro screen — display labels per
    article, in rules.yaml order. Labels only: anchors, reliability, and the
    trap list stay server-side for the drill itself to reveal."""
    endings: dict[str, list[str]] = {a: [] for a in ARTICLES}
    for rule in load_rules().values():
        if rule["id"] == "verbnomen":
            label = "verb→noun"
        elif rule["kind"] == "prefix":
            label = f"{rule['match'][0].capitalize()}-"
        else:
            label = "/".join(f"-{m}" for m in rule["match"])
        endings[rule["article"]].append(label)
    return {"endings": endings}


_MAX_ANSWER_CHARS = 80

_EDGE_PUNCT = " .,!?;:…\"'"


def _tokens(s: str) -> list[str]:
    """Lowercased tokens with edge punctuation stripped — commas and stray
    periods never decide a verdict."""
    return [
        t for t in (tok.strip(_EDGE_PUNCT) for tok in s.lower().split()) if t
    ]


# Carrier-sentence openers whose case we KNOW — the whitelist that lets a
# learner wrap the phrase in a small sentence ("Ich liebe …", "Das ist …")
# without breaking deterministic grading. sein-openers keep the nominative;
# the transitive verbs below force the accusative. Pronoun-verb agreement is
# deliberately not graded here — that's Zeitfärbung/Verbformen territory,
# this drill grades the noun phrase only.
_NOM_OPENERS = frozenset(
    {
        ("das", "ist"), ("es", "ist"), ("hier", "ist"), ("da", "ist"),
        ("dort", "ist"), ("das", "war"), ("es", "war"),
        ("ich", "bin"), ("du", "bist"), ("er", "ist"), ("sie", "ist"),
        ("wir", "sind"), ("ihr", "seid"), ("sie", "sind"),
    }
)
_PRONOUNS = frozenset({"ich", "du", "er", "sie", "es", "wir", "ihr", "man"})
_ACC_FORMS = frozenset(
    {
        "liebe", "liebst", "liebt", "lieben",
        "mag", "magst", "mögen", "mögt",
        "habe", "hast", "hat", "haben", "habt",
        "kaufe", "kaufst", "kauft", "kaufen",
        "suche", "suchst", "sucht", "suchen",
        "sehe", "siehst", "sieht", "sehen", "seht",
        "brauche", "brauchst", "braucht", "brauchen",
        "nehme", "nimmst", "nimmt", "nehmen", "nehmt",
        "finde", "findest", "findet", "finden",
    }
)

_PHRASE_GUIDANCE = (
    "Couldn't read that — type the phrase (ein bequemer Stuhl / der bequeme "
    "Stuhl) or open with a small sentence: Ich liebe … / Das ist …"
)


def _grade_phrase(item: dict, answer: str) -> dict:
    """Grade the typed production deterministically. Accepts the bare phrase
    (definite or indefinite, nominative) or the phrase inside a whitelisted
    carrier sentence — whose opener also fixes the case, so "Ich liebe der
    bequeme Stuhl" is corrected to den, not waved through.

    ``kind="unrecognized"`` is guidance, not a scored verdict — the frontend
    keeps the item live and the DATA-004 log skips it, like Zeitfärbung.
    ``wrongIndex`` is the offending token's index in the TYPED answer so the
    frontend can mark exactly that word red (no strikethrough)."""
    got = _tokens(answer)
    case, rest = "nominative", got
    if len(got) > 3:
        opener = tuple(got[:2])
        if opener in _NOM_OPENERS:
            rest = got[2:]
        elif got[0] in _PRONOUNS and got[1] in _ACC_FORMS:
            case, rest = "accusative", got[2:]
        else:
            return {
                "correct": False,
                "kind": "unrecognized",
                "expected": None,
                "article": item["article"],
                "note": _PHRASE_GUIDANCE,
                "wrongIndex": None,
            }

    art, adj, noun = item["article"], item["adjective"], item["noun"]
    forms = phrase_forms(art, adj, noun, case)

    def display(triple: tuple[str, str, str]) -> str:
        return f"{triple[0]} {triple[1]} {noun}"

    if len(rest) != 3:
        return {
            "correct": False,
            "kind": "shape",
            "expected": display(forms["indefinite"]),
            "article": art,
            "note": "Three words for the phrase: article + adjective + noun.",
            "wrongIndex": None,
        }

    if any(tuple(rest) == f for f in forms.values()):
        matched = next(k for k, f in forms.items() if tuple(rest) == f)
        return {
            "correct": True,
            "kind": "match",
            "expected": display(forms[matched]),
            "article": art,
            "note": None,
            "wrongIndex": None,
        }

    # Diagnose against the form family the learner was going for — someone
    # typing "der …" gets the definite gold, not a lecture about ein.
    family = (
        "definite"
        if rest[0] in ("der", "die", "das", "den", "dem")
        else "indefinite"
    )
    target = forms[family]
    offset = len(got) - 3
    if rest[2] != target[2]:
        kind, wrong, note = "noun", 2, f"Keep the noun as given: {noun}."
    elif rest[0] != target[0]:
        kind, wrong = "article", 0
        note = f"{art}-words take {target[0]} here — {display(target)}."
    else:
        kind, wrong = "adjective", 1
        ending = target[1][len(adj):]
        note = f"After {target[0]}, the adjective needs -{ending}: {target[1]}."
    return {
        "correct": False,
        "kind": kind,
        "expected": display(target),
        "article": art,
        "note": note,
        "wrongIndex": offset + wrong,
    }


def _format_surface(surface: str | None, kind: str) -> str:
    """Render a matched surface the way learners read endings: "-e" for a
    suffix, "Ge-" for a prefix."""
    if not surface:
        return "a pattern"
    return f"{surface}-" if kind == "prefix" else f"-{surface.lower()}"


def _generic_trap_why(item: dict, rule: dict | None) -> str:
    """Fallback teaching line for deck traps outside the exception lexicon."""
    if rule is None:
        return (
            f"Falle! It's {item['article']} {item['noun']} — "
            f"no rule covers it, learn it."
        )
    surface = _match_surface(item["noun"], rule, require_stem=False)
    return (
        f"Falle! {item['noun']} wears {_format_surface(surface, rule['kind'])} "
        f"({rule['article']}-territory) but is {item['article']} "
        f"{item['noun']} — no rule covers it, learn it."
    )


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
                why = item.get("why") or _generic_trap_why(item, rule)
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
                    # The anchor card's header already says "Kein Muster".
                    "note": f"Just remember: {item['article']} {item['noun']}.",
                }
        else:  # phase == "phrase"
            payload = _grade_phrase(item, answer)

        attempt_span.set_attribute(
            "langfuse.trace.output",
            f"correct={payload['correct']} phase={body.phase}"
            + (f" — {payload['note']}" if payload.get("note") else ""),
        )
        attempt_span.set_attribute("verdict.correct", bool(payload["correct"]))

        # Cross-drill attempt log (DATA-004) — its own commit, non-fatal like
        # the sibling drills. Both beats log, distinguished via item_ref, so
        # accuracy can later split "knows the gender" from "can inflect it".
        # "unrecognized" input is NOT an attempt (the frontend keeps the item
        # live and unscored) — it never reaches the log, like Zeitfärbung.
        # NO ledger write here on purpose — see the module docstring.
        try:
            if payload.get("kind") == "unrecognized":
                return payload
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
