"""Feste Verbindungen — mixed verb-chunk drill (GRAM-002, Exercise D).

Complete the fixed combination — reflexive pronoun, fixed preposition,
governed case, da-/wo-compound — with reflexive and non-reflexive verbs
mixed in the same set, so "does this verb even take one?" stays the live
question. Targets ``reflexivpronomen`` / ``verben-mit-praepositionen`` /
``da-wo-komposita``; reads and feeds the grammar-error ledger.
"""

from .content import load_items
from .routes import router

__all__ = ["router", "load_items"]
