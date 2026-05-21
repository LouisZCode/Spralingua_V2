"""
Hardcoded long-term profile store — stand-in for the eventual PostgreSQL
table backing the long-term prompt layer.

`load_profile(user_id)` is called once per WebSocket connect in
`ClientWrapper.__init__` and the result lives on `Context.profile` for
the lifetime of the connection.

The frontend currently hardcodes `USER_ID = "0001"` (see
`frontend/src/components/VoiceChat.tsx`) while we iterate on the prompt
and LLM layers, so the `"0001"` profile here is the one the agent will
actually load during testing. Unknown ids fall back to
`FALLBACK_PROFILE` so the pipeline never crashes.
"""

from .dynamic_prompts import StudentProfile


FALLBACK_PROFILE = StudentProfile(
    student_name="friend",
    native_language="unknown",
    target_language="English",
    interests=[],
    life_events=[],
    topics_covered=[],
    difficulties=[],
)


PROFILES: dict[str, StudentProfile] = {
    # Hardcoded test id — matches `USER_ID` in
    # `frontend/src/components/VoiceChat.tsx`. Will be replaced once the
    # backend has real user accounts.
    "0001": StudentProfile(
        student_name="Luis",
        native_language="Spanish",
        target_language="English",
        interests=["climbing", "cooking"],
        life_events=[
            {"date": "2025-09-05", "fact": "moved to Germany"},
        ],
        topics_covered=[
            {"date": "2025-10-01", "topic": "mentioned he likes brazilian coffee", "notes": "struggled with past tenses"},
        ],
        difficulties=[],
    ),
}


def load_profile(user_id: str) -> StudentProfile:
    return PROFILES.get(user_id, FALLBACK_PROFILE)
