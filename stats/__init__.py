"""Cross-drill practice stats (DATA-004).

Reads ``drill_attempts`` (the append-only per-attempt event log every drill
now writes to, alongside its own exercise-local state) and ``user_errors``
(the grammar ledger) to answer "how has practice been going lately" — a
question neither table alone can answer well: the ledger only tracks
lifetime pattern tallies, and no other surface aggregates across all six
drills.
"""

from .routes import router

__all__ = ["router"]
