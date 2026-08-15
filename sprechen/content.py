"""Task-catalog loader/validator for Sprechen & transkribieren (GRAM-002,
Exercise B). Same fail-loud philosophy as ``bauteil/content.py``: a malformed
task aborts startup (``main.py`` lifespan) instead of 500ing mid-practice.
"""

from functools import lru_cache
from pathlib import Path

import yaml

from grammar import load_taxonomy

_TASKS_PATH = Path(__file__).parent / "tasks.yaml"

# The taxonomy patterns this exercise targets (todo GRAM-002, Exercise B —
# verb position / die Satzklammer, the load-side prescription).
# Der Konditional-Absprung expands this to fronted wenn/als-clauses: the flip
# (main clause verb-first after the comma) plus the mood (real, habitual
# past, or Konjunktiv II).
TARGET_PATTERNS = (
    "v2-wortstellung",
    "nebensatz-verbende",
    "perfekt-satzklammer",
    "modalverb-infinitiv-ende",
    "trennbare-verben",
    "konjunktiv2-hypothese",
    "als-vs-wenn",
)

_REQUIRED = ("id", "pattern_id", "title", "prompt", "forces", "min_elements")


@lru_cache(maxsize=1)
def load_tasks() -> dict[str, dict]:
    """Parse and validate the catalog once per process; return ``{id: task}``."""
    with open(_TASKS_PATH, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    tasks = (data or {}).get("tasks")
    if not tasks:
        raise ValueError(f"{_TASKS_PATH}: no 'tasks' list")

    taxonomy = load_taxonomy()
    catalog: dict[str, dict] = {}
    for i, task in enumerate(tasks):
        where = f"{_TASKS_PATH}: tasks[{i}] ({task.get('id', '?')})"
        for field in _REQUIRED:
            if not task.get(field):
                raise ValueError(f"{where}: missing '{field}'")
        if task["pattern_id"] not in TARGET_PATTERNS:
            raise ValueError(f"{where}: pattern_id must be one of {TARGET_PATTERNS}")
        if task["pattern_id"] not in taxonomy:
            raise ValueError(f"{where}: pattern_id not in grammar/taxonomy.yaml")
        # GRAM-008: the count `forces` requires ("at least 3 main clauses",
        # "weil" twice, …) in a form the judge's constraint_met override can
        # compare against — see sprechen/judge.py::judge_spoken.
        min_elements = task.get("min_elements")
        if (
            not isinstance(min_elements, int)
            or isinstance(min_elements, bool)
            or min_elements < 1
        ):
            raise ValueError(f"{where}: min_elements must be a positive int")
        if "distinct" in task and not isinstance(task["distinct"], bool):
            raise ValueError(f"{where}: distinct must be a bool")
        keyterms = task.get("keyterms")
        if keyterms is not None and (
            not isinstance(keyterms, list)
            or not all(isinstance(k, str) and k.strip() for k in keyterms)
        ):
            raise ValueError(f"{where}: keyterms must be a list of non-empty strings")
        if task["id"] in catalog:
            raise ValueError(f"{where}: duplicate task id")
        catalog[task["id"]] = task
    return catalog
