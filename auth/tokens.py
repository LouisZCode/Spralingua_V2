"""Token verification + session-JWT mint/verify for Google sign-in (AUTH-001).

Two token types live here and must not be confused:

  * the **Google ID token** — a JWT minted by Google, handed to the frontend by
    Google Identity Services and POSTed to ``/auth/google``. We only *verify* it
    (signature against Google's public certs, audience == our client id, issuer,
    expiry); we never mint it.
  * our **session JWT** — an HS256 token we mint ourselves after a successful
    Google verification. It's what the frontend then presents on the WS
    handshake (``?token=``) and ``/say`` (``Bearer``). We both mint and verify it.

The Google verification (``verify_oauth2_token``) does blocking I/O the first
time (it fetches + caches Google's certs), so it's wrapped in
``asyncio.to_thread`` to keep the event loop free.
"""

import asyncio
import secrets
import time

import jwt
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token
from loguru import logger

from config.settings import app_env, google_client_id, jwt_expiry_days, jwt_secret

_ALGORITHM = "HS256"
_GOOGLE_ISSUERS = {"accounts.google.com", "https://accounts.google.com"}

# One transport reused across calls (it caches Google's signing certs internally).
_google_transport = google_requests.Request()

# JWT_SECRET signs our session tokens. In dev (APP_ENV unset/"dev") we fall back
# to a random per-process secret so it works out of the box — but that secret
# differs per worker and dies on restart, so in any deployed env (APP_ENV set to
# something other than "dev") a missing JWT_SECRET is fatal rather than silently
# minting unverifiable tokens.
if jwt_secret:
    _SIGNING_SECRET = jwt_secret
elif app_env == "dev":
    _SIGNING_SECRET = secrets.token_urlsafe(48)
    logger.warning(
        "JWT_SECRET is not set — using an ephemeral per-process secret (APP_ENV=dev). "
        "Session tokens won't survive a restart; set JWT_SECRET before deploying."
    )
else:
    raise RuntimeError(
        f"JWT_SECRET is required when APP_ENV={app_env!r} (deployed). Refusing to "
        "mint session tokens with an ephemeral secret — set JWT_SECRET."
    )


class AuthError(Exception):
    """Raised when a token (Google ID token or our session JWT) is invalid."""


async def verify_google_id_token(credential: str) -> dict:
    """Verify a Google ID token and return its claims, or raise ``AuthError``.

    Checks signature (against Google's certs), audience == our client id, and
    expiry (all inside ``verify_oauth2_token``), then the issuer explicitly. The
    caller owns the ``email_verified`` check — that's a policy decision, not a
    token-validity one.
    """
    if not google_client_id:
        raise AuthError("Google auth is not configured (GOOGLE_CLIENT_ID unset).")
    try:
        claims = await asyncio.to_thread(
            id_token.verify_oauth2_token,
            credential,
            _google_transport,
            google_client_id,
            # Tolerate small clock drift between this machine and Google —
            # the library default of 0 rejects fresh tokens as "used too
            # early" when the local clock runs even a few seconds behind
            # (observed locally: Mac 6s behind → every sign-in 401'd).
            clock_skew_in_seconds=10,
        )
    except ValueError as e:
        raise AuthError(f"Invalid Google token: {e}") from e
    if claims.get("iss") not in _GOOGLE_ISSUERS:
        raise AuthError(f"Unexpected token issuer: {claims.get('iss')!r}")
    return claims


def issue_session_jwt(user_id: str, role: str = "normal") -> str:
    """Mint an HS256 session JWT for ``user_id`` (Google ``sub``).

    ``role`` rides as a signed claim so it's tamper-resistant — a client can't
    promote itself by editing localStorage. Valid for ``jwt_expiry_days``. No
    refresh in v1 — the frontend re-runs Google sign-in when this one expires.
    """
    now = int(time.time())
    payload = {
        "sub": user_id,
        "role": role,
        "iat": now,
        "exp": now + jwt_expiry_days * 86400,
    }
    return jwt.encode(payload, _SIGNING_SECRET, algorithm=_ALGORITHM)


def decode_session_jwt(token: str) -> str:
    """Verify our session JWT and return its subject (user_id), or raise ``AuthError``."""
    try:
        payload = jwt.decode(token, _SIGNING_SECRET, algorithms=[_ALGORITHM])
    except jwt.PyJWTError as e:
        raise AuthError(f"Invalid session token: {e}") from e
    sub = payload.get("sub")
    if not sub:
        raise AuthError("Session token is missing its subject.")
    return sub
