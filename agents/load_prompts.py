"""Lesson-aware prompt loader.

Each lesson lives in `agents/prompts/{lesson_id}.yaml`. Adding a lesson
means dropping a new YAML file — no code edits needed. Unknown ids fall
back to `lesson_zero` with a warning.
"""

from pathlib import Path

import yaml
from loguru import logger

_PROMPTS_DIR = Path(__file__).parent / "prompts"
_FALLBACK = "lesson_zero"


def load_prompts(lesson_id: str) -> dict:
    """Load a lesson's YAML. Falls back to `lesson_zero` on missing file."""
    path = _PROMPTS_DIR / f"{lesson_id}.yaml"
    if not path.exists():
        if lesson_id == _FALLBACK:
            raise FileNotFoundError(f"Fallback lesson YAML missing: {path}")
        logger.warning(f"Unknown lesson_id={lesson_id!r}; falling back to {_FALLBACK!r}")
        return load_prompts(_FALLBACK)
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)
