"""Interview exercise (INTV-003 slice 2).

HTTP router serving the personal audio pool (``audio_items``/``audio_chunks``,
registered by slice 1's ``scripts/import_interviews.py``) plus the two-round
"listen & retell" / "read & answer" exercise, ported from the local-only
workbench (``interview_local/``, git-excluded — see ``interview_local/app.py``
and ``interview_local/judges/``). Unlike the workbench, this package persists
nothing about an attempt except the one thing that matters long-term: a
flawed answer's grammar slips still reach the shared ``user_errors`` ledger,
via the same harvester every other drill/tandem session uses
(``agents/error_extractor.py::extract_errors``).
"""

from .routes import router

__all__ = ["router"]
