"""Satzbau — German clause-construction drill.

Five taxonomy patterns that are all the same underlying problem: build the
clause, then put the verb where German puts it — relativsatz,
indirekte-frage, zu-infinitiv, um-zu-damit, fragen-wortstellung — drilled
INTERLEAVED so no single pattern lets the learner coast on autopilot (see
satzbau/routes.py's MAX_PER_PATTERN / MIN_PATTERNS). Reads and feeds the
grammar-error ledger.
"""

from .content import load_items
from .routes import router

__all__ = ["router", "load_items"]
