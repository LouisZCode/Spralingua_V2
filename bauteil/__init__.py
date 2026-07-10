"""Bauteil-Sätze — written declension drill (GRAM-002, Exercise A).

The learner gets raw, uninflected parts (``ein · gut · Job``) and a sentence
frame that forces a case, and types the correctly inflected phrase. Targets
``adjektivendungen`` / ``possessiv-endung`` / ``n-deklination``; reads and
feeds the grammar-error ledger (GRAM-001).
"""

from .content import load_items
from .routes import router

__all__ = ["router", "load_items"]
