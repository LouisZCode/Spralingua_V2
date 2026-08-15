"""Shared brain of the per-drill vocab nudges (Artikel-Anker's ``genus``,
Sprechen's ``sprechen``, and any drill that follows).

The nudge is always the same shape wherever it appears: one judge call
matches the learner's personal vocabulary against whatever they're about to
produce and picks up to three words that would fit naturally. The frontend
shows only the COUNT — the learner rummages through their own memory first;
clicking reveals the words and a graduated-hint sentence each.

Purely decorative by contract: any failure here must resolve to an empty
pick so the drill proceeds untouched. This module owns the deck query, the
prompt, the LLM call, and the hallucination guard; each drill module builds
its own one-line ``activity`` description and owns its own route.
"""

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agents.observability import (
    generation_span,
    record_generation_output,
    unwrap_structured_output,
)
from agents.openrouter_llm import structured_judge_llm
from database.orm import UserCard, VocabCard

NUDGE_MODEL = "openai/gpt-oss-120b"

# One prompt holds the whole deck — decks are tens of words today; the cap
# is only a token-bound guard for pathological sizes.
DECK_CAP = 120


class NudgeWord(BaseModel):
    """One deck word the learner could weave into their production."""

    word: str = Field(
        description="The vocabulary entry copied EXACTLY as listed, no article"
    )
    hint: str = Field(
        description=(
            "One short simple German sentence (max 10 words) showing this "
            "word in use, exactly as the hint rule in the prompt requires"
        )
    )


class VocabNudge(BaseModel):
    """Up to three deck words that fit the learner's upcoming production."""

    words: list[NudgeWord] = Field(
        description="0-3 picks; empty when nothing on the list truly fits"
    )


PROMPT = """# Role
A German learner is about to {activity}. Below is their personal vocabulary — words they are currently learning. Pick the ones they could naturally weave into what they produce.

# Rules
- Pick AT MOST 3 words, only ones that fit a natural, simple A1-B1 production of this task. Fewer is fine; an empty list is fine when nothing truly fits.
- Never pick a word that is not on the list{never_pick}.
- word: copy the entry exactly as listed (without any article).
- hint: one short, correct, simple German sentence (max 10 words) that uses {hint_rule}.

# Their vocabulary
{deck}
"""


async def suggest_vocab(
    activity: str,
    deck: list[tuple[str, str | None, str]],
    *,
    span_name: str,
    focus: str | None = None,
) -> VocabNudge:
    """One structured-output pick over the learner's deck.

    ``deck`` entries are ``(target, article, gloss)`` — article only for
    nouns, shown so the judge sees natural German entries. ``activity`` is
    the one-line task description each drill supplies; ``focus`` — when the
    drill centers on one specific word (e.g. Artikel-Anker's target noun) —
    keeps that word out of the picks and forces the hint sentence to use it
    alongside the pick, so the graduated hint teaches the target too."""
    lines = []
    for target, article, gloss in deck[:DECK_CAP]:
        shown = f"{article} {target}" if article else target
        lines.append(f"- {shown} ({gloss})" if gloss else f"- {shown}")
    never_pick = f', and never pick "{focus}" itself' if focus else ""
    hint_rule = (
        f'BOTH the picked word and "{focus}"'
        if focus
        else "the picked word and fits the task"
    )
    prompt = (
        PROMPT.replace("{activity}", activity)
        .replace("{never_pick}", never_pick)
        .replace("{hint_rule}", hint_rule)
        .replace("{deck}", "\n".join(lines))
    )
    # JUDGE-001 (2026-08-15): picks a nudge word and phrases the hint, not a
    # verdict — temperature=None keeps provider-default sampling so the
    # phrasing (and pick, among ties) varies across nudges.
    llm = structured_judge_llm(VocabNudge, temperature=None)
    # OBS-007: the nudge span is the child of the route's own trace.
    with generation_span(span_name, model=NUDGE_MODEL, input_text=prompt) as span:
        result, usage, response_metadata = unwrap_structured_output(
            await llm.ainvoke(prompt)
        )
        record_generation_output(span, result.model_dump_json(), usage, response_metadata)
    return result


async def load_deck(
    db: AsyncSession, user_id: str, *, exclude: frozenset[str] = frozenset()
) -> dict[str, tuple[str, str | None, str]]:
    """The learner's whole deck, all card types — verbs and phrases are
    prime sentence material, not just nouns. Past-tense siblings are the
    same word again; the base card (``tense IS NULL``) suffices. Keyed by
    lowercased target so a hallucinated pick can be checked in O(1)."""
    cards = (
        await db.execute(
            select(VocabCard)
            .join(UserCard, UserCard.card_id == VocabCard.id)
            .where(UserCard.user_id == user_id, VocabCard.tense.is_(None))
        )
    ).scalars().all()
    deck: dict[str, tuple[str, str | None, str]] = {}
    for c in cards:
        target = (c.target or "").strip()
        target_l = target.lower()
        if target and target_l not in exclude:
            deck[target_l] = (target, c.article, c.gloss or "")
    return deck


_ARTICLES = frozenset({"der", "die", "das"})


def filter_picks(
    pick: VocabNudge,
    deck: dict[str, tuple[str, str | None, str]],
    *,
    exclude: frozenset[str] = frozenset(),
) -> list[dict]:
    """Hallucination guard: only words that really are on the deck survive,
    matched with or without a leading article, deduped, capped at 3. Nouns
    get their article back in the payload — that's how the learner should
    re-encounter them."""
    words: list[dict] = []
    seen: set[str] = set()
    for w in pick.words:
        key = w.word.strip().lower()
        parts = key.split()
        if key not in deck and len(parts) > 1 and parts[0] in _ARTICLES:
            key = " ".join(parts[1:])
        entry = deck.get(key)
        if entry is None or key in seen or key in exclude:
            continue
        seen.add(key)
        target, article, _ = entry
        words.append(
            {
                "word": f"{article} {target}" if article else target,
                "hint": w.hint.strip(),
            }
        )
        if len(words) == 3:
            break
    return words
