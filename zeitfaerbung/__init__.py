"""Zeitfärbung — Präteritum meaning drill (GRAM-003).

English collapses several meanings into "was"; German splits them
semantically: simply BEING in a state (war), changing INTO a state (wurde),
an action done TO the subject (Vorgangspassiv: wurde + Partizip), and
STAYING in a state (blieb). Some sentences genuinely accept both war and
wurde with different meanings — that ambiguity is drilled on purpose.
Targets ``praeteritum-sein-haben-modal`` / ``passiv-werden``; reads and
feeds the grammar-error ledger. Deterministic grading, no judge LLM.
"""

from .content import load_items
from .routes import router

__all__ = ["router", "load_items"]
