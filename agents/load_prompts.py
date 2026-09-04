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

# PROMPT-003: schema validation so authoring errors (a typo'd field name, a
# forgotten section) fail loud at load — and therefore at startup, since the
# lifespan's validate_lesson_languages() already calls load_prompts() for
# every lesson id — instead of surfacing as a KeyError mid-connect.
_COMMON_REQUIRED = ("id", "type", "title")
_REQUIRED_BY_TYPE = {
    "conversation": ("base_prompt", "short_term_template", "long_term_template"),
    "respond": ("persona_prompt",),
    "tandem": ("persona_prompt", "short_term_template", "grammar_focus_header", "vocab_header", "long_term_header"),
    "teacher": ("persona_prompt", "short_term_template", "grammar_focus_header"),
}


def _validate_lesson(data: dict, path: Path) -> None:
    """Raise ValueError naming every missing/invalid field, all at once."""
    if not isinstance(data, dict):
        raise ValueError(f"{path}: lesson YAML must be a mapping, got {type(data).__name__}")

    errors = []

    def _require_str(field: str) -> None:
        value = data.get(field)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{field!r} (missing or empty string)")

    for field in _COMMON_REQUIRED:
        _require_str(field)

    max_exchanges = data.get("max_exchanges")
    if not isinstance(max_exchanges, int) or isinstance(max_exchanges, bool) or max_exchanges < 1:
        errors.append("'max_exchanges' (missing or not an int >= 1)")

    # Unknown `type` values validate common fields only — the middleware
    # falls back to lesson_zero with a warning for those.
    for field in _REQUIRED_BY_TYPE.get(data.get("type"), ()):
        _require_str(field)

    if errors:
        raise ValueError(f"{path}: invalid lesson YAML — " + ", ".join(errors))


def load_prompts(lesson_id: str) -> dict:
    """Load a lesson's YAML. Falls back to `lesson_zero` on missing/unknown id.

    SEC-005: `lesson_id` arrives from caller input (the WS `?lesson=` param,
    the unauthenticated `GET /lessons/{lesson_id}`) and used to be joined
    straight into a filesystem path (`_PROMPTS_DIR / f"{lesson_id}.yaml"`)
    with no containment check — `../../etc/passwd` (or any relative escape
    landing on a real `*.yaml` file elsewhere) would have been read and
    handed to `yaml.safe_load`. Whitelisting against `list_lesson_ids()`
    (every stem under `prompts/` that has a `type:` key) BEFORE the path is
    ever built makes an escape impossible: everything past this check is
    only ever a name this function already confirmed is a real lesson on
    disk. Unknown ids keep the pre-existing behavior — log + fall back to
    `lesson_zero` — they just can no longer touch the filesystem outside
    this directory to get there.
    """
    if lesson_id != _FALLBACK and lesson_id not in list_lesson_ids():
        logger.warning(f"Unknown lesson_id={lesson_id!r}; falling back to {_FALLBACK!r}")
        return load_prompts(_FALLBACK)

    path = _PROMPTS_DIR / f"{lesson_id}.yaml"
    if not path.exists():
        if lesson_id == _FALLBACK:
            raise FileNotFoundError(f"Fallback lesson YAML missing: {path}")
        # Defensive only now that the whitelist above is the real guard —
        # covers a race where the file is deleted between that check and
        # this read, not the primary path-containment defense.
        logger.warning(f"Unknown lesson_id={lesson_id!r}; falling back to {_FALLBACK!r}")
        return load_prompts(_FALLBACK)
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    _validate_lesson(data, path)
    return data


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
