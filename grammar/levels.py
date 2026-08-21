"""Learner level, and which items a learner at that level should be served
(LEVEL-001).

The problem this solves: a B1 learner was being handed A1 items — present-
tense verb endings, sein-vs-haben — because rounds were assembled without
any notion of where the learner is. Practice time spent on a decision they
made correctly two years ago is time not spent on the thing they actually
get wrong.

Four buckets, not six
----------------------
``A1`` · ``A2`` · ``B1`` · ``B2+``. CEFR has more rungs, but a self-declared
level is only accurate to about this resolution. B1 and B2+ are split even
though the grammar taxonomy tops out at B1 (33 patterns: 11 A1, 12 A2, 10
B1, zero B2) — CONTENT-leveled exercises (Briefkasten seeds, Szenario
question tiers) and judge calibration need the finer distinction. For
taxonomy-derived gating specifically, B2+ has no ceiling: no pattern maps to
it, so a B2+ learner with open ledger gaps drills exactly those, and one
with a clean ledger gets the whole pool back via ``select()``'s degrade
ladder — see ``BUCKETS``.

Where item levels come from
---------------------------
Not from the items. Every grammar-drill item already carries a
``pattern_id``, and ``taxonomy.yaml`` already carries a ``level`` per
pattern, so the level is *derived*. Verified 2026-08-14 across all 377
grammar-drill items: every one maps to a known pattern, and for the 120
items that ALSO carried a hand-written ``level`` field (faelle, satzbau)
the derived and hand-written values agreed 120/120. The taxonomy is
therefore the single source of truth, and the per-item ``level`` fields are
redundant (left in place — they are validated content, and removing them is
a separate cleanup).

Satzschmiede is not covered here: its cards are vocabulary, not grammar
patterns, and carry their own per-card CEFR level. Genus is not covered
either — noun gender is A1 foundational and stays relevant at every level.

The serving rule
----------------
``allows()`` implements: **the level sets the ceiling, the ledger sets the
floor.**

- Never serve an item ABOVE the learner's level. A B1 pattern handed to an
  A1 learner is not a stretch goal, it is noise.
- Serve an item BELOW the learner's level only when the ledger says they
  actually get that pattern wrong. This is the half that keeps the rule
  honest: "B1 learner" does not mean "has no A1 gaps", and the whole point
  of ``user_errors`` is that we know which gaps are real.
- Items AT the learner's level always pass.

A learner with no level set (every existing account, and anyone who skips
the question) is served everything, exactly as before — the rule is opt-in
and never silently narrows a round it was not asked to narrow.
"""

from grammar.loader import load_taxonomy

__all__ = [
    "BUCKETS",
    "DEFAULT_BUCKET",
    "allows",
    "bucket_of",
    "level_for_pattern",
    "rank",
    "select",
]

# Ordered easiest → hardest; the index IS the rank used for comparison.
BUCKETS: tuple[str, ...] = ("A1", "A2", "B1", "B2+")

DEFAULT_BUCKET = "A1"

# Taxonomy level -> bucket. B2 is mapped even though no pattern uses it
# today, so adding a B2 pattern later cannot raise a KeyError at serve time.
_TO_BUCKET = {"A1": "A1", "A2": "A2", "B1": "B1", "B2": "B2+"}


def bucket_of(level: str | None) -> str | None:
    """Normalize any CEFR-ish string to one of ``BUCKETS`` (None passes
    through, meaning "no level known — serve everything")."""
    if level is None:
        return None
    key = level.strip().upper().replace("+", "")
    if key in _TO_BUCKET:
        return _TO_BUCKET[key]
    return level.strip().upper() if level.strip().upper() in BUCKETS else None


def rank(bucket: str | None) -> int | None:
    """Position in ``BUCKETS``; None when the bucket is unknown."""
    b = bucket_of(bucket)
    return BUCKETS.index(b) if b in BUCKETS else None


def level_for_pattern(pattern_id: str) -> str | None:
    """The bucket an item testing ``pattern_id`` belongs to.

    None for a pattern outside the taxonomy — the caller then treats the
    item as unleveled and serves it, which is the safe direction: a missing
    level must never make an item unreachable.
    """
    pattern = load_taxonomy().get(pattern_id)
    if pattern is None:
        return None
    return _TO_BUCKET.get(pattern["level"])


def allows(user_level: str | None, pattern_id: str, weak_patterns: set[str] | None = None) -> bool:
    """Should a learner at ``user_level`` be served an item on ``pattern_id``?

    ``weak_patterns`` is the set of pattern ids the learner demonstrably
    gets wrong (from the ``user_errors`` ledger). A below-level pattern in
    that set is served; one absent from it is skipped as already known.

    Returns True whenever the level is unknown or the pattern is unleveled,
    so this can be dropped into a round assembler without changing the
    behaviour of any user who has not answered the level question.
    """
    user_rank = rank(user_level)
    if user_rank is None:
        return True

    item_bucket = level_for_pattern(pattern_id)
    if item_bucket is None:
        return True
    item_rank = BUCKETS.index(item_bucket)

    if item_rank > user_rank:
        return False  # ceiling: above the learner's level
    if item_rank < user_rank:
        return pattern_id in (weak_patterns or ())  # floor: only real gaps
    return True  # at level


def _at_or_below(user_level: str | None, pattern_id: str) -> bool:
    """The ceiling alone, with the ledger floor dropped."""
    user_rank = rank(user_level)
    item_bucket = level_for_pattern(pattern_id)
    if user_rank is None or item_bucket is None:
        return True
    return BUCKETS.index(item_bucket) <= user_rank


def select(items, user_level, weak_patterns=None, *, pattern_of=None):
    """Apply the serving rule to a pool of items, degrading gracefully.

    Not every drill has content at every level — ``faelle`` is entirely
    A1/A2, ``zeitfaerbung`` entirely A2/B1 — so the strict rule alone would
    hand a B2+ learner an EMPTY Fälle round and take the drill dark. That
    is worse than the problem being fixed, so this walks a ladder:

    1. the full rule (at level, plus below-level gaps the ledger knows);
    2. if that is empty, the ceiling ALONE — a learner with a clean ledger
       gets the below-level items back, which is also how the ledger first
       learns what they actually miss;
    3. if that is still empty, empty — the drill genuinely has nothing at
       or below this learner's level (zeitfaerbung for an A1 learner), and
       serving B1 tense-aspect work to an A1 beginner is exactly what this
       whole change exists to stop. The caller decides what to show.

    **The ceiling is never crossed.** Only the ledger floor relaxes.
    """
    key = pattern_of or (lambda i: i.get("pattern_id"))
    if not items or rank(user_level) is None:
        return list(items)

    strict = [i for i in items if allows(user_level, key(i), weak_patterns)]
    if strict:
        return strict
    return [i for i in items if _at_or_below(user_level, key(i))]
