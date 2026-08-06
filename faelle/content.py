"""Item-catalog loader/validator for Fälle (GRAM-006 Proposal-1, "the case
cluster").

Same fail-loud philosophy as ``verbindungen/content.py``: a malformed item
aborts startup (``main.py`` lifespan) instead of 500ing mid-practice.
"""

from functools import lru_cache
from pathlib import Path

import yaml

from grammar import load_taxonomy

_ITEMS_PATH = Path(__file__).parent / "items.yaml"

# The six taxonomy patterns this exercise targets, and NO others — every
# German case decision outside verb conjugation itself: the one two-way
# preposition group, the two fixed-case preposition groups, the direct-
# object article, the small dative-only verb group, and the personal-
# pronoun case split.
TARGET_PATTERNS = (
    "wechselpraepositionen",
    "dativ-praepositionen",
    "akkusativ-praepositionen",
    "akkusativ-artikel",
    "dativ-verben",
    "pronomen-akk-dat",
)

_REQUIRED = ("id", "pattern_id", "level", "frame", "answer", "hint", "rule")
_LEVELS = ("a1", "a2")


@lru_cache(maxsize=1)
def load_items() -> dict[str, dict]:
    """Parse and validate the catalog once per process; return ``{id: item}``."""
    with open(_ITEMS_PATH, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    items = (data or {}).get("items")
    if not items:
        raise ValueError(f"{_ITEMS_PATH}: no 'items' list")

    taxonomy = load_taxonomy()
    catalog: dict[str, dict] = {}
    for i, item in enumerate(items):
        where = f"{_ITEMS_PATH}: items[{i}] ({item.get('id', '?')})"
        for field in _REQUIRED:
            if not item.get(field):
                raise ValueError(f"{where}: missing '{field}'")
        if item["pattern_id"] not in TARGET_PATTERNS:
            raise ValueError(f"{where}: pattern_id must be one of {TARGET_PATTERNS}")
        if item["pattern_id"] not in taxonomy:
            raise ValueError(f"{where}: pattern_id not in grammar/taxonomy.yaml")
        if item["level"] not in _LEVELS:
            raise ValueError(f"{where}: level must be one of {_LEVELS}")
        if item["frame"].count("___") != 1:
            raise ValueError(f"{where}: frame needs exactly one '___' gap")
        if item["id"] in catalog:
            raise ValueError(f"{where}: duplicate item id")
        catalog[item["id"]] = item
    return catalog
