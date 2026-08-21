"""Seed-pool loader/validator for Briefkasten (the letter-writing exercise).

Same fail-loud philosophy as ``verbindungen/content.py``: a malformed seed
aborts startup (``main.py`` lifespan) instead of 500ing mid-practice.

A seed is the *situation*, not the letter — ``briefkasten/writer.py`` writes
the German at request time. So there is no answer key to validate here, only
the shape the writer and the judges both depend on.
"""

from functools import lru_cache
from pathlib import Path

import yaml

from grammar.levels import bucket_of

_SEEDS_PATH = Path(__file__).parent / "seeds.yaml"

REGISTERS = ("informal", "formal")
LEVELS = ("a1", "a2", "b1", "b2")

# LEVEL-001/LEVEL-002: the learner's self-declared bucket
# (grammar/levels.py::BUCKETS) -> the seed levels it may draw from. Strict,
# not a ceiling: a learner who said "A2" gets A2 situations only — the
# letter, its word target and the judge's expectations all follow the seed
# level, so an A1 seed for a B1+ learner was a 30-50-word letter graded to
# A1, which is what prompted this.
# B2+ keeps the combined (b1, b2) pool for now because only 2 b2 seeds
# exist — a b2-only pool would repeat almost immediately. Authoring more b2
# seeds is tracked as follow-up content work.
# Pool sizes today (seeds.yaml): a1 -> 6, a2 -> 7, b1 -> 6, b2 -> 2.
BUCKET_SEED_LEVELS: dict[str, tuple[str, ...]] = {
    "A1": ("a1",),
    "A2": ("a2",),
    "B1": ("b1",),
    "B2+": ("b1", "b2"),
}

# How much the learner is asked to write, by seed level — carried over from
# Spralingua v1's email exercise, where these ranges were tuned in practice.
# Also what the feedback judge scores against: 90 words is a strong A1 letter
# and a thin B2 one, and the score has to know the difference.
WORD_TARGETS: dict[str, tuple[int, int]] = {
    "a1": (30, 50),
    "a2": (50, 80),
    "b1": (80, 120),
    "b2": (100, 150),
}

# Exactly four, because the hint judge reports coverage back as a fixed-length
# checklist the UI ticks off. Changing this number is a contract change across
# briefkasten/judge.py and the frontend.
POINT_COUNT = 4

_REQUIRED = ("id", "register", "level", "sender", "situation", "points")


@lru_cache(maxsize=1)
def load_seeds() -> dict[str, dict]:
    """Parse and validate the seed pool once per process; return ``{id: seed}``."""
    with open(_SEEDS_PATH, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    seeds = (data or {}).get("seeds")
    if not seeds:
        raise ValueError(f"{_SEEDS_PATH}: no 'seeds' list")

    catalog: dict[str, dict] = {}
    for i, seed in enumerate(seeds):
        where = f"{_SEEDS_PATH}: seeds[{i}] ({seed.get('id', '?')})"
        for field in _REQUIRED:
            if not seed.get(field):
                raise ValueError(f"{where}: missing '{field}'")
        if seed["register"] not in REGISTERS:
            raise ValueError(f"{where}: register must be one of {REGISTERS}")
        if seed["level"] not in LEVELS:
            raise ValueError(f"{where}: level must be one of {LEVELS}")

        sender = seed["sender"]
        if not isinstance(sender, dict):
            raise ValueError(f"{where}: 'sender' must be a mapping")
        for field in ("name", "relation"):
            if not sender.get(field):
                raise ValueError(f"{where}: missing 'sender.{field}'")

        points = seed["points"]
        if not isinstance(points, list) or len(points) != POINT_COUNT:
            raise ValueError(f"{where}: needs exactly {POINT_COUNT} 'points'")
        for j, point in enumerate(points):
            if not isinstance(point, str) or not point.strip():
                raise ValueError(f"{where}: points[{j}] is empty")

        if seed["id"] in catalog:
            raise ValueError(f"{where}: duplicate seed id")
        catalog[seed["id"]] = seed
    return catalog


def seeds_for_level(seeds: list[dict], user_level: str | None) -> list[dict]:
    """Narrow ``seeds`` to the learner's level bucket (LEVEL-001).

    ``None`` — the learner hasn't answered the level question, or picked
    "not sure" — returns the pool untouched: they draw from every level,
    exactly as before. Unknown level strings behave the same. If the bucket
    has no seeds at all (cannot happen with today's pool, every bucket has
    at least one) the pool is returned untouched rather than starving the
    exercise: a slightly-off letter beats no letter.
    """
    bucket = bucket_of(user_level)
    if bucket is None:
        return seeds
    wanted = BUCKET_SEED_LEVELS.get(bucket)
    if not wanted:
        return seeds
    narrowed = [s for s in seeds if s["level"] in wanted]
    return narrowed or seeds


def word_target(level: str) -> tuple[int, int]:
    """The (min, max) word range for a seed level. Unknown levels can't reach
    here — ``load_seeds`` validates ``level`` against ``LEVELS`` at startup —
    but B1 is a safe middle if one ever does."""
    return WORD_TARGETS.get(level, WORD_TARGETS["b1"])
