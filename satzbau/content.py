"""Item-catalog loader/validator for Satzbau (clause-construction drill:
order chips into a correct German subordinate clause or question).

Same fail-loud philosophy as ``faelle/content.py``: a malformed item aborts
startup (``main.py`` lifespan) instead of 500ing mid-practice.
"""

from functools import lru_cache
from pathlib import Path

import yaml

from grammar import load_taxonomy

_ITEMS_PATH = Path(__file__).parent / "items.yaml"

# The five taxonomy patterns this exercise targets, and NO others — all five
# boil down to the same underlying problem: build the clause, then put the
# verb where German puts it (clause-final for a relative clause / indirect
# question / purpose clause, second position — verb-first for yes/no — for
# a direct question).
TARGET_PATTERNS = (
    "relativsatz",
    "indirekte-frage",
    "zu-infinitiv",
    "um-zu-damit",
    "fragen-wortstellung",
)

# ``given`` and ``accepts`` are allowed to be legitimately EMPTY (an empty
# lead-in for fragen-wortstellung items, where the whole sentence is chips;
# an empty accepts list wherever only one order is genuinely valid German —
# see items.yaml's header comment) — so they're checked for PRESENCE below,
# not truthiness. Every other field must be non-empty.
_REQUIRED = (
    "id", "pattern_id", "level", "given", "task", "chips", "answer", "accepts", "hint", "rule",
)
_REQUIRED_NONEMPTY = ("id", "pattern_id", "level", "task", "chips", "answer", "hint", "rule")
_LEVELS = ("a1", "a2", "b1")


def _multiset(tokens: list) -> list:
    return sorted(tokens)


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
            if field not in item:
                raise ValueError(f"{where}: missing '{field}'")
        for field in _REQUIRED_NONEMPTY:
            if not item[field]:
                raise ValueError(f"{where}: missing '{field}'")
        if item["pattern_id"] not in TARGET_PATTERNS:
            raise ValueError(f"{where}: pattern_id must be one of {TARGET_PATTERNS}")
        if item["pattern_id"] not in taxonomy:
            raise ValueError(f"{where}: pattern_id not in grammar/taxonomy.yaml")
        if item["level"] not in _LEVELS:
            raise ValueError(f"{where}: level must be one of {_LEVELS}")
        if not isinstance(item["chips"], list) or not isinstance(item["answer"], list):
            raise ValueError(f"{where}: 'chips' and 'answer' must be lists")
        if _multiset(item["chips"]) != _multiset(item["answer"]):
            raise ValueError(f"{where}: 'chips' must contain exactly the tokens of 'answer'")
        accepts = item["accepts"]
        if not isinstance(accepts, list):
            raise ValueError(f"{where}: 'accepts' must be a list")
        answer_ms = _multiset(item["answer"])
        for j, alt in enumerate(accepts):
            if not isinstance(alt, list) or _multiset(alt) != answer_ms:
                raise ValueError(f"{where}: accepts[{j}] is not a reordering of 'answer'")
            if alt == item["answer"]:
                raise ValueError(f"{where}: accepts[{j}] duplicates the canonical 'answer'")
        if item["id"] in catalog:
            raise ValueError(f"{where}: duplicate item id")
        catalog[item["id"]] = item
    return catalog
