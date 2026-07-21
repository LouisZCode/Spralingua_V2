"""Expanding-interval scheduler for Satzschmiede (SATZ-002, Phase 4).

One ladder, two moves: a correct WORD climbs to the next rung, anything else
(word wrong, or the learner revealed the example instead of attempting) drops
the card back to due now, at a quarter of its old interval. Grammar slips
elsewhere in the sentence never touch the schedule — consistent with the
verdict model, where only word_ok decides pass/fail.

The ``user_cards`` columns this writes (due_at / interval_days / reps /
last_score) have existed since the Satzschmiede migration precisely so this
phase could be pure code — no schema change.
"""

import random
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


def lapse_interval(current: int | None) -> int:
    """Interval after a lapse — a partial reset, not a full one.

    A lapse falls roughly two rungs instead of restarting the ladder: 35 // 4
    = 8, so the next success climbs back to 16 via ``next_interval(8)``.
    Rungs 1 and 3 floor to 0 (ladder bottom — a full restart is cheap there).
    Modern schedulers (FSRS) reduce memory stability on failure, never zero
    it; this is the same idea applied to a fixed ladder.
    """
    return (current or 0) // 4


def schedule(word_ok: bool, current: int | None, now: datetime) -> tuple[int, datetime]:
    """``(interval_days, due_at)`` after one graded attempt.

    due_at floors to midnight so "1 day" means "any time tomorrow" — an
    evening practice must not push the card past the next morning's session.
    A miss goes to ``(lapse_interval(current), now)``: still due now, so the
    same-session retry keeps working — only the stored rung is punished.
    """
    if not word_ok:
        return lapse_interval(current), now
    interval = next_interval(current)
    due_date = now.date() + timedelta(days=interval)
    if interval >= 7:
        # Fuzz the due DATE only — the stored interval stays canonical/
        # unfuzzed so ladder climbing (next_interval) stays deterministic.
        # Without this, cards added together (a whole pack, a study binge)
        # all mature on the same day and stay clumped on that day forever.
        # Below 7 days, 15% rounds to zero anyway, so skip it.
        f = round(interval * 0.15)
        offset = random.randint(-f, f)
        due_date = now.date() + timedelta(days=max(1, interval + offset))
    return interval, datetime.combine(due_date, time.min)
