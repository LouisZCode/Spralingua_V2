"""Szenario-Sparring — in-character speaking drill (P1, thin slice).

An in-character persona asks the learner ONE unpredictable German question;
the learner speaks ONE answer; a judge reviews the answer's STRUCTURE (not
grammar) and extracts the "skeleton" hiding in it. Grammar errors are mined
silently from the same transcript in the background and credited to the
existing grammar ledger (``agents/error_extractor.py``) — the learner never
sees that part.
"""

from .content import load_scenarios
from .routes import router

__all__ = ["router", "load_scenarios"]
