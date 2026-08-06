"""Briefkasten — the letter-writing exercise.

A letter arrives from a friend, a landlord or an office; the learner writes
back in German. Two graded attempts: hints first (where the problems are,
never the fixes), corrections second (the repaired letter, the reasons, a
score, and — when there is genuinely something to teach — how a German would
actually have written it).

The one written production exercise in the app, and therefore the cleanest
grammar signal it has: a considered letter beats a speech transcript whose
endings Deepgram ate. Attempt one feeds the shared error ledger.
"""

from .content import load_seeds
from .routes import router

__all__ = ["router", "load_seeds"]
