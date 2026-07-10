"""Sprechen & transkribieren — constrained speaking drill (GRAM-002, Exercise B).

A constrained speaking prompt traps one verb-position structure (V2, verb-
final, Satzklammer, modal bracket, separable prefix); the learner speaks;
the raw Deepgram transcript is judged for constraint + target structure.
The load-side prescription: speaking reveals what writing hides.
"""

from .content import load_tasks
from .routes import router

__all__ = ["router", "load_tasks"]
