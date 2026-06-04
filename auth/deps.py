"""FastAPI dependency that resolves a session JWT to a user_id (AUTH-001).

Guards authenticated HTTP routes (e.g. ``GET /auth/me``). The WS + ``/say``
learn-flow guards are wired separately in Stage 3 — WS handshakes can't carry an
``Authorization`` header, so those read the token from a query param instead.
"""

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .tokens import AuthError, decode_session_jwt

_bearer = HTTPBearer(auto_error=True)


def get_current_user_id(
    creds: HTTPAuthorizationCredentials = Depends(_bearer),
) -> str:
    try:
        return decode_session_jwt(creds.credentials)
    except AuthError as e:
        raise HTTPException(status_code=401, detail=str(e))
