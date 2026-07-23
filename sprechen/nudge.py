"""Sprechen's vocab nudge — which of the learner's deck words fit this
speaking task. Same retrieval-cue contract as Artikel-Anker's: shown
count-first before recording, decorative by construction. See
``vocab_nudge.py`` for the prompt, deck query, and hallucination guard.
"""

import vocab_nudge
from vocab_nudge import VocabNudge  # re-exported for callers

__all__ = ["VocabNudge", "suggest_vocab"]


async def suggest_vocab(task: dict, deck: list[tuple[str, str | None, str]]) -> VocabNudge:
    """One structured-output pick over the learner's deck for the given
    speaking task — no focus word, since the task has no single target."""
    # removesuffix: task prompts end in a period and the shared template adds
    # its own right after the activity line.
    activity = (
        f'speak a short answer (a few sentences) to this speaking task: '
        f'"{task["title"]}" — {task["prompt"].strip().removesuffix(".")}'
    )
    return await vocab_nudge.suggest_vocab(activity, deck, span_name="sprechen-nudge")
