"""Expanding-interval scheduler for Satzschmiede (SATZ-002, Phase 4).

One ladder, two moves: a correct WORD climbs to the next rung, anything else
(word wrong, or the learner revealed the example instead of attempting) drops
the card back to "due now". Grammar slips elsewhere in the sentence never
touch the schedule — consistent with the verdict model, where only word_ok
decides pass/fail.

The ``user_cards`` columns this writes (due_at / interval_days / reps /
last_score) have existed since the Satzschmiede migration precisely so this
phase could be pure code — no schema change.
"""

from datetime import datetime, time, timedelta

# Interval ladder in days. A card that outgrows the ladder keeps doubling —
# no cap, mature words just fade far into the future.
STEPS = (1, 3, 7, 16, 35)


def next_interval(current: int | None) -> int:
    """The rung above ``current`` (None/0 — new or lapsed — starts at 1)."""
    for step in STEPS:
        if not current or current < step:
            return step
    return current * 2


def schedule(word_ok: bool, current: int | None, now: datetime) -> tuple[int, datetime]:
    """``(interval_days, due_at)`` after one graded attempt.

    due_at floors to midnight so "1 day" means "any time tomorrow" — an
    evening practice must not push the card past the next morning's session.
    A miss goes to ``(0, now)``: still due, retryable this same session.
    """
    if not word_ok:
        return 0, now
    interval = next_interval(current)
    return interval, datetime.combine(now.date() + timedelta(days=interval), time.min)
