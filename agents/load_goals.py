"""Lesson-aware goals loader.

Each lesson's evaluator config (`language`, `goal_text`, `pass_criterion`)
lives under its `lesson_id` key in `agents/goals.yaml`. Lessons without an
entry skip the post-session evaluator entirely (e.g. internal dev lessons
like `goodbye_test` — see `pipeline/factory.py` disconnect block). No
fallback: silently judging lesson A by lesson B's criterion was a footgun,
not a feature. The YAML is the v1 stub for the DATA-003 Postgres table;
the call-site signature stays the same when that swap happens.
"""

from pathlib import Path

import yaml
from loguru import logger

_GOALS_FILE = Path(__file__).parent / "goals.yaml"


def load_goal(lesson_id: str) -> dict | None:
    """Load a lesson's evaluator config, or `None` if the lesson has no entry."""
    with open(_GOALS_FILE, "r", encoding="utf-8") as f:
        goals = yaml.safe_load(f)
    if lesson_id not in goals:
        logger.info(f"No evaluator goal for lesson_id={lesson_id!r}; skipping evaluation")
        return None
    return goals[lesson_id]
