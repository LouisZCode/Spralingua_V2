"""Loader/validator for the grammar-pattern taxonomy (GRAM-001).

Same fail-loud philosophy as ``satz/content.py``: a malformed catalog is a
content bug and must not survive silently — extractors classify against
these ids, and the ledger stores them as primary-key parts.
"""

from functools import lru_cache
from pathlib import Path

import yaml

_TAXONOMY_PATH = Path(__file__).parent / "taxonomy.yaml"

_REQUIRED_FIELDS = ("id", "label", "level", "description", "wrong", "right", "elicit")
_LEVELS = {"A1", "A2", "B1", "B2"}


@lru_cache(maxsize=1)
def load_taxonomy() -> dict[str, dict]:
    """Parse and validate the catalog once per process; return ``{id: pattern}``.

    The dict preserves YAML order (A1 → B1), which is also the order
    ``taxonomy_brief`` presents to classifying LLMs.
    """
    with open(_TAXONOMY_PATH, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    patterns = (data or {}).get("patterns")
    if not patterns:
        raise ValueError(f"{_TAXONOMY_PATH}: no 'patterns' list")

    catalog: dict[str, dict] = {}
    for i, p in enumerate(patterns):
        where = f"{_TAXONOMY_PATH}: patterns[{i}]"
        for field in _REQUIRED_FIELDS:
            if not p.get(field):
                raise ValueError(f"{where} ({p.get('id', '?')}): missing '{field}'")
        if p["level"] not in _LEVELS:
            raise ValueError(f"{where} ({p['id']}): level {p['level']!r} not in {sorted(_LEVELS)}")
        if p["id"] in catalog:
            raise ValueError(f"{where}: duplicate pattern id {p['id']!r}")
        catalog[p["id"]] = p
    return catalog


def taxonomy_brief() -> str:
    """The catalog as compact one-liners for a classifying LLM prompt.

    The wrong → right pair disambiguates better than the prose description,
    so that's what rides along; ``description``/``elicit`` are for humans and
    the (Phase 3) grammar-focus prompt layer.
    """
    return "\n".join(
        f'- {p["id"]} — {p["label"]} ("{p["wrong"]}" → "{p["right"]}")'
        for p in load_taxonomy().values()
    )
