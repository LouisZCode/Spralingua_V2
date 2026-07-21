"""Per-client runtime context and the active prompt's data model.

The conversation system prompt is assembled per-request by
`agents.conversational_prompt.layered_prompt_middleware`, which loads
the lesson-specific YAML from `agents/prompts/{lesson_id}.yaml` via
`agents.load_prompts.load_prompts`. Add a lesson by dropping a new YAML.

The standalone V1 (`conversational_prompt_middleware`) and V2
(`conversational_prompt_v2_middleware`) middlewares remain importable
as revert paths — they return inline Python constants unchanged.
"""

from dataclasses import dataclass, field

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

    `lesson_id` routes the middleware to the matching
    `agents/prompts/{lesson_id}.yaml`. Defaults to `"lesson_zero"` (open
    conversational mode). `profile` is attached at connect time by
    `ClientWrapper.__init__` via `fake_profiles.load_profile(user_id)`.
    Only the `conversation` lesson type reads the profile; `respond`-type
    lessons (e.g. A1-L1) use just the persona prompt. Student CEFR level
    is no longer carried on Context — the conversation middleware reads
    `default_level` directly from the lesson YAML until user profiles land.
    """
    lesson_id: str = "lesson_zero"
    agent_voice: str = "happy_harry"
    agent_personality: str = "friendly"
    profile: StudentProfile | None = None
    # Grammatik-Tandem (TANDEM-001) layers — populated at connect for
    # `type: tandem` lessons only, and read by the tandem middleware branch.
    # `topic` is the learner's chosen conversation theme (the `?topic=` WS query
    # param); `grammar_focus` is their top ~3 open ledger patterns, each
    # `{pattern_id, label, description, elicit, examples}`; `session_notes` is
    # the thin memory (recent tandem session-note strings); `vocab_words` is a
    # random sample of the learner's active-window deck words
    # (`{word, gloss}`, from `load_vocab_words`) for Lena to weave into her
    # own speech. Empty everywhere else.
    topic: str = ""
    grammar_focus: list = field(default_factory=list)
    session_notes: list = field(default_factory=list)
    vocab_words: list = field(default_factory=list)
