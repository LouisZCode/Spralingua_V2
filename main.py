# Backend:  uvicorn main:app --host 0.0.0.0 --port 8765
# Frontend: cd frontend && npm run dev

import asyncio
import re
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import text

from pipecat.frames.frames import LLMContextFrame
from pipecat.processors.aggregators.llm_context import LLMContext

from agents.load_prompts import load_prompts, load_tandem_topics
from auth import AuthError, decode_session_jwt, router as auth_router
from bauteil import load_items as load_bauteil_items, router as bauteil_router
from config import database_url
from config.settings import allowed_origins, demo_session_timeout_s, say_max_chars
from database import ActivitySession, dispose_engine, get_sessionmaker, init_engine
from pipeline import run_pipeline
from pipeline.factory import ACTIVE_TASKS, validate_lesson_languages
from satz import router as satz_router, sync_curated_content
from security import (
    client_ip,
    demo_release,
    demo_try_admit,
    origin_allowed,
    say_try_admit,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Fail-loud: if Postgres is unreachable, init_engine raises here and
    # uvicorn exits non-zero. Saves us from silent broken-persistence builds.
    await init_engine(database_url)
    # Same philosophy for curated Satzschmiede content: YAML packs are synced
    # into Postgres on every boot, so a malformed pack aborts startup instead
    # of serving a half-broken gallery (SATZ-002).
    await sync_curated_content()
    # And for the two per-lesson language sources (ENGLISH_LESSONS vs the YAML
    # `locale:`) — a drifted lesson would be transcribed in one language and
    # pronunciation-scored in another, so it aborts startup instead.
    validate_lesson_languages()
    # And for the Bauteil-Sätze item catalog (GRAM-002, Exercise A) — same
    # rule: malformed drill content aborts startup, not a mid-practice 500.
    load_bauteil_items()
    yield
    await dispose_engine()


app = FastAPI(lifespan=lifespan)

# The /say, /auth, /sessions, and /satz HTTP endpoints are CORS-checked
# (WebSockets aren't). Origins come from ALLOWED_ORIGINS (config.settings) so
# prod sets its real frontend origin without a code edit; defaults to
# localhost:3000 for dev.
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Google sign-in + session-JWT routes (AUTH-001).
app.include_router(auth_router)
# Satzschmiede packs/pool/deck routes (SATZ-002).
app.include_router(satz_router)
# Bauteil-Sätze round/attempt routes (GRAM-002, Exercise A).
app.include_router(bauteil_router)


@app.get("/health")
async def health():
    """Readiness check (INFRA-003): verifies Postgres is still reachable.

    Startup is fail-loud (lifespan), but the DB can die *after* startup —
    session writes are non-fatal by design, so nothing else would surface
    it. Returning 503 here lets Railway's healthcheck stop routing to an
    instance that has silently stopped persisting.
    """
    try:
        async with asyncio.timeout(2):
            async with get_sessionmaker()() as db:
                await db.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001 — any failure means "not ready"
        raise HTTPException(status_code=503, detail="database unreachable")
    return {"status": "ok"}


_PARAGRAPH_MARKER = "\x00PARA\x00"


def _normalize_briefing_text(s: str) -> str:
    """Collapse author wrap-for-readability into flowing prose.

    YAML briefing copy is authored as wrapped paragraphs — `|` (literal)
    preserves every wrap as a hard newline, and the frontend's
    ``whitespace-pre-line`` renders each as a visible break. Authors
    shouldn't have to remember `|` vs `>` scalar styles to get clean
    output, so we normalize here:

    - Runs of 2+ newlines (intentional paragraph break) survive as one
      newline (still renders as a paragraph gap via whitespace-pre-line).
    - Single newlines (wrap-for-readability) collapse to a space.
    - Runs of horizontal whitespace collapse to one space.

    Applies to ``/lessons/{id}`` response only; raw text is preserved in
    ``lesson_snapshot`` and prompt assembly for fidelity.
    """
    if not isinstance(s, str) or not s:
        return s
    text = re.sub(r"\n\s*\n+", _PARAGRAPH_MARKER, s.strip())
    text = text.replace("\n", " ")
    text = re.sub(r"[ \t]+", " ", text)
    return text.replace(_PARAGRAPH_MARKER, "\n\n")


@app.get("/lessons/{lesson_id}")
def lesson_meta(lesson_id: str):
    """Briefing copy + title (+ optional completion content) for the conversation page.

    Loader already falls back to `lesson_zero` on unknown id (with a logged
    warning), so the frontend never gets a 404 here. ``completion`` may be
    ``None`` — the frontend's ``SessionSummaryModal`` supplies defaults.
    Briefing strings are normalized so authored YAML wrap-for-readability
    doesn't leak into the rendered briefing card.
    """
    lesson = load_prompts(lesson_id)
    briefing_raw = lesson.get("briefing") or {}
    # Briefing values can be either a string (prose) or a list of strings
    # (bulleted goals). Normalize each item either way.
    def _normalize_value(v):
        if isinstance(v, str):
            return _normalize_briefing_text(v)
        if isinstance(v, list):
            return [
                _normalize_briefing_text(item) if isinstance(item, str) else item
                for item in v
            ]
        return v
    briefing = {k: _normalize_value(v) for k, v in briefing_raw.items()}
    return {
        "title": lesson["title"],
        "briefing": briefing,
        "completion": lesson.get("completion"),
    }


@app.get("/sessions/{session_id}")
async def get_session(session_id: str, request: Request):
    """Return the activity_session row for the post-session modal to render.

    ``ended_at IS NULL`` means the disconnect-side finalize step hasn't run
    yet (evaluators + DB update happen in pipeline/factory.py's finally:
    block, after the WS is already closed). The frontend polls this route
    every ~1s until ``ended_at`` is set, then renders the eval blocks.

    Auth (SEC-002): requires the caller's session JWT, and the row is only
    returned to its owner. No demo carve-out — the public demo never opens
    the summary modal, so demo rows have no legitimate caller here.
    """
    sub = _bearer_subject(request)
    if sub is None:
        raise HTTPException(status_code=401, detail="missing or invalid token")
    try:
        session_uuid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid session_id")

    async with get_sessionmaker()() as db:
        row = await db.get(ActivitySession, session_uuid)
    if row is None:
        raise HTTPException(status_code=404, detail="session not found")
    if row.user_id != sub:
        raise HTTPException(status_code=403, detail="not your session")
    return {
        "id": str(row.id),
        "lesson_id": row.lesson_id,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "ended_at": row.ended_at.isoformat() if row.ended_at else None,
        "ended_by": row.ended_by,
        "passed": row.passed,
        "goal_eval": row.goal_eval,
        "pron_eval": row.pron_eval,
        # error_eval is surfaced ONLY for tandem (the debrief the TandemDebriefModal
        # renders). Drill sessions also store an error_eval (the silent grammar
        # harvest), but feedback separation is the point — never hand it back to a
        # drill client, which would let it leak into the drill summary modal.
        "error_eval": row.error_eval if row.lesson_id == "tandem" else None,
    }


@app.get("/tandem/topics")
def tandem_topics():
    """Conversation-topic suggestions for the Grammatik-Tandem topic screen
    (TANDEM-001). Public, non-sensitive content — same as ``/lessons/{id}``."""
    return {"topics": load_tandem_topics()}


@app.websocket("/ws/{user_id}")
async def ws_endpoint(
    websocket: WebSocket,
    user_id: str,
    voice: str = "happy_harry",
    lesson: str = "lesson_zero",
    topic: str = "",
    token: str = "",
):
    """Authenticated learn socket (AUTH-001, P-3).

    Browsers can't set custom headers on a WebSocket handshake, so the session
    JWT rides as a ``?token=`` query param. We verify it and close *before*
    accept on a bad/absent token (1008 policy) so the browser's onError fires
    cleanly. The token's subject is authoritative for identity — the path
    ``user_id`` is ignored, so a client can't drive an arbitrary id by editing
    the URL.
    """
    try:
        sub = decode_session_jwt(token)
    except AuthError:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    # `topic` is the tandem conversation theme (ignored by non-tandem lessons).
    await run_pipeline(websocket, sub, voice, lesson, topic=topic)


# Demo user ids are minted client-side as `demo-<uuid4>`; pin the shape so the
# public route can't be driven with arbitrary ids.
_DEMO_USER_ID_RE = re.compile(r"^demo-[A-Za-z0-9_-]{1,64}$")


@app.websocket("/ws/demo/{user_id}")
async def ws_demo_endpoint(websocket: WebSocket, user_id: str):
    """Public, unauthenticated front-page demo socket (SEC-001).

    Hardened sibling of /ws/{user_id}: the lesson and voice are forced
    server-side (a visitor can't drive an arbitrary lesson or voice), the Origin
    header is checked (WebSocket handshakes bypass browser CORS), and global +
    per-IP concurrency/rate caps gate admission. Rejections close *before*
    accept with a WS status code (1008 policy / 1013 try-again) so the browser's
    onError fires cleanly. A successful admit owns one concurrency slot, released
    in ``finally`` no matter how the session ends.
    """
    if not origin_allowed(websocket.headers.get("origin")):
        await websocket.close(code=1008)
        return
    if not _DEMO_USER_ID_RE.match(user_id):
        await websocket.close(code=1008)
        return
    ip = client_ip(websocket)
    ok, _reason = demo_try_admit(ip)
    if not ok:
        await websocket.close(code=1013)
        return
    try:
        await websocket.accept()
        await run_pipeline(
            websocket,
            user_id,
            voice="German_Female",
            lesson_id="welcome",
            session_timeout_s=demo_session_timeout_s,
            # All anonymous demo sessions persist under one seeded `demo` user
            # row (AUTH-001) — `user_id` stays per-session for ACTIVE_TASKS/say
            # routing, but the DB FK points at the shared sentinel so the demo
            # doesn't fill `users` with a row per visitor.
            db_user_id="demo",
        )
    finally:
        demo_release(ip)


class SayBody(BaseModel):
    text: str


def _bearer_subject(request: Request) -> str | None:
    """Verify the session JWT from an ``Authorization: Bearer`` header.

    Returns the token subject (user_id) on success, or ``None`` if the header is
    absent/malformed or the token fails verification.
    """
    auth = request.headers.get("authorization", "")
    scheme, _, raw = auth.partition(" ")
    if scheme.lower() != "bearer" or not raw.strip():
        return None
    try:
        return decode_session_jwt(raw.strip())
    except AuthError:
        return None


@app.post("/say/{user_id}")
async def say(user_id: str, body: SayBody, request: Request):
    """Inject a typed turn into an active pipeline.

    LangchainProcessor accepts LLMContextFrame directly — it extracts the last
    message's content and runs the chain. For a complete typed utterance we
    don't need STT/VAD/buffer at all; we just hand the LLM stage the same frame
    type the converter would have produced. The converter passes it through
    unchanged via its else branch. TTS / audio playback / Langfuse / goodbye
    detection / exchange count all fire identically to a spoken turn.

    Two callers, two gates (the path id picks the branch):

    - ``demo-*`` ids — the public demo's no-mic fallback (SEC-001). Token-free,
      so it carries the same world-facing guards as the demo socket: Origin
      allowlist + per-IP rate limit. The id shape is pinned by the regex.
    - real ids — the authenticated /learn flow (AUTH-001). Requires an
      ``Authorization: Bearer`` session JWT whose subject equals the path id, so
      one signed-in user can't inject turns into another's active session.

    A length cap bounds LLM token cost for both.
    """
    if _DEMO_USER_ID_RE.match(user_id):
        if not origin_allowed(request.headers.get("origin")):
            raise HTTPException(status_code=403, detail="origin not allowed")
        if not say_try_admit(client_ip(request)):
            raise HTTPException(status_code=429, detail="too many requests")
        target_id = user_id
    else:
        sub = _bearer_subject(request)
        if sub is None:
            raise HTTPException(status_code=401, detail="missing or invalid token")
        if sub != user_id:
            raise HTTPException(status_code=403, detail="token does not match user")
        target_id = sub

    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is empty")
    if len(text) > say_max_chars:
        raise HTTPException(status_code=413, detail="text too long")
    task = ACTIVE_TASKS.get(target_id)
    if task is None:
        raise HTTPException(status_code=404, detail="No active session for that user_id")

    context = LLMContext([{"role": "user", "content": text}])
    await task.queue_frame(LLMContextFrame(context=context))
    return {"ok": True}
