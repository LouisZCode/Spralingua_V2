"""
Per-client runtime context and the active prompt's data model.

`prompts` (loaded once at import) is the active `agents/prompts.yaml` —
keys `conversational_prompt_v2`, `short_term_template`, `long_term_template`.
Consumed by `agents/conversational_prompt.py::layered_prompt_middleware`.

The legacy `personalized_prompt` middleware that filled the V1
language-learning template (situations / vocabulary / agent_story / etc.)
has been retired; that content now lives in the gitignored
`agents/prompts_archived.yaml`. To revert behavior temporarily, switch
the active middleware in `agents/conversation_agent.py` back to
`conversational_prompt_v2_middleware` (the standalone V2 path).
"""

from dataclasses import dataclass, field

from .load_prompts import load_prompts

prompts = load_prompts()

# Module-level capture of the most recent assembled system prompt, read by
# the session transcript logger after the first LLM call. Middlewares are
# expected to write to this.
_last_system_prompt = None


def get_last_system_prompt() -> str:
    """Return the last generated system prompt (for transcript logging)."""
    return _last_system_prompt


@dataclass
class StudentProfile:
    """Long-term layer of the system prompt — facts that persist across sessions.

    Today populated from the hardcoded store in `agents/fake_profiles.py`;
    will move to a real DB once the schema is validated. Date strings are
    ISO `YYYY-MM-DD` and form the basis of "what you remember" history that
    the agent reasons against using the session's `today` from short-term.
    """
    student_name: str
    native_language: str
    target_language: str
    interests: list[str] = field(default_factory=list)
    life_events: list[dict] = field(default_factory=list)        # [{"date": "...", "fact": "..."}]
    topics_covered: list[dict] = field(default_factory=list)     # [{"date": "...", "topic": "...", "notes": "..."}]
    difficulties: list[dict] = field(default_factory=list)       # [{"date": "...", "fact": "..."}]


@dataclass
class Context:
    """Per-client runtime context passed to LangGraph via `agent.astream(context=...)`.

    Short-term fields come from the WebSocket query params (`user_level`,
    `situation`, `agent_voice`). `profile` is attached at connect time by
    `ClientWrapper.__init__` via `fake_profiles.load_profile(user_id)`.

    `situation`, `agent_voice`, `agent_personality` stay on the dataclass
    so the UI plumbing keeps working, but they are NOT rendered into the
    active prompt today — the layered middleware only uses `user_level`
    plus the profile. They will be re-surfaced when persona lives in DB.
    """
    user_level: str = "A1"
    situation: str = "introducing_yourself"
    agent_voice: str = "happy_harry"
    agent_personality: str = "friendly"
    profile: StudentProfile | None = None
