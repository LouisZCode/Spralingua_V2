"""Item-catalog loader/validator for Feste Verbindungen (GRAM-002, Exercise D).

Same fail-loud philosophy as ``bauteil/content.py``: a malformed item aborts
startup (``main.py`` lifespan) instead of 500ing mid-practice.
"""

from functools import lru_cache
from pathlib import Path

import yaml

from grammar import load_taxonomy

_ITEMS_PATH = Path(__file__).parent / "items.yaml"

# The taxonomy patterns this exercise targets (todo GRAM-002, Exercise D).
# The first three are the original lexicon chunks (reflexivity, fixed
# prepositions, da-/wo-compounds); the remaining five are an A1/A2 hygiene
# batch — patterns with no exercise anywhere else in the app. The loader
# below cross-checks every id against grammar/taxonomy.yaml.
TARGET_PATTERNS = (
    "reflexivpronomen",
    "verben-mit-praepositionen",
    "da-wo-komposita",
    "subjekt-verb-endung",
    "sein-vs-haben",
    "nicht-vs-kein",
    "am-um-im-zeit",
    "komparativ-form",
)

_REQUIRED = ("id", "pattern_id", "frame", "answer", "chunk", "hint")


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
        if item["frame"].count("___") != 1:
            raise ValueError(f"{where}: frame needs exactly one '___' gap")
        if item["id"] in catalog:
            raise ValueError(f"{where}: duplicate item id")
        catalog[item["id"]] = item
    return catalog
