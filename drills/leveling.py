"""Apply the level serving rule to a drill's item pool (LEVEL-001).

One helper, six callers. Every grammar drill assembles its round the same
way — load the catalog, weight it by the ledger, cut it to size — and each
now narrows the catalog to what the learner's level warrants before any of
that weighting happens.

The rule itself lives in ``grammar/levels.py``; this module is only the
part that needs the database (the learner's level and their open ledger
patterns), which ``grammar/`` deliberately stays free of.

**Never fatal.** A level read that fails returns the pool untouched and
logs. Losing the filter costs a learner a slightly-off round; raising here
would cost them the round entirely, and a personalisation outage must never
be the thing that breaks practice — same contract as the ledger reads these
handlers already wrap.
"""

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from database.repository import load_user_level, load_weak_patterns
from grammar import select

__all__ = ["apply_level"]


async def apply_level(
    db: AsyncSession,
    *,
    user_id: str,
    items: list,
    drill: str,
    pattern_of=None,
    always_allow=None,
) -> list:
    """Narrow ``items`` to what a learner at their level should be served.

    Returns the pool unchanged when the learner has no level set, when the
    drill's items carry no known pattern, or when anything goes wrong.

    ``always_allow`` (LEVEL-002) is passed straight through to
    ``grammar.select()`` — see its docstring. Defaults to ``None`` (no
    change in behaviour) for every caller that doesn't opt in.
    """
    try:
        level = await load_user_level(db, user_id=user_id)
        if level is None:
            return items
        weak = await load_weak_patterns(db, user_id=user_id)
        narrowed = select(items, level, weak, pattern_of=pattern_of, always_allow=always_allow)
        if len(narrowed) != len(items):
            # Worth a line: a suddenly-small round is otherwise indis-
            # tinguishable from a content bug when reading logs.
            logger.info(
                "{} round narrowed by level {}: {} -> {} items",
                drill,
                level,
                len(items),
                len(narrowed),
            )
        return narrowed
    except Exception:
        logger.exception("{} level read failed — serving an unfiltered round", drill)
        return items
