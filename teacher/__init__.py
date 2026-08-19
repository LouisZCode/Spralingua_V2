"""Clara's interactive-exercise loop — backend half (AGENT-00X).

Normalizes items across five typed-answer drills (faelle, satzbau,
zeitfaerbung, verbindungen, bauteil) into one generic exercise-card shape
Clara can hand her student mid-conversation, and grades attempts using each
drill's own deterministic checks + judge — writing NOTHING to any
learning-state table (see routes.py's loud comment on POST
/teacher/exercise/attempts). The frontend half — rendering the card and
wiring the `exercise_request` RTVI message — is a later slice.
"""

from .routes import router

__all__ = ["router"]
