"""Cold-start starter topics for Clara (teacher, AGENT-00X follow-up).

``load_grammar_focus`` (``database/repository.py``) returns nothing for a
learner whose ``user_errors`` ledger is still empty — day one, or anyone who
has only talked to Lena/Paul so far. Without a fallback, Clara's topic
screen has no cards to show and her prompt's focus section is omitted
entirely, so she has no legal exercise id to deal from at all.

This module is the fallback: a curated map of three dealable pattern ids per
CEFR level bucket, picked from the 24 ids ``teacher/registry.py`` actually
covers. Labels and descriptions are never hardcoded here — they're read live
from ``grammar/loader.py::load_taxonomy()``, so a taxonomy edit (wording,
not id) never goes stale in this file.

Single source for both call sites: ``GET /teacher/starters`` (the topic
screen) and ``pipeline/factory.py``'s teacher branch (the prompt's seeded
focus section when the real ledger is empty).
"""

from grammar.levels import DEFAULT_BUCKET, bucket_of
from grammar.loader import load_taxonomy

# Three ids per bucket, matched to the level-bucket names in grammar/levels.py.
# B1 and B2+ share a map — the taxonomy tops out at B1, so a B2+ learner's
# starters are the same three patterns (grammar/levels.py's own ceiling rule:
# no pattern is above B2+, so nothing is out of reach).
_STARTER_MAP: dict[str, tuple[str, ...]] = {
    "A1": ("akkusativ-artikel", "sein-vs-haben", "nicht-vs-kein"),
    "A2": ("dativ-praepositionen", "wechselpraepositionen", "praeteritum-sein-haben-modal"),
    "B1": ("adjektivendungen", "relativsatz", "verben-mit-praepositionen"),
    "B2+": ("adjektivendungen", "relativsatz", "verben-mit-praepositionen"),
}


def starters_for_level(level: str | None) -> list[dict]:
    """Three curated starter patterns for ``level`` (A1 default, incl. NULL).

    Returns ``[{pattern_id, label, description, wrong, right, level}]`` —
    the same shape a caller can render as a focus card, or fold into a
    ``load_grammar_focus``-shaped entry (see ``pipeline/factory.py``).
    ``level`` on each entry is the bucket the starter was picked for, not
    the pattern's own taxonomy level (only relevant for B1 vs B2+, which
    share content). ``wrong``/``right`` (CLARA-19) are the taxonomy's
    minimal contrast pair, additive alongside ``description`` — existing
    consumers that fold this dict into prompt layers are unaffected.
    """
    bucket = bucket_of(level) or DEFAULT_BUCKET
    catalog = load_taxonomy()
    return [
        {
            "pattern_id": pattern_id,
            "label": catalog[pattern_id]["label"],
            "description": catalog[pattern_id]["description"],
            "wrong": catalog[pattern_id]["wrong"],  # CLARA-19
            "right": catalog[pattern_id]["right"],  # CLARA-19
            "level": bucket,
        }
        for pattern_id in _STARTER_MAP[bucket]
    ]
