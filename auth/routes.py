"""Auth HTTP routes for Google sign-in (AUTH-001, P-3).

``POST /auth/google`` — exchange a Google ID token for our session JWT.
``GET  /auth/me``      — return the signed-in user (guarded by the session JWT).
``PUT  /auth/level``   — set the learner's CEFR bucket (LEVEL-001).
"""

from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError

from database import User, get_sessionmaker, upsert_user
from database.repository import load_user_level, set_user_level
from grammar import BUCKETS

from .deps import get_current_user_id
from .tokens import AuthError, issue_session_jwt, verify_google_id_token

router = APIRouter(prefix="/auth", tags=["auth"])


class GoogleAuthBody(BaseModel):
    credential: str  # the Google ID token from Google Identity Services


class UserOut(BaseModel):
    id: str
    email: str | None
    name: str | None
    picture: str | None
    role: str
    # LEVEL-001. null means "never asked" — the frontend shows the picker,
    # and until it's answered the learner is served everything as before.
    level: str | None = None


class LevelBody(BaseModel):
    # A Literal, not a str: the ck_users_level check constraint (0021,
    # widened by 0023) is the backstop, but a client should never be able
    # to write a junk bucket that silently disables filtering. ``None`` is
    # the "not sure" escape.
    level: Optional[Literal["A1", "A2", "B1", "B2+"]]


class AuthOut(BaseModel):
    token: str
    user: UserOut


@router.post("/google", response_model=AuthOut)
async def auth_google(body: GoogleAuthBody) -> AuthOut:
    """Verify a Google ID token, upsert the user, and return a session JWT.

    401 if the token is invalid or the Google email isn't verified; 503 if the
    server has no GOOGLE_CLIENT_ID configured yet (verify raises AuthError, which
    we treat as "auth not available"). The returned ``token`` is what the
    frontend stores and replays on the WS handshake and ``/say``.
    """
    try:
        claims = await verify_google_id_token(body.credential)
    except AuthError as e:
        raise HTTPException(status_code=401, detail=str(e))

    if not claims.get("email_verified"):
        raise HTTPException(
            status_code=401, detail="Google account email is not verified."
        )

    user_id = claims["sub"]
    email = claims.get("email")
    name = claims.get("name")
    picture = claims.get("picture")

    try:
        async with get_sessionmaker()() as db:
            role = await upsert_user(
                db, user_id=user_id, email=email, name=name, picture=picture
            )
            # Returning users already have a level; sending it with the
            # sign-in payload is what keeps the picker from reappearing on
            # every login.
            level = await load_user_level(db, user_id=user_id)
    except SQLAlchemyError:
        raise HTTPException(status_code=500, detail="Could not persist the user.")

    token = issue_session_jwt(user_id, role)
    return AuthOut(
        token=token,
        user=UserOut(
            id=user_id, email=email, name=name, picture=picture, role=role, level=level
        ),
    )


@router.get("/me", response_model=UserOut)
async def auth_me(user_id: str = Depends(get_current_user_id)) -> UserOut:
    """Return the signed-in user's profile, resolved from the session JWT."""
    async with get_sessionmaker()() as db:
        user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")
    return UserOut(
        id=user.id,
        email=user.email,
        name=user.name,
        picture=user.picture,
        role=user.role,
        level=user.level,
    )


@router.put("/level", response_model=UserOut)
async def set_level(
    body: LevelBody, user_id: str = Depends(get_current_user_id)
) -> UserOut:
    """Set (or clear) the learner's CEFR bucket — LEVEL-001.

    Clearing it (``level: null``) is a supported answer, not an error: it
    puts the learner back on "serve everything", which is what the picker's
    "not sure" escape does. Nobody should be locked into a level they
    guessed at once.
    """
    if body.level is not None and body.level not in BUCKETS:
        # Unreachable through the Literal above; kept so a future caller
        # that bypasses the model can't write past the check constraint.
        raise HTTPException(status_code=422, detail="Unknown level.")
    try:
        async with get_sessionmaker()() as db:
            await set_user_level(db, user_id=user_id, level=body.level)
            user = await db.get(User, user_id)
    except SQLAlchemyError:
        raise HTTPException(status_code=500, detail="Could not save your level.")
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")
    return UserOut(
        id=user.id,
        email=user.email,
        name=user.name,
        picture=user.picture,
        role=user.role,
        level=user.level,
    )
