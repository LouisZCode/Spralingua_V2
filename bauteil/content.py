"""Item-catalog loader/validator for Bauteil-Sätze (GRAM-002, Exercise A).

Same fail-loud philosophy as ``satz/content.py`` and ``grammar/loader.py``:
a malformed item is a content bug and must abort startup (``main.py`` calls
:func:`load_items` in the lifespan) instead of 500ing mid-practice. The
catalog stays YAML-served — no DB table; only the ledger writes persist.
"""

from functools import lru_cache
from pathlib import Path

import yaml

from grammar import load_taxonomy

_ITEMS_PATH = Path(__file__).parent / "items.yaml"

# The taxonomy patterns this exercise targets (todo GRAM-002, Exercise A).
# The round endpoint intersects the learner's hot ledger patterns with these.
TARGET_PATTERNS = ("adjektivendungen", "possessiv-endung", "n-deklination")

_CASES = {"nominative", "accusative", "dative"}
_GENDERS = {"m", "f", "n", "pl"}
_ARTICLES = {"der", "die", "das", "ein", "kein",
             "mein", "dein", "sein", "ihr", "unser", "euer"}
_REQUIRED = ("id", "pattern_id", "case", "gender", "parts", "frame", "answer", "hint")


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
        if item["case"] not in _CASES:
            raise ValueError(f"{where}: case must be one of {sorted(_CASES)}")
        if item["gender"] not in _GENDERS:
            raise ValueError(f"{where}: gender must be one of {sorted(_GENDERS)}")
        if item.get("article") is not None and item["article"] not in _ARTICLES:
            raise ValueError(f"{where}: unknown article {item['article']!r}")
        parts = item["parts"]
        if (
            not isinstance(parts, list)
            or len(parts) < 2
            or not all(isinstance(p, str) and p.strip() for p in parts)
        ):
            raise ValueError(f"{where}: parts must be a list of 2+ non-empty strings")
        if item["frame"].count("___") != 1:
            raise ValueError(f"{where}: frame needs exactly one '___' gap")
        if item["id"] in catalog:
            raise ValueError(f"{where}: duplicate item id")
        catalog[item["id"]] = item
    return catalog
