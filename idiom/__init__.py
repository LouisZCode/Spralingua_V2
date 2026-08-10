"""Idiom — the "how would a German say this?" judge (IDIOM-002 Proposal-1).

One on-demand rephrase per learner tap: the learner's own spoken or written
line goes in, a plain-but-native rewrite comes back — or null when their
German already sounds German, which is a first-class outcome, not a failure.
Phrasing only: grammar verdicts belong to the drill judges and the tandem
debrief, never to this module. Calibration reference: the letter's
``natural_version`` pass in ``briefkasten/judge.py``, the one surface where
this idea shipped first.
"""

from .routes import router

__all__ = ["router"]
