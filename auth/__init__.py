"""Authentication package (AUTH-001): Google sign-in + backend session JWT."""

from .deps import get_current_user_id
from .routes import router
from .tokens import AuthError, decode_session_jwt, issue_session_jwt

__all__ = [
    "AuthError",
    "decode_session_jwt",
    "get_current_user_id",
    "issue_session_jwt",
    "router",
]
