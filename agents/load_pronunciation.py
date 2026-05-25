"""Lesson-aware pronunciation-assessment config loader.

Reads ``locale`` from ``agents/prompts/{lesson_id}.yaml``. Missing key or
empty value returns ``None``; ``pipeline/factory.py`` short-circuits on
``None`` and skips the pronunciation step.

``lesson_zero`` is hard-skipped regardless of YAML contents: it is the
open-conversation default and must never be evaluated (memory rule
"feedback-lesson-zero-untouchable").
"""

from loguru import logger

from .load_prompts import load_prompts


def load_pronunciation_locale(lesson_id: str) -> str | None:
    """Return the BCP-47 locale string for a lesson, or ``None`` to skip.

    Skip conditions:
      - ``lesson_id == "lesson_zero"`` (explicit rule).
      - The lesson YAML omits ``locale`` or it is empty.
    """
    if lesson_id == "lesson_zero":
        logger.info("lesson_zero is unscored by design; skipping pronunciation assessment")
        return None
    locale = load_prompts(lesson_id).get("locale")
    if not locale:
        logger.info(f"No locale on lesson_id={lesson_id!r}; skipping pronunciation assessment")
        return None
    return locale
