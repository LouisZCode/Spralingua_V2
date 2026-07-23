"""Vocab nudge for Artikel-Anker's free-text beat (user decision 2026-07-23).

Deck words are shown TO the learner all over the app, but nothing ever asks
them to retrieve one on their own. This module powers the opt-in cue: when
the production beat opens, one judge call matches the learner's personal
vocabulary against the target noun and picks up to three words that would
fit naturally in a sentence about it. The frontend shows only the COUNT —
the learner rummages through their own memory first; clicking reveals the
words and a short example each (the graduated hint).

Purely decorative by contract: any failure here returns an empty pick and
the drill proceeds untouched. Same Cerebras ``gpt-oss-120b`` structured-
output wiring as ``genus/judge.py``.
"""

from pydantic import BaseModel, Field

from agents.observability import (
    generation_span,
    record_generation_output,
    unwrap_structured_output,
)
from agents.openrouter_llm import structured_judge_llm

NUDGE_MODEL = "openai/gpt-oss-120b"

# One prompt holds the whole deck — decks are tens of words today; the cap
# is only a token-bound guard for pathological sizes.
DECK_CAP = 120


class NudgeWord(BaseModel):
    """One deck word the learner could weave into their sentence."""

    word: str = Field(
        description="The vocabulary entry copied EXACTLY as listed, no article"
    )
    hint: str = Field(
        description=(
            "One short simple German sentence (max 10 words) using both "
            "this word and the target noun"
        )
    )


class VocabNudge(BaseModel):
    """Up to three deck words that fit the target noun's sentence."""

    words: list[NudgeWord] = Field(
        description="0-3 picks; empty when nothing on the list truly fits"
    )


PROMPT = """# Role
A German learner is about to write ONE short sentence containing the noun "{article} {noun}"{gloss}. Below is their personal vocabulary — words they are currently learning. Pick the ones they could naturally weave into such a sentence.

# Rules
- Pick AT MOST 3 words, only ones that fit a natural, simple A1-B1 sentence together with "{article} {noun}". Fewer is fine; an empty list is fine when nothing truly fits.
- Never pick a word that is not on the list, and never pick "{noun}" itself.
- word: copy the entry exactly as listed (without any article).
- hint: one short, correct, simple German sentence (max 10 words) that uses BOTH the picked word and "{noun}".

# Their vocabulary
{deck}
"""


async def suggest_vocab(
    item: dict, deck: list[tuple[str, str | None, str]]
) -> VocabNudge:
    """One structured-output pick over the learner's deck.

    ``deck`` entries are ``(target, article, gloss)`` — article only for
    nouns, shown so the judge sees natural German entries."""
    lines = []
    for target, article, gloss in deck[:DECK_CAP]:
        shown = f"{article} {target}" if article else target
        lines.append(f"- {shown} ({gloss})" if gloss else f"- {shown}")
    prompt = (
        PROMPT.replace("{article}", item["article"])
        .replace("{noun}", item["noun"])
        .replace("{gloss}", f' ("{item["gloss"]}")' if item.get("gloss") else "")
        .replace("{deck}", "\n".join(lines))
    )
    llm = structured_judge_llm(VocabNudge)
    # OBS-007: the `genus-nudge` child of the route's nudge trace.
    with generation_span("genus-nudge", model=NUDGE_MODEL, input_text=prompt) as span:
        result, usage, response_metadata = unwrap_structured_output(
            await llm.ainvoke(prompt)
        )
        record_generation_output(span, result.model_dump_json(), usage, response_metadata)
    return result
