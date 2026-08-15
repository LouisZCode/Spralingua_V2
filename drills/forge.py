"""CONT-002 — forge personal drill items from the learner's own vocabulary.

The user ruled (2026-07-19) that Satzschmiede deck words should feed the
grammar drills — deck words are by definition still being LEARNED, so the
old "familiar words let you dodge the ending" caveat doesn't apply to them. Each
round is a 50/50 mix: half shared YAML catalog, half items forged here.

Two forgers, one guardrail philosophy — a learner must never be taught a wrong
gold answer:

* **Bauteil** (nouns with an article): the LLM only CHOOSES — an adjective
  from a concat-safe list, a case, an article type, a frame. The inflected
  answer is built DETERMINISTICALLY from declension tables below, so the gold
  ending cannot be wrong. The tables guarantee grammar, not sense — a
  ``has_fit`` gate lets the LLM tombstone nouns no listed adjective pairs
  with naturally (abstract/meta words: the live example was "Ich spreche mit
  dem wichtigen Geschlecht").
* **Verbindungen** (base verbs): fixed verb+preposition chunks aren't
  table-derivable, so a second verify LLM pass gates every drafted item; a
  verb with no standard chunk yields a tombstone (NULL item) and is never
  retried.

Runs strictly off the request path: fired via ``asyncio.create_task`` from
the add-word route and the round endpoints' lazy backfill.
"""

from typing import Literal, Optional
from uuid import uuid4

from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy import and_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from agents.observability import (
    generation_span,
    record_generation_output,
    unwrap_structured_output,
)
from agents.openrouter_llm import structured_judge_llm
from database.connection import get_sessionmaker
from database.orm import UserCard, UserDrillItem, VocabCard

FORGE_MODEL = "openai/gpt-oss-120b"

_GENDER_BY_ARTICLE = {"der": "m", "die": "f", "das": "n"}


# ── Declension tables (Bauteil) ──────────────────────────────────────────
# Adjectives whose declined forms are pure stem+ending (no elision like
# teuer→teure or dunkel→dunkle) — the LLM must pick from these, and the
# builder enforces it.
SAFE_ADJECTIVES = {
    "neu", "alt", "klein", "groß", "gut", "schön", "warm", "kalt", "frisch",
    "lang", "kurz", "schnell", "langsam", "billig", "ruhig", "laut", "leicht",
    "schwer", "voll", "leer", "sauber", "modern", "bequem", "wichtig",
    "lecker", "gesund", "stark", "nett", "freundlich", "bunt",
}

_CASES = ("nominative", "accusative", "dative")

# Definite-article forms by (gender, case).
_DEF_ART = {
    ("m", "nominative"): "der", ("f", "nominative"): "die", ("n", "nominative"): "das",
    ("m", "accusative"): "den", ("f", "accusative"): "die", ("n", "accusative"): "das",
    ("m", "dative"): "dem", ("f", "dative"): "der", ("n", "dative"): "dem",
}

# ein-type endings by (gender, case) — applies to ein/kein/mein/dein/sein/ihr;
# unser/euer have their own full forms below (euer elides: eure/eurem/...).
_EIN_END = {
    ("m", "nominative"): "", ("f", "nominative"): "e", ("n", "nominative"): "",
    ("m", "accusative"): "en", ("f", "accusative"): "e", ("n", "accusative"): "",
    ("m", "dative"): "em", ("f", "dative"): "er", ("n", "dative"): "em",
}
_UNSER_EUER = {
    ("unser", "m", "nominative"): "unser", ("unser", "f", "nominative"): "unsere", ("unser", "n", "nominative"): "unser",
    ("unser", "m", "accusative"): "unseren", ("unser", "f", "accusative"): "unsere", ("unser", "n", "accusative"): "unser",
    ("unser", "m", "dative"): "unserem", ("unser", "f", "dative"): "unserer", ("unser", "n", "dative"): "unserem",
    ("euer", "m", "nominative"): "euer", ("euer", "f", "nominative"): "eure", ("euer", "n", "nominative"): "euer",
    ("euer", "m", "accusative"): "euren", ("euer", "f", "accusative"): "eure", ("euer", "n", "accusative"): "euer",
    ("euer", "m", "dative"): "eurem", ("euer", "f", "dative"): "eurer", ("euer", "n", "dative"): "eurem",
}

# Adjective endings: weak (after definite), mixed (after ein-type), strong (bare).
_ADJ_WEAK = {
    ("m", "nominative"): "e", ("f", "nominative"): "e", ("n", "nominative"): "e",
    ("m", "accusative"): "en", ("f", "accusative"): "e", ("n", "accusative"): "e",
    ("m", "dative"): "en", ("f", "dative"): "en", ("n", "dative"): "en",
}
_ADJ_MIXED = {
    ("m", "nominative"): "er", ("f", "nominative"): "e", ("n", "nominative"): "es",
    ("m", "accusative"): "en", ("f", "accusative"): "e", ("n", "accusative"): "es",
    ("m", "dative"): "en", ("f", "dative"): "en", ("n", "dative"): "en",
}
_ADJ_STRONG = {
    ("m", "nominative"): "er", ("f", "nominative"): "e", ("n", "nominative"): "es",
    ("m", "accusative"): "en", ("f", "accusative"): "e", ("n", "accusative"): "es",
    ("m", "dative"): "em", ("f", "dative"): "er", ("n", "dative"): "em",
}


def build_bauteil_answer(
    article_type: str, gender: str, case: str, adjective: str, noun: str
) -> tuple[str, list[str]]:
    """Deterministically build the inflected gold answer + the raw ``parts``
    the learner sees, from the LLM's four choices (article_type, gender is
    derived from the card, case, adjective) plus the noun itself. The LLM
    never inflects anything — every ending comes straight from the tables
    above — so the gold answer this function returns cannot be wrong the way
    a model's own inflection sometimes is.

    Returns ``(answer, parts)``. ``answer`` is lowercase-first even when the
    article/adjective would normally be capitalized (mid-sentence use) — the
    caller capitalizes the first character when the frame's gap is sentence-
    initial, matching the catalog's own convention.
    """
    if article_type == "definite":
        art_word = _DEF_ART[(gender, case)]
        adj_ending = _ADJ_WEAK[(gender, case)]
        parts = [_DEF_ART[(gender, "nominative")], adjective, noun]
        answer = f"{art_word} {adjective}{adj_ending} {noun}"
    elif article_type in ("ein", "kein", "mein", "dein", "sein", "ihr"):
        art_word = article_type + _EIN_END[(gender, case)]
        adj_ending = _ADJ_MIXED[(gender, case)]
        parts = [article_type, adjective, noun]
        answer = f"{art_word} {adjective}{adj_ending} {noun}"
    elif article_type in ("unser", "euer"):
        art_word = _UNSER_EUER[(article_type, gender, case)]
        adj_ending = _ADJ_MIXED[(gender, case)]
        parts = [article_type, adjective, noun]
        answer = f"{art_word} {adjective}{adj_ending} {noun}"
    elif article_type == "bare":
        adj_ending = _ADJ_STRONG[(gender, case)]
        parts = [adjective, noun]
        answer = f"{adjective}{adj_ending} {noun}"
    else:
        raise ValueError(f"unknown article_type {article_type!r}")
    return answer, parts


# ── Pydantic schemas (LLM choices only — descriptions kept SHORT: they count
# toward Cerebras' 5,000-char strict-schema budget, see openrouter_llm.py) ──


class BauteilForgeDraft(BaseModel):
    """LLM choices for one declension item — the answer is built in code."""
    has_fit: bool = Field(description="True iff at least one listed adjective pairs naturally with this noun in an everyday sentence")
    adjective: str = Field(description="One adjective from the allowed list, base form")
    case: Literal["nominative", "accusative", "dative"] = Field(description="The case the frame forces")
    article_type: Literal["definite", "ein", "kein", "mein", "dein", "sein", "ihr", "unser", "euer", "bare"] = Field(description="Article slot of the phrase")
    frame: str = Field(description="German sentence with exactly one ___ gap where the phrase goes; the rest of the sentence must force the chosen case")
    hint: str = Field(description="Natural English rendering of the full sentence")


class VerbindungenForgeDraft(BaseModel):
    """LLM draft of one fixed verb-preposition chunk item."""
    has_chunk: bool = Field(description="True iff this verb has one standard fixed preposition pairing common at A2-B1")
    reflexive: bool = Field(description="True iff the chunk uses the verb reflexively")
    preposition: str = Field(description="The fixed preposition, lowercase")
    case: Literal["Akkusativ", "Dativ"] = Field(description="Case the preposition governs in this chunk")
    frame: str = Field(description="German sentence with exactly one ___ gap over the chunk region (pronoun+preposition for reflexive verbs, else the preposition, optionally + article)")
    answer: str = Field(description="The words that fill the gap")
    hint: str = Field(description="Natural English rendering of the full sentence")


class ChunkVerify(BaseModel):
    """Second-pass gate on a drafted chunk item."""
    correct: bool = Field(description="True iff the verb-preposition-case pairing is standard German AND the filled sentence is fully grammatical and natural")
    reason: str = Field(description="One short line naming the problem when correct=false, else 'ok'")


# ── Forge prompts ─────────────────────────────────────────────────────────

BAUTEIL_PROMPT = """# Role
You choose the ingredients for one German declension drill item ("Bauteil-Sätze"). The learner will see a sentence with a gap and the raw, uninflected parts of a phrase (article + adjective + noun), and has to type the phrase correctly inflected. The noun below comes from the learner's own vocabulary — you do NOT inflect anything; you only CHOOSE an adjective, a case, an article type, and a frame sentence. A separate deterministic step builds the inflected answer from your choices, so pick carefully and correctly.

# The noun
- noun: {noun}
- its article (gender marker): {article}
- meaning: {gloss}

# First — has_fit
Decide honestly: does at least ONE adjective from the list below make a natural, everyday phrase with "{noun}" — something a German speaker would actually say? Abstract or grammar-flavoured nouns often have no natural pairing here, and a grammatically perfect but absurd sentence ("Ich spreche mit dem wichtigen Geschlecht") must never reach a learner. If none fits, set has_fit=false and fill the other fields with any reasonable placeholder — the item will be discarded.

# Your choices
1. `adjective` — pick ONE adjective from this exact list that plausibly fits the noun's MEANING (never invent one, never pick a clashing one):
{adjectives}
2. `case` — nominative | accusative | dative.
3. `article_type` — one of: definite | ein | kein | mein | dein | sein | ihr | unser | euer | bare. (definite = der/die/das family; ein-type = ein/kein/mein/dein/sein/ihr/unser/euer; bare = no article, the adjective takes the strong ending.)
4. `frame` — ONE simple A2 everyday German sentence about the noun's world, with exactly one `___` gap where the whole article+adjective+noun phrase goes, such that the REST of the sentence unambiguously forces the case you chose:
   - dative → a dative preposition (mit, bei, von, unter, nach, zu, aus, seit) or a dative verb (helfen, gefallen, gehören, danken) governing the gap.
   - accusative → a clearly transitive verb whose direct object is the gap (ich nehme/kaufe/sehe/suche ___, …).
   - nominative → the gap sits in the subject slot, before the verb.
5. `hint` — a natural, comprehension-only English rendering of the full sentence (gap filled in your head).

# Hard rules
- "{noun}" must NOT appear anywhere else in the frame — only in the gap.
- Singular only — no plural nouns or plural agreement.
- Exactly one `___` in the frame.
- The frame must read as a natural, simple sentence a real learner would see, not a grammar-drill fragment.
"""

VERBINDUNGEN_PROMPT = """# Role
You judge whether one German verb has a standard, dictionary-listed fixed preposition pairing, and if so draft one drill sentence for it. This is for "Feste Verbindungen" — completing the fixed verb chunk (reflexive pronoun if any + preposition + the case it governs).

# The verb
- verb: {verb}
- reflexive in its base dictionary sense: {reflexive}
- meaning: {gloss}

# Step 1 — has_chunk
Decide honestly: does this verb have ONE standard, dictionary-listed fixed preposition pairing common at A2-B1 (e.g. sich freuen auf, warten auf, hoffen auf, sich interessieren für, denken an, sich erinnern an)? If not, set has_chunk=false and fill the other fields with any reasonable placeholder — the item will be discarded.

# Step 2 — when has_chunk=true
- `reflexive` — true iff the chunk uses the verb WITH a reflexive pronoun (sich freuen auf → true; warten auf → false).
- `preposition` — the fixed preposition, lowercase.
- `case` — Akkusativ | Dativ, whichever this preposition governs IN THIS CHUNK.
- `frame` — ONE simple A2-B1 sentence using the verb, with exactly one `___` gap spanning ONLY the chunk words: for a reflexive chunk, the gap is pronoun+preposition (e.g. "Ich freue ___ das Wochenende." → gap "mich auf"); for a non-reflexive chunk, the gap is the preposition alone, or preposition + article if the case demands one.
- `answer` — exactly the words that fill the gap, nothing more.
- `hint` — a natural English rendering of the full sentence.

# Hard rules
- Exactly one `___` in the frame.
- The answer must slot in perfectly — the filled sentence must read as completely natural German.
- The frame and answer must not overlap: the words that fill the gap appear ONLY in the answer, never also in the frame — re-read the filled sentence before returning.
- Only claim has_chunk=true for pairings a German dictionary would actually list, not just any verb that CAN take a preposition.
- When preposition + article fuse, write the CONTRACTION a German would actually write: "beim Umzug", not "bei dem Umzug"; likewise am/ans, im/ins, zum/zur, vom, aufs, fürs. Spelling the fusion out makes the answer read as textbook German the learner is then graded against. (Both spellings are accepted at grading time, so this is about the item reading naturally — see grammar/contractions.py.)
"""

VERIFY_PROMPT = """# Role
You are a strict second-pass check on one drafted German verb-chunk drill item ("Feste Verbindungen"). Verify it before it ever reaches a learner.

# The chunk being tested
{chunk}

# The filled sentence (frame with the gap filled in)
{sentence}

# What to check
- Is the verb-preposition(-case) pairing STANDARD, dictionary-listed German — not merely grammatically possible?
- Is the filled sentence fully grammatical and natural — something a native speaker would actually say?

Set `correct=true` only if BOTH hold. Otherwise `correct=false` and name the problem in one short line in `reason` (else "ok").
"""


async def _forge_bauteil_item(row_id: str, card: VocabCard, user_id: str) -> Optional[dict]:
    """One LLM choice call + a deterministic build. Returns ``None`` (never
    raises past its own guard clauses) when the draft fails a sanity check —
    the caller inserts that as a tombstone."""
    gender = _GENDER_BY_ARTICLE.get(card.article)
    if gender is None:
        return None

    llm = structured_judge_llm(BauteilForgeDraft)
    prompt = (
        BAUTEIL_PROMPT.replace("{noun}", card.target)
        .replace("{article}", card.article)
        .replace("{gloss}", card.gloss or "")
        .replace("{adjectives}", ", ".join(sorted(SAFE_ADJECTIVES)))
    )
    with generation_span(
        "bauteil-forge", model=FORGE_MODEL, input_text=prompt, user_id=user_id
    ) as span:
        result, usage, response_metadata = unwrap_structured_output(await llm.ainvoke(prompt))
        record_generation_output(span, result.model_dump_json(), usage, response_metadata)

    if not result.has_fit:
        logger.info(
            "Bauteil forge: no natural adjective fit for {!r} — tombstoned",
            card.target,
        )
        return None
    if result.adjective not in SAFE_ADJECTIVES:
        logger.warning(
            "Bauteil forge picked an unsafe adjective {!r} for {!r}",
            result.adjective, card.target,
        )
        return None
    if result.case not in _CASES:
        logger.warning("Bauteil forge picked an invalid case for {!r}", card.target)
        return None
    if result.frame.count("___") != 1:
        logger.warning("Bauteil forge frame missing exactly one gap for {!r}", card.target)
        return None
    if card.target.lower() in result.frame.lower():
        logger.warning("Bauteil forge frame leaked the noun {!r}", card.target)
        return None

    try:
        answer, parts = build_bauteil_answer(
            result.article_type, gender, result.case, result.adjective, card.target
        )
    except (KeyError, ValueError):
        logger.exception("Bauteil forge answer build failed for {!r}", card.target)
        return None

    frame = result.frame
    if frame.startswith("___") and answer:
        answer = answer[0].upper() + answer[1:]

    item: dict = {
        "id": row_id,
        "pattern_id": "adjektivendungen",
        "case": result.case,
        "gender": gender,
        "parts": parts,
        "frame": frame,
        "answer": answer,
        "hint": result.hint,
    }
    if result.article_type != "bare":
        item["article"] = parts[0]
    return item


async def _forge_verbindungen_item(row_id: str, card: VocabCard, user_id: str) -> Optional[dict]:
    """Draft + mechanical checks + a second verify LLM pass. Fixed verb
    chunks aren't table-derivable like Bauteil's endings, so this is the
    guardrail that keeps a wrong gold answer from ever reaching a learner.

    At most TWO attempts: one flaky draft (live-test example: 'hoffen', where
    the frame already carried the article the answer repeated, and verify
    rightly rejected the doubled article) must not permanently tombstone a
    verb that has a real chunk — a rejected first draft gets one fresh
    draft+verify before the tombstone lands. ``has_chunk=false`` is not a
    flake, it's the honest answer — that tombstones immediately."""
    prompt = (
        VERBINDUNGEN_PROMPT.replace("{verb}", card.target)
        .replace("{reflexive}", "yes" if card.reflexive else "no")
        .replace("{gloss}", card.gloss or "")
    )

    for attempt in (1, 2):
        retrying = " — retrying with a fresh draft" if attempt == 1 else ""

        llm = structured_judge_llm(VerbindungenForgeDraft)
        with generation_span(
            "verbindungen-forge", model=FORGE_MODEL, input_text=prompt, user_id=user_id
        ) as span:
            result, usage, response_metadata = unwrap_structured_output(await llm.ainvoke(prompt))
            record_generation_output(span, result.model_dump_json(), usage, response_metadata)

        if not result.has_chunk:
            return None
        if result.frame.count("___") != 1:
            logger.warning(
                "Verbindungen forge frame missing exactly one gap for {!r}{}",
                card.target, retrying,
            )
            continue
        answer = " ".join(result.answer.split())
        if not answer or result.preposition.lower() not in answer.lower():
            logger.warning(
                "Verbindungen forge answer missing the preposition for {!r}{}",
                card.target, retrying,
            )
            continue
        if result.reflexive and not any(
            p in answer.lower().split() for p in ("mich", "dich", "sich", "uns", "euch")
        ):
            logger.warning(
                "Verbindungen forge reflexive answer missing a pronoun for {!r}{}",
                card.target, retrying,
            )
            continue

        chunk = f"{'sich ' if result.reflexive else ''}{card.target} {result.preposition} + {result.case}"
        sentence = result.frame.replace("___", answer)

        verify_llm = structured_judge_llm(ChunkVerify)
        verify_prompt = VERIFY_PROMPT.replace("{chunk}", chunk).replace("{sentence}", sentence)
        with generation_span(
            "verbindungen-forge-verify",
            model=FORGE_MODEL,
            input_text=verify_prompt,
            user_id=user_id,
        ) as span:
            vresult, vusage, vmeta = unwrap_structured_output(await verify_llm.ainvoke(verify_prompt))
            record_generation_output(span, vresult.model_dump_json(), vusage, vmeta)

        if not vresult.correct:
            logger.warning(
                "Verbindungen forge verify rejected {!r}: {}{}",
                card.target, vresult.reason, retrying,
            )
            continue

        return {
            "id": row_id,
            "pattern_id": "reflexivpronomen" if result.reflexive else "verben-mit-praepositionen",
            "frame": result.frame,
            "answer": answer,
            "chunk": chunk,
            "hint": result.hint,
        }

    return None


async def forge_items_for_card(user_id: str, card_id: str) -> None:
    """Background entry point (fired via ``asyncio.create_task`` — never
    awaited on the request path): forge this user's personal drill item from
    one of their own Satzschmiede cards. Opens its own DB session. Never
    raises — a forge failure must never surface anywhere the caller can see."""
    try:
        sessionmaker = get_sessionmaker()
        async with sessionmaker() as db:
            card = await db.get(VocabCard, card_id)
            if card is None:
                return

            if card.type == "noun" and card.article:
                exercise = "bauteil"
            elif card.type == "verb" and card.tense is None:
                exercise = "verbindungen"
            else:
                # Other card types (phrases, adjectives, past-tense siblings,
                # …) are out of scope for CONT-002 — no row at all, not even
                # a tombstone; there is nothing here to ever retry.
                return

            existing = await db.scalar(
                select(UserDrillItem).where(
                    UserDrillItem.user_id == user_id,
                    UserDrillItem.exercise == exercise,
                    UserDrillItem.source_card_id == card_id,
                )
            )
            if existing is not None:
                return

            row_id = f"udi-{uuid4().hex[:12]}"
            if exercise == "bauteil":
                item = await _forge_bauteil_item(row_id, card, user_id)
            else:
                item = await _forge_verbindungen_item(row_id, card, user_id)

            await db.execute(
                pg_insert(UserDrillItem)
                .values(
                    id=row_id,
                    user_id=user_id,
                    exercise=exercise,
                    source_card_id=card_id,
                    item=item,
                )
                .on_conflict_do_nothing(
                    index_elements=["user_id", "exercise", "source_card_id"]
                )
            )
            await db.commit()
    except Exception:
        logger.exception(
            "Drill-item forge failed (user {}, card {})", user_id, card_id
        )


async def backfill_missing(user_id: str, exercise: str, limit: int = 3) -> None:
    """Lazy backfill fired from the round endpoints (``asyncio.create_task``,
    never awaited): finds up to ``limit`` of the user's linked cards eligible
    for ``exercise`` with no ``user_drill_items`` row yet, and forges them
    one at a time. Covers words added before the forge existed, or during a
    forge outage. Fully guarded — a backfill failure must never surface to
    the round it rode in on."""
    try:
        if exercise == "bauteil":
            type_filter = and_(VocabCard.type == "noun", VocabCard.article.is_not(None))
        elif exercise == "verbindungen":
            type_filter = and_(VocabCard.type == "verb", VocabCard.tense.is_(None))
        else:
            return

        sessionmaker = get_sessionmaker()
        async with sessionmaker() as db:
            already = select(UserDrillItem.source_card_id).where(
                UserDrillItem.user_id == user_id,
                UserDrillItem.exercise == exercise,
            )
            card_ids = (
                await db.execute(
                    select(VocabCard.id)
                    .join(UserCard, UserCard.card_id == VocabCard.id)
                    .where(
                        UserCard.user_id == user_id,
                        type_filter,
                        VocabCard.id.not_in(already),
                    )
                    .limit(limit)
                )
            ).scalars().all()

        for card_id in card_ids:
            await forge_items_for_card(user_id, card_id)
    except Exception:
        logger.exception(
            "Drill-item backfill failed (user {}, exercise {})", user_id, exercise
        )
