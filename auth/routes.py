"""Auth HTTP routes for Google sign-in (AUTH-001, P-3).

``POST /auth/google`` — exchange a Google ID token for our session JWT.
``GET  /auth/me``      — return the signed-in user (guarded by the session JWT).
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError

from database import User, get_sessionmaker, upsert_user

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
    except SQLAlchemyError:
        raise HTTPException(status_code=500, detail="Could not persist the user.")

    token = issue_session_jwt(user_id, role)
    return AuthOut(
        token=token,
        user=UserOut(
            id=user_id, email=email, name=name, picture=picture, role=role
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
    )
