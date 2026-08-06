"""Fälle — interleaved German case drill (GRAM-006 Proposal-1, "the case
cluster").

Six taxonomy patterns that all boil down to "which case, and which article/
pronoun form does that case need" — the one two-way preposition group, the
two fixed-case preposition groups, the direct-object article, the small
dative-only verb group, and the personal-pronoun case split — drilled
INTERLEAVED so no single pattern lets the learner coast on autopilot (see
faelle/routes.py's MAX_PER_PATTERN / MIN_PATTERNS). Reads and feeds the
grammar-error ledger.
"""

from .content import load_items
from .routes import router

__all__ = ["router", "load_items"]
