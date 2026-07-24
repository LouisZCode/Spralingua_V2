"""Scenario-catalog loader/validator for Szenario-Sparring (P1, thin slice).

Same fail-loud philosophy as ``sprechen/content.py``: a malformed scenario
aborts startup (``main.py`` lifespan) instead of 500ing mid-practice.
"""

from functools import lru_cache
from pathlib import Path

import yaml

_SCENARIOS_PATH = Path(__file__).parent / "scenarios.yaml"

_REQUIRED = (
    "id",
    "stufe",
    "persona",
    "register",
    "kontext",
    "questions",
    "ziel_vokabular",
)
_PERSONA_REQUIRED = ("name", "role", "attitude")

# Keep round content tight — one persona, one unpredictable question, not a
# whole dialogue tree.
_MIN_QUESTIONS, _MAX_QUESTIONS = 3, 5
_MIN_VOKABULAR, _MAX_VOKABULAR = 4, 8


@lru_cache(maxsize=1)
def load_scenarios() -> dict[str, dict]:
    """Parse and validate the catalog once per process; return ``{id: scenario}``."""
    with open(_SCENARIOS_PATH, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    scenarios = (data or {}).get("scenarios")
    if not scenarios:
        raise ValueError(f"{_SCENARIOS_PATH}: no 'scenarios' list")

    catalog: dict[str, dict] = {}
    for i, scenario in enumerate(scenarios):
        where = f"{_SCENARIOS_PATH}: scenarios[{i}] ({scenario.get('id', '?')})"
        for field in _REQUIRED:
            if not scenario.get(field):
                raise ValueError(f"{where}: missing '{field}'")

        if not isinstance(scenario["stufe"], int):
            raise ValueError(f"{where}: 'stufe' must be an int")

        # SZEN-004: the address form is declared data, never inferred — new
        # scenes must commit to du (the default) or Sie (only where the scene
        # genuinely demands it).
        if scenario["register"] not in ("du", "Sie"):
            raise ValueError(f"{where}: 'register' must be 'du' or 'Sie'")

        persona = scenario["persona"]
        if not isinstance(persona, dict):
            raise ValueError(f"{where}: 'persona' must be a mapping")
        for field in _PERSONA_REQUIRED:
            value = persona.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{where}: persona missing '{field}'")

        if not isinstance(scenario["kontext"], str) or not scenario["kontext"].strip():
            raise ValueError(f"{where}: 'kontext' must be a non-empty string")

        questions = scenario["questions"]
        if not isinstance(questions, list) or not all(
            isinstance(q, str) and q.strip() for q in questions
        ):
            raise ValueError(f"{where}: 'questions' must be a list of non-empty strings")
        if not (_MIN_QUESTIONS <= len(questions) <= _MAX_QUESTIONS):
            raise ValueError(
                f"{where}: 'questions' must have {_MIN_QUESTIONS}-{_MAX_QUESTIONS} entries"
            )

        ziel_vokabular = scenario["ziel_vokabular"]
        if not isinstance(ziel_vokabular, list) or not all(
            isinstance(v, str) and v.strip() for v in ziel_vokabular
        ):
            raise ValueError(
                f"{where}: 'ziel_vokabular' must be a list of non-empty strings"
            )
        if not (_MIN_VOKABULAR <= len(ziel_vokabular) <= _MAX_VOKABULAR):
            raise ValueError(
                f"{where}: 'ziel_vokabular' must have "
                f"{_MIN_VOKABULAR}-{_MAX_VOKABULAR} entries"
            )

        if scenario["id"] in catalog:
            raise ValueError(f"{where}: duplicate scenario id")
        catalog[scenario["id"]] = scenario
    return catalog
