"""Lesson-aware prompt loader.

Each lesson lives in `agents/prompts/{lesson_id}.yaml`. Adding a lesson
means dropping a new YAML file — no code edits needed. Unknown ids fall
back to `lesson_zero` with a warning.
"""

from functools import lru_cache
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


def list_lesson_ids() -> list[str]:
    """Stems of every lesson YAML under prompts/ — skips data files that are
    not lessons (``tandem_topics.yaml`` has no ``type`` key). Used by the
    startup language cross-check in ``pipeline/factory.py``."""
    ids: list[str] = []
    for path in sorted(_PROMPTS_DIR.glob("*.yaml")):
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if isinstance(data, dict) and "type" in data:
            ids.append(path.stem)
    return ids


@lru_cache(maxsize=1)
def tandem_lesson_ids() -> frozenset[str]:
    """Ids of every ``type: tandem`` lesson (TAND-008: Lena + Paul, and any
    future partner). The single source for call sites that used to hardcode
    ``"tandem"`` — debrief surfacing in ``main.py``, the REC-001 tandem count
    in ``stats/routes.py``, the topics endpoint below."""
    return frozenset(
        lid for lid in list_lesson_ids() if load_prompts(lid).get("type") == "tandem"
    )


@lru_cache(maxsize=8)
def load_tandem_topics(lesson_id: str = "tandem") -> list[str]:
    """Load a tandem partner's topic suggestions (TANDEM-001 / TAND-008).

    Each partner has their own pool at ``{lesson_id}_topics.yaml`` (Lena's
    everyday themes in ``tandem_topics.yaml``, Paul's professional pool in
    ``tandem_paul_topics.yaml``). Not lessons — flat theme lists served to the
    topic screen (`GET /tandem/topics?lesson=…`) and never routed through the
    lesson loader. Only real tandem lesson ids are accepted (the endpoint takes
    the id from the query string). Fail-loud like the satz content sync: a
    malformed list is a content bug, not a silent empty screen.
    """
    if lesson_id not in tandem_lesson_ids():
        raise ValueError(f"not a tandem lesson: {lesson_id!r}")
    path = _PROMPTS_DIR / f"{lesson_id}_topics.yaml"
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    topics = (data or {}).get("topics")
    if not topics:
        raise ValueError(f"{path}: no 'topics' list")
    return topics
