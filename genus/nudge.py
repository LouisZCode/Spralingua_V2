"""Artikel-Anker's vocab nudge — the genus-specific activity line over the
shared brain in ``vocab_nudge.py``. See that module for the prompt, deck
query, and hallucination guard; this one just describes what the learner is
about to write and keeps the target noun out of its own picks.
"""

import vocab_nudge
from vocab_nudge import NudgeWord, VocabNudge  # re-exported for callers/tests

__all__ = ["NudgeWord", "VocabNudge", "suggest_vocab"]


async def suggest_vocab(
    item: dict, deck: list[tuple[str, str | None, str]]
) -> VocabNudge:
    """One structured-output pick over the learner's deck for the free-text
    sentence beat, matched against ``item``'s target noun."""
    gloss = f' ("{item["gloss"]}")' if item.get("gloss") else ""
    activity = f'write ONE short sentence containing the noun "{item["article"]} {item["noun"]}"{gloss}'
    return await vocab_nudge.suggest_vocab(
        activity, deck, span_name="genus-nudge", focus=item["noun"]
    )
