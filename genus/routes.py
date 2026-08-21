"""HTTP routes for Artikel-Anker: German noun gender, trained as a decision.

Beat 1 (phase="article"): the learner drags der/die/das onto the bare noun —
the physical act of attaching the gender color to the ENDING is the mnemonic.
Beat 2 (phase="phrase"): produce the nominative indefinite carrier phrase
("eine neue Wohnung") so the gender immediately does grammatical work.

Grading is hybrid, Bauteil-style: the article drop and the recognized phrase
shapes are deterministic (table-built gold forms, zero LLM cost); free-text
productions — any sentence with the target noun, ≤140 chars — fall through
to ``genus/judge.py``'s one-shot gender judge, which checks exactly one
axis: is the noun's gender used correctly. Only unparseable input pays for
a judge call; every deterministic outcome stays free and instant.

CONT-002 applies: up to half the round is the learner's own deck nouns
(``VocabCard.article`` is the truth), classified live by ending shape — no
forge, no ``user_drill_items`` rows, everything re-derivable at attempt time.

Deliberately NO grammar-ledger writes: ``grammar/taxonomy.yaml`` scopes the
ledger to structural patterns and excludes lexical slips ("a noun's gender
misremembered … belongs to the vocabulary card's own SRS"). Attempts land in
the cross-drill DATA-004 log only, as ``exercise="genus"``.
"""

import random
import zlib
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from agents.observability import propagate_trace_context, tracer
from auth.deps import get_current_user_id
from database.connection import get_db
from database.orm import UserCard, VocabCard
from database.repository import record_drill_attempt
from genus.content import (
    ARTICLES,
    SAFE_ADJECTIVES,
    classify_noun,
    load_items,
    load_pools,
    load_rules,
    phrase_forms,
    trap_why,
    _match_surface,
)
from genus.judge import GenderVerdict, judge_gender
from genus.nudge import suggest_vocab
from security import drill_try_admit
from vocab_nudge import filter_picks, load_deck

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
# re-derives it from the card. Neutral subset of content.SAFE_ADJECTIVES
# only — a deck noun can be a person or an abstract, so food/texture words
# ("lecker", "frisch") would pair absurdly. crc32 (process-stable, unlike
# hash()) spreads nouns across the pool far better than len() ever did.
# GEN-001b: this is now the fallback half — when the learner has concat-safe
# adjectives of their own decked, half the deck items wear those instead
# (see _deck_item's adj_pool split), so the drill keeps pace with their
# actual vocabulary rather than looping the same 12 words forever.
_DECK_ADJECTIVES = (
    "neu", "alt", "klein", "groß", "gut", "schön",
    "modern", "wichtig", "nett", "ruhig", "stark", "schnell",
)
# Fail loud at import if the tuple ever drifts outside the concat-safe set —
# a non-safe adjective would silently produce wrong gold endings.
assert set(_DECK_ADJECTIVES) <= SAFE_ADJECTIVES, "deck adjective not concat-safe"


def _concat_safe(adjective: str) -> bool:
    """Can this adjective take its endings by pure concatenation?

    Conservative shape check for learner-decked adjectives that aren't in
    the curated SAFE_ADJECTIVES set: reject anything that declines with
    elision or suppletion (dunkel→dunkle, teuer→teure, hoch→hohe), ends in
    a vowel (leise+e, rosa is indeclinable), or isn't a single lowercase
    word. False negatives are fine — the static pool covers the noun.
    """
    a = adjective.strip()
    if a in SAFE_ADJECTIVES:
        return True
    return (
        a.isalpha()
        and a == a.lower()
        and len(a) >= 3
        and not a.endswith(("a", "e", "i", "o", "u", "el", "er"))
        and a != "hoch"
    )


def _safe_deck_adjectives(cards) -> tuple[str, ...]:
    """The learner's own concat-safe adjectives, sorted for determinism.

    GEN-001b: sorted so the crc32 pick below is stable across requests as
    long as the deck's adjective set is unchanged. (If the set changes
    between a round being served and its attempt being graded, one item's
    re-derived adjective can differ — accepted: rare, self-healing next
    round.)
    """
    return tuple(
        sorted(
            {
                t
                for card in cards
                if card.type == "adjective"
                and (t := (card.target or "").strip())
                and " " not in t
                and _concat_safe(t)
            }
        )
    )


def _deck_item(card: VocabCard, deck_adjs: tuple[str, ...] = ()) -> dict | None:
    """Build the drill item for one deck noun, or ``None`` when the card
    can't carry the drill (multi-word target, missing/odd article)."""
    noun = (card.target or "").strip()
    if not noun or " " in noun or card.article not in ARTICLES:
        return None
    rule_id, _, trap = classify_noun(noun, card.article)
    h = zlib.crc32(noun.encode("utf-8"))
    # GEN-001b: the learner's own adjectives carry half the deck items
    # (hash-deterministic, so round and attempt re-derive identically);
    # the static neutral pool carries the rest — and everything, when
    # the deck has no safe adjective yet.
    adj_pool = deck_adjs if deck_adjs and h & 1 else _DECK_ADJECTIVES
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
        "adjective": adj_pool[(h >> 1) % len(adj_pool)],
    }


@router.get("/round")
async def get_round(
    pool: str = "basis",
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """One practice round. Only ``{id, noun, gloss, adjective}`` ships — the
    article, rule, and anchor stay server-side until the verdict, so devtools
    can't leak the answer mid-drag.

    ``pool`` picks the curated half's themed pool (default ``basis``); the
    deck half is always the learner's own nouns regardless of pool. An
    unknown pool id (stale localStorage after a pool rename) degrades to
    basis rather than failing the round."""
    personal: list[dict] = []
    try:
        cards = (
            await db.execute(
                select(VocabCard)
                .join(UserCard, UserCard.card_id == VocabCard.id)
                .where(
                    UserCard.user_id == user_id,
                    VocabCard.type.in_(("noun", "adjective")),
                )
            )
        ).scalars().all()
        # Adjective cards have no article — the ``noun`` + non-NULL-article
        # filter that used to live in SQL now runs in Python so this one
        # query can also pull the learner's adjectives for GEN-001b.
        noun_cards = [
            c for c in cards if c.type == "noun" and c.article is not None
        ]
        deck_adjs = _safe_deck_adjectives(cards)
        candidates = [
            item
            for card in noun_cards
            if (item := _deck_item(card, deck_adjs))
        ]
        random.shuffle(candidates)
        personal = candidates[:PERSONAL_MAX]
    except Exception:
        # A personalisation outage must never break the round.
        logger.exception("Genus deck read failed — serving a curated-only round")
        personal = []

    pools = load_pools()
    selected = pools.get(pool)
    if selected is None:
        logger.warning("Unknown genus pool {!r} — serving basis", pool)
        selected = pools["basis"]

    # A deck noun that also exists in the curated catalog must not appear
    # twice in one round — the personal copy wins.
    personal_nouns = {p["noun"].lower() for p in personal}
    catalog = [
        i
        for i in selected["items"].values()
        if i["noun"].lower() not in personal_nouns
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
    return {
        "endings": endings,
        # The selectable noun pools, display order — the intro chips.
        "pools": [
            {"id": p["id"], "title": p["title"]}
            for p in sorted(load_pools().values(), key=lambda p: p["position"])
        ],
    }


# Free-text productions are welcome (the judge grades them) — the cap is
# only a paste-accident guard.
_MAX_ANSWER_CHARS = 140

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

# The determiner tokens the deterministic miss-diagnosis understands. A
# 3-token phrase led by anything else (meine gute Absicht, diese alte Tür)
# is perfectly legal German — it goes to the judge, never to a deterministic
# "wrong article" verdict that would be a lie.
_DET_TOKENS = frozenset(
    {"der", "die", "das", "den", "dem",
     "ein", "eine", "einen", "einem", "einer", "eines"}
)


def _grade_phrase(item: dict, answer: str) -> dict | None:
    """The deterministic fast path. Accepts the bare 3-word phrase (definite
    or indefinite) or that phrase inside a whitelisted carrier sentence —
    whose opener also fixes the case, so "Ich liebe der bequeme Stuhl" is
    corrected to den, not waved through. Returns ``None`` for everything
    else — free text the judge should grade instead.

    ``kind="unrecognized"`` is guidance, not a scored verdict — the frontend
    keeps the item live and the DATA-004 log skips it, like Zeitfärbung.
    ``wrongIndex`` is the offending token's index in the TYPED answer so the
    frontend can mark exactly that word red (no strikethrough)."""
    got = _tokens(answer)
    noun_l = item["noun"].lower()
    # startswith, not equality: inflection may extend the noun (den Kunden,
    # des Stuhls). A sentence without the target noun is guidance, not an
    # attempt — and never worth a judge call.
    if not any(t.startswith(noun_l) for t in got):
        return {
            "correct": False,
            "kind": "unrecognized",
            "expected": None,
            "article": item["article"],
            "note": (
                f"Use {item['noun']} in your answer — the phrase, "
                f"or any sentence of your own."
            ),
            "wrongIndex": None,
        }
    case, rest = "nominative", got
    if len(got) > 3:
        opener = tuple(got[:2])
        if opener in _NOM_OPENERS:
            rest = got[2:]
        elif got[0] in _PRONOUNS and got[1] in _ACC_FORMS:
            case, rest = "accusative", got[2:]
        else:
            return None  # free text → judge
    if len(rest) != 3 or rest[0] not in _DET_TOKENS:
        return None  # bare/possessive/longer NP → judge

    art, adj, noun = item["article"], item["adjective"], item["noun"]
    forms = phrase_forms(art, adj, noun, case)

    def display(triple: tuple[str, str, str]) -> str:
        return f"{triple[0]} {triple[1]} {noun}"

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


def _judge_payload(item: dict, answer: str, v: GenderVerdict) -> dict:
    """Map the judge's verdict onto the phrase-payload contract. A sentence
    where the gender never surfaces (plural, bare noun) is guidance, not a
    scored miss — same unrecognized contract as the deterministic path."""
    if not v.gender_shown:
        return {
            "correct": False,
            "kind": "unrecognized",
            "expected": None,
            "article": item["article"],
            "note": (
                v.note
                if v.note and v.note != "ok"
                else (
                    f"Use {item['noun']} in the singular with an article "
                    f"so the gender shows."
                )
            ),
            "wrongIndex": None,
        }
    if v.correct:
        return {
            "correct": True,
            "kind": "match",
            "expected": (v.corrected or answer).strip(),
            "article": item["article"],
            "note": None,
            "wrongIndex": None,
        }
    # Locate the judge's offending word in the typed answer so the frontend
    # can mark exactly that token red — same index convention the
    # deterministic path uses (answer is already whitespace-collapsed).
    wrong_index = None
    wrong_word = v.wrong_word.strip(_EDGE_PUNCT).lower() if v.wrong_word else ""
    if wrong_word:
        for i, tok in enumerate(answer.split()):
            if tok.strip(_EDGE_PUNCT).lower() == wrong_word:
                wrong_index = i
                break
    return {
        "correct": False,
        "kind": "gender",
        "expected": v.corrected or f"{item['article']} {item['noun']}",
        "article": item["article"],
        "note": v.note if v.note and v.note != "ok" else None,
        "wrongIndex": wrong_index,
    }


class AttemptIn(BaseModel):
    item_id: str
    # "article" = the drag (beat 1), "phrase" = the typed production (beat 2).
    phase: Literal["article", "phrase"]
    answer: str
    # OBS-007 practice-sitting id — same contract as the sibling drills.
    session_id: str | None = Field(None, max_length=64)
    # FLOW-002: the deliberate "give up" escape (Flow mode only, phase
    # "article" — that's the only beat Flow deals). Skips validation and
    # grades as a real, distinguishable miss.
    give_up: bool = False


async def _resolve_item(
    db: AsyncSession, user_id: str, item_id: str
) -> dict | None:
    """Catalog items by id; deck items re-derived from the card — scoped to
    the caller so one user can't probe another's deck by id.

    GEN-001b: also pulls the user's adjective cards in the same query so the
    re-derived item's ``adj_pool`` matches what ``get_round`` served — round
    and attempt must agree bit-for-bit for grading to stay deterministic."""
    if item_id.startswith("deck-"):
        rows = (
            await db.execute(
                select(VocabCard)
                .join(UserCard, UserCard.card_id == VocabCard.id)
                .where(
                    UserCard.user_id == user_id,
                    or_(
                        VocabCard.id == item_id[len("deck-"):],
                        VocabCard.type == "adjective",
                    ),
                )
            )
        ).scalars().all()
        card = next(
            (c for c in rows if c.id == item_id[len("deck-"):]), None
        )
        if card is None:
            return None
        return _deck_item(card, _safe_deck_adjectives(rows))
    return load_items().get(item_id)


@router.post("/attempts")
async def submit_attempt(
    body: AttemptIn,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Grade one beat deterministically, log it, return the verdict (+ the
    anchor scene — only now: shipping it with the round would answer beat 1)."""
    if not drill_try_admit(user_id):
        raise HTTPException(
            status_code=429,
            detail="You're going very fast — take a short break and try again in a few minutes.",
        )
    item = await _resolve_item(db, user_id, body.item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Unknown item.")
    answer = " ".join(body.answer.split())
    # give_up skips validation entirely — there's no drag/answer to check,
    # the learner is conceding the item.
    if not body.give_up:
        if not answer:
            raise HTTPException(status_code=422, detail="Type your answer first.")
        if len(answer) > _MAX_ANSWER_CHARS:
            raise HTTPException(
                status_code=422,
                detail="Keep it under 140 characters — one sentence is plenty.",
            )

    rule = load_rules().get(item["rule"]) if item.get("rule") else None

    with propagate_trace_context(user_id=user_id, session_id=body.session_id), tracer.start_as_current_span("genus-attempt") as attempt_span:
        attempt_span.set_attribute("user.id", user_id)
        attempt_span.set_attribute("item_id", item["id"])
        attempt_span.set_attribute("phase", body.phase)
        if body.session_id:
            attempt_span.set_attribute("langfuse.session.id", body.session_id)
        attempt_span.set_attribute("langfuse.observation.input", answer)

        if body.phase == "article":
            if body.give_up:
                # FLOW-002 escape hatch: no drag was made — reveal the drop
                # through the exact same reveal fields a CORRECT drop gets
                # below, just with correct forced False so scoring/tally
                # treat it as a miss. The frontend renders it via the
                # existing anchor-card path, no new branch needed there.
                attempt_span.set_attribute("gave_up", True)
                correct = False
            else:
                choice = answer.lower()
                if choice not in ARTICLES:
                    raise HTTPException(status_code=422, detail="Drop der, die, or das.")
                correct = choice == item["article"]

            if not correct and not body.give_up:
                # No reveal on a genuine wrong drop — the learner retries.
                # But when a trap word just caught them with its fake
                # ending, say so: noticing the lie is the teachable moment.
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
                    "correct": correct,
                    "article": item["article"],
                    "segment": None,
                    "anchor": None,
                    "reliability": None,
                    "trap": True,
                    "note": why,
                    # Absent (falsy) on a real correct drop; True only on a
                    # give-up — the frontend's only cue that this reveal is a
                    # concession, since genus has no "wrong" reveal to reuse.
                    "gaveUp": body.give_up,
                }
            elif rule is not None:
                surface = _match_surface(item["noun"], rule, require_stem=False)
                payload = {
                    "correct": correct,
                    "article": item["article"],
                    "segment": (
                        {"kind": rule["kind"], "text": surface} if surface else None
                    ),
                    "anchor": rule["anchor"],
                    "reliability": rule["reliability"],
                    "trap": False,
                    "note": None,
                    "gaveUp": body.give_up,
                }
            else:
                payload = {
                    "correct": correct,
                    "article": item["article"],
                    "segment": None,
                    "anchor": None,
                    "reliability": None,
                    "trap": False,
                    # The anchor card's header already says "Kein Muster".
                    "note": f"Just remember: {item['article']} {item['noun']}.",
                    "gaveUp": body.give_up,
                }
        else:  # phase == "phrase"
            payload = _grade_phrase(item, answer)
            if payload is not None:
                # Deterministic outcome — flag it so Langfuse can tell
                # "judged" apart from "matched without a judge call".
                attempt_span.set_attribute("judge_skipped", True)
            else:
                try:
                    verdict = await judge_gender(item, answer)
                except Exception as exc:
                    attempt_span.record_exception(exc)
                    logger.exception("Genus judge call failed (item {})", item["id"])
                    raise HTTPException(
                        status_code=502,
                        detail=(
                            "The judge is unavailable right now — "
                            "try again in a moment."
                        ),
                    )
                payload = _judge_payload(item, answer, verdict)

        attempt_span.set_attribute(
            "langfuse.observation.output",
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
                # ":giveup" suffix marks a concession in DATA-004 without a
                # new column — extends the existing phase-suffix convention.
                item_ref=(
                    f"{item['id']}:{body.phase}:giveup"
                    if body.give_up
                    else f"{item['id']}:{body.phase}"
                ),
                pattern_id=None,
                correct=payload["correct"],
                modality="written",
                session_id=body.session_id,
            )
        except Exception:
            logger.exception("Drill-attempt log write failed (item {})", item["id"])

        return payload


class NudgeIn(BaseModel):
    item_id: str
    # OBS-007 practice-sitting id — groups the nudge trace with the attempts.
    session_id: str | None = Field(None, max_length=64)


@router.post("/nudge")
async def vocab_nudge(
    body: NudgeIn,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """The retrieval cue for the production beat: which of the learner's OWN
    deck words would fit a sentence about this noun. The frontend shows only
    the count as a pill; clicking reveals ``words`` (the graduated hint).

    Decorative by contract — every failure path (empty deck, judge outage,
    hallucinated picks) returns ``{"words": []}``, never an error the drill
    would have to handle. Nothing here is an attempt: no DATA-004 row."""
    # The shared drill budget covers this route too (it makes a judge call),
    # but the decorative contract above outranks the 429: a throttled learner
    # gets no pill, never an error the drill would have to handle.
    if not drill_try_admit(user_id):
        return {"words": []}
    item = await _resolve_item(db, user_id, body.item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Unknown item.")

    noun_l = item["noun"].lower()
    deck = await load_deck(db, user_id, exclude=frozenset({noun_l}))
    if not deck:
        return {"words": []}

    with propagate_trace_context(user_id=user_id, session_id=body.session_id), tracer.start_as_current_span("genus-nudge") as span:
        span.set_attribute("user.id", user_id)
        span.set_attribute("item_id", item["id"])
        if body.session_id:
            span.set_attribute("langfuse.session.id", body.session_id)
        span.set_attribute("langfuse.observation.input", item["noun"])
        span.set_attribute("deck_size", len(deck))
        try:
            pick = await suggest_vocab(item, list(deck.values()))
        except Exception as exc:
            span.record_exception(exc)
            logger.warning("Genus nudge judge failed (item {}) — serving none", item["id"])
            return {"words": []}

        words = filter_picks(pick, deck, exclude=frozenset({noun_l}))
        span.set_attribute(
            "langfuse.observation.output",
            ", ".join(w["word"] for w in words) or "(none)",
        )
        return {"words": words}
