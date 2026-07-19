"""Item-catalog loader/validator for Zeitfärbung (GRAM-003): fill a
Präteritum blank with war / wurde / blieb by MEANING.

Same fail-loud philosophy as ``bauteil/content.py`` / ``verbindungen/content.py``:
a malformed item aborts startup (``main.py`` lifespan) instead of 500ing
mid-practice.
"""

from functools import lru_cache
from pathlib import Path

import yaml

from grammar import load_taxonomy

_ITEMS_PATH = Path(__file__).parent / "items.yaml"

# The closed conjugation families this drill grades against (also mirrored in
# ``zeitfaerbung/routes.py`` for grading — kept here too so content validation
# doesn't need to import the routes module).
FORM_FAMILIES = {
    "sein": ("war", "warst", "waren", "wart"),
    "werden": ("wurde", "wurdest", "wurden", "wurdet"),
    "bleiben": ("blieb", "bliebst", "blieben", "bliebt"),
}
ALL_FORMS = frozenset(f for forms in FORM_FAMILIES.values() for f in forms)
FORM_TO_FAMILY = {f: fam for fam, forms in FORM_FAMILIES.items() for f in forms}

# Six content groups (GRAM-003 spec). "eindeutig" groups take exactly one
# answer + hint/why; "doppeldeutig" groups take exactly two (a sein-family +
# a werden-family form) + per-form readings, and FORBID hint/why — an English
# hint would force one reading and spoil the ambiguity that's the whole point.
GROUPS = (
    "zustand",
    "uebergang",
    "passiv",
    "verbleib",
    "doppeldeutig-adjektiv",
    "doppeldeutig-partizip",
)
DOPPELDEUTIG_GROUPS = ("doppeldeutig-adjektiv", "doppeldeutig-partizip")

# Derived rule (user's spec): passiv + doppeldeutig-partizip are the two
# groups whose gap is a Vorgangspassiv auxiliary (werden + Partizip, with the
# Partizip already printed in the frame) — they target passiv-werden. Every
# other group targets the Präteritum sein/werden/bleiben choice itself.
_PASSIV_PATTERN = "passiv-werden"
_PRAETERITUM_PATTERN = "praeteritum-sein-haben-modal"


def _pattern_for_group(group: str) -> str:
    if group in ("passiv", "doppeldeutig-partizip"):
        return _PASSIV_PATTERN
    return _PRAETERITUM_PATTERN


_REQUIRED = ("id", "group", "pattern_id", "frame", "answers")


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

        if item["id"] in catalog:
            raise ValueError(f"{where}: duplicate item id")

        group = item["group"]
        if group not in GROUPS:
            raise ValueError(f"{where}: group must be one of {GROUPS}")
        is_doppel = group in DOPPELDEUTIG_GROUPS

        expected_pattern = _pattern_for_group(group)
        if item["pattern_id"] != expected_pattern:
            raise ValueError(
                f"{where}: group {group!r} must use pattern_id {expected_pattern!r}, "
                f"got {item['pattern_id']!r}"
            )
        if item["pattern_id"] not in taxonomy:
            raise ValueError(f"{where}: pattern_id not in grammar/taxonomy.yaml")

        if item["frame"].count("___") != 1:
            raise ValueError(f"{where}: frame needs exactly one '___' gap")

        answers = item["answers"]
        if not isinstance(answers, list):
            raise ValueError(f"{where}: 'answers' must be a list")
        for form in answers:
            if form not in ALL_FORMS:
                raise ValueError(
                    f"{where}: answer {form!r} is not a war/wurde/blieb form"
                )

        if is_doppel:
            if len(answers) != 2:
                raise ValueError(f"{where}: doppeldeutig groups need exactly 2 answers")
            families = {FORM_TO_FAMILY[a] for a in answers}
            if families != {"sein", "werden"}:
                raise ValueError(
                    f"{where}: doppeldeutig answers must be one war-family + "
                    f"one wurde-family form, got families {sorted(families)}"
                )
            if item.get("hint"):
                raise ValueError(f"{where}: doppeldeutig items must not have 'hint'")
            if item.get("why"):
                raise ValueError(f"{where}: doppeldeutig items must not have 'why'")
            readings = item.get("readings")
            if not isinstance(readings, dict):
                raise ValueError(f"{where}: doppeldeutig items need a 'readings' dict")
            if set(readings.keys()) != set(answers):
                raise ValueError(
                    f"{where}: 'readings' keys must exactly match 'answers' {answers}"
                )
            for form, reading in readings.items():
                if not isinstance(reading, str) or not reading.strip():
                    raise ValueError(f"{where}: 'readings[{form!r}]' must be non-empty")
        else:
            if len(answers) != 1:
                raise ValueError(f"{where}: eindeutig groups need exactly 1 answer")
            if not item.get("hint"):
                raise ValueError(f"{where}: eindeutig items need 'hint'")
            if not item.get("why"):
                raise ValueError(f"{where}: eindeutig items need 'why'")
            if item.get("readings"):
                raise ValueError(f"{where}: eindeutig items must not have 'readings'")

        catalog[item["id"]] = item
    return catalog
