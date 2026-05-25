"""Lesson-aware evaluator-goals loader.

Goal data lives alongside the rest of the lesson in
`agents/prompts/{lesson_id}.yaml` under the top-level keys ``goals`` and
``pass_threshold``. A lesson without those keys is intentionally not
evaluated — `pipeline/factory.py` short-circuits on `None`.

`lesson_zero` is hard-skipped regardless of YAML contents: it is the
open-conversation default and must never be evaluated (memory rule
"feedback-lesson-zero-untouchable").
"""

from loguru import logger

from .load_prompts import load_prompts


def load_goal(lesson_id: str) -> dict | None:
    """Return `{goals, pass_threshold}` for a lesson, or `None` to skip eval.

    Skip conditions:
      - `lesson_id == "lesson_zero"` (explicit rule).
      - The lesson YAML omits `goals` or `pass_threshold`.
      - `goals` is empty.
    """
    if lesson_id == "lesson_zero":
        logger.info("lesson_zero is unscored by design; skipping evaluation")
        return None
    lesson = load_prompts(lesson_id)
    goals = lesson.get("goals")
    pass_threshold = lesson.get("pass_threshold")
    if not goals or pass_threshold is None:
        logger.info(f"No evaluator goals on lesson_id={lesson_id!r}; skipping evaluation")
        return None
    return {"goals": goals, "pass_threshold": pass_threshold}
