# Backend:  uvicorn main:app --host 0.0.0.0 --port 8765
# Frontend: cd frontend && npm run dev

import asyncio
import logging
import re
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import text

from pipecat.frames.frames import LLMContextFrame
from pipecat.processors.aggregators.llm_context import LLMContext

from agents.load_prompts import load_prompts, load_tandem_topics, tandem_lesson_ids
from agents.observability import get_trace_session, propagate_trace_context, tracer
from auth import AuthError, decode_session_jwt, get_current_user_id, router as auth_router
from bauteil import load_items as load_bauteil_items, router as bauteil_router
from briefkasten import load_seeds as load_briefkasten_seeds, router as briefkasten_router
from faelle import load_items as load_faelle_items, router as faelle_router
from genus import (
    load_exceptions as load_genus_exceptions,
    load_items as load_genus_items,
    load_rules as load_genus_rules,
    router as genus_router,
)
from idiom import router as idiom_router
from interview import router as interview_router
from payments import router as payments_router
from satzbau import load_items as load_satzbau_items, router as satzbau_router
from sprechen import load_tasks as load_sprechen_tasks, router as sprechen_router
from stats import router as stats_router
from szenario import load_scenarios as load_szenario_scenarios, router as szenario_router
from teacher import router as teacher_router
from verbformen import router as verbformen_router
from verbindungen import (
    load_items as load_verbindungen_items,
    router as verbindungen_router,
)
from zeitfaerbung import (
    load_items as load_zeitfaerbung_items,
    router as zeitfaerbung_router,
)
from config import database_url
from config.settings import allowed_origins, demo_session_timeout_s, say_max_chars
from database import ActivitySession, dispose_engine, get_sessionmaker, init_engine
from pipeline import run_pipeline
from pipeline.factory import ACTIVE_LESSONS, ACTIVE_TASKS, lesson_language, validate_lesson_languages
from satz import router as satz_router, sync_curated_content
from satz.examiner import transcribe_attempt
from security import (
    client_ip,
    demo_release,
    demo_try_admit,
    learn_release,
    learn_try_admit,
    origin_allowed,
    say_try_admit,
    say_user_try_admit,
)

# AGENT-001: the TAND-003 hardcoded-German assert that used to live here is
# gone. satz/examiner.py::transcribe_attempt no longer bakes German into its
# Deepgram URL — /tandem/say-audio now derives the language per call from
# ACTIVE_LESSONS (the live session's own lesson_id, via lesson_language()),
# so the route serves both the German tandem partners and Clara's English
# teacher room correctly without a static cross-check to keep in sync.

# The session JWT rides as ``?token=`` on the WS handshake URL (see
# ws_endpoint below); uvicorn's WebSocket access logging writes the full
# path+query to stdout, which would otherwise leak a 7-day impersonation
# token into Railway logs. Redact it before it's ever formatted.
_TOKEN_LOG_RE = re.compile(r"(token=)[^&\s\"']+")


class _RedactTokenFilter(logging.Filter):
    """Strip ``token=...`` (and ``ticket=...``) from WS access-log lines so the
    session JWT never lands in stdout / Railway logs."""
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
            if "token=" in msg or "ticket=" in msg:
                record.msg = _TOKEN_LOG_RE.sub(r"\1REDACTED", msg)
                record.args = ()
        except Exception:
            pass
        return True


for _ln in ("uvicorn.error", "uvicorn.access"):
    logging.getLogger(_ln).addFilter(_RedactTokenFilter())


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
    # And for the grammar-exercise catalogs (GRAM-002, Exercises A/B/D) — same
    # rule: malformed drill content aborts startup, not a mid-practice 500.
    load_bauteil_items()
    load_sprechen_tasks()
    load_verbindungen_items()
    load_zeitfaerbung_items()
    load_faelle_items()
    load_satzbau_items()
    # And for Artikel-Anker's gender rules + noun catalog — the items loader
    # cross-checks every curated noun against the ending classifier, so a
    # mistagged rule/trap aborts startup here, not mid-drag.
    load_genus_rules()
    load_genus_items()
    load_genus_exceptions()
    # And for Szenario-Sparring's scenario catalog (P1, thin slice) — same
    # fail-loud rule as the grammar-exercise catalogs above.
    load_szenario_scenarios()
    # And for Briefkasten's letter seeds — same rule again. A seed is only a
    # situation (the German is written per request), so what's validated here
    # is the shape the writer and both judges depend on.
    load_briefkasten_seeds()
    yield
    # Graceful drain: let in-flight pipelines finalize their DB rows before the
    # engine is disposed (a redeploy mid-session otherwise orphans them).
    for _ in range(20):  # up to ~10s
        if not ACTIVE_TASKS:
            break
        await asyncio.sleep(0.5)
    await dispose_engine()


app = FastAPI(lifespan=lifespan)

# The /say, /auth, /sessions, and /satz HTTP endpoints are CORS-checked
# (WebSockets aren't). Origins come from ALLOWED_ORIGINS (config.settings) so
# prod sets its real frontend origin without a code edit; defaults to
# localhost:3000 for dev.
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    # CHORE-001: enumerated from the actual route decorators across main.py +
    # every router instead of "*". PUT joined with LEVEL-001 (PUT /auth/level);
    # a verb missing here fails the browser's preflight with a 400 and the
    # frontend only sees a thrown fetch — the modal read "Couldn't save that"
    # in prod 2026-08-15 while the DB and the route were both fine.
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)

# Google sign-in + session-JWT routes (AUTH-001).
app.include_router(auth_router)
# Satzschmiede packs/pool/deck routes (SATZ-002).
app.include_router(satz_router)
# Grammar-exercise routes (GRAM-002): Bauteil-Sätze (A), Sprechen (B),
# Verbformen (C — deck auto-feeds from the Satzschmiede pool, schedule is
# drill-local on the user_verbformen overlay), Feste Verbindungen (D).
app.include_router(bauteil_router)
app.include_router(sprechen_router)
app.include_router(verbformen_router)
app.include_router(verbindungen_router)
# Zeitfärbung (GRAM-003): war/wurde/blieb by meaning — deterministic grading,
# no judge LLM.
app.include_router(zeitfaerbung_router)
# Fälle (GRAM-006 Proposal-1, "the case cluster"): six case-decision
# patterns drilled interleaved so no single pattern lets the learner coast.
app.include_router(faelle_router)
# Satzbau: five clause-construction patterns (relative clauses, indirect
# questions, zu-infinitives, um-zu/damit purpose clauses, question word
# order) — order chips into the correct clause, drilled interleaved same as
# Fälle.
app.include_router(satzbau_router)
# Artikel-Anker: noun gender via ending anchors — drag der/die/das onto the
# word, then produce the carrier phrase. Deterministic grading, no judge LLM.
app.include_router(genus_router)
# Szenario-Sparring (P1, thin slice): in-character question, one spoken
# answer, structure-only judge + silent grammar-ledger credit.
app.include_router(szenario_router)
# Briefkasten: a letter arrives, the learner writes back — hints on the first
# draft, corrections + score on the revision. The one written production mode.
app.include_router(briefkasten_router)
# Idiom (IDIOM-002 Proposal-1): the on-demand "how would a German say this?"
# rephrase — one judge call per tap, null when their German already sounds
# German. Writes nothing; every surface's own verdict stays untouched.
app.include_router(idiom_router)
# Interview (INTV-003 slice 2): the personal audio-pool viewer + two-round
# "listen & retell" / "read & answer" exercise, ported from the local-only
# workbench (interview_local/). The one persistence it does is a grammar-
# ledger write from round 2's answer, via the shared harvester.
app.include_router(interview_router)
# Cross-drill practice stats (DATA-004): GET /me/stats.
app.include_router(stats_router)
# Clara's interactive-exercise loop (AGENT-00X, backend half): one random
# item per taxonomy pattern, normalized across five typed-answer drills
# (faelle/satzbau/zeitfaerbung/verbindungen/bauteil) into one generic card,
# graded with each drill's own checks. Writes nothing to any learning-state
# table — see teacher/routes.py's loud comment on POST /exercise/attempts.
app.include_router(teacher_router)
# Stripe billing (PAY-001): Checkout, the billing portal, and the webhook
# that keeps users.tier + the local subscriptions mirror in sync. Fail-soft —
# every route 503s until STRIPE_* env is set (payments/stripe_sync.py).
app.include_router(payments_router)


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
        # error_eval is surfaced ONLY for tandem lessons (the debrief the
        # TandemDebriefModal renders — any partner). Drill sessions also store an
        # error_eval (the silent grammar harvest), but feedback separation is the
        # point — never hand it back to a drill client, which would let it leak
        # into the drill summary modal.
        "error_eval": row.error_eval if row.lesson_id in tandem_lesson_ids() else None,
    }


@app.get("/tandem/topics")
def tandem_topics(lesson: str = "tandem"):
    """Conversation-topic suggestions for the Grammatik-Tandem topic screen
    (TANDEM-001). Each partner has their own pool (TAND-008) — ``lesson``
    picks it, defaulting to Lena's. Public, non-sensitive content — same as
    ``/lessons/{id}``."""
    if lesson not in tandem_lesson_ids():
        raise HTTPException(status_code=404, detail="unknown tandem lesson")
    return {"topics": load_tandem_topics(lesson)}


@app.websocket("/ws/{user_id}")
async def ws_endpoint(
    websocket: WebSocket,
    user_id: str,
    voice: str = "happy_harry",
    lesson: str = "lesson_zero",
    topic: str = "",
    token: str = "",
    exchanges: str = "",
):
    """Authenticated learn socket (AUTH-001, P-3, E1).

    Browsers can't set custom headers on a WebSocket handshake, so the session
    JWT rides as a ``?token=`` query param. We verify it and close *before*
    accept on a bad/absent token (1008 policy) so the browser's onError fires
    cleanly. The token's subject is authoritative for identity — the path
    ``user_id`` is ignored, so a client can't drive an arbitrary id by editing
    the URL.

    Google sign-in is free/instant, so a signed-in user isn't automatically a
    trusted one: without a cap one account could open unlimited concurrent
    full pipelines (continuous STT+LLM+TTS), an uncapped cost-DoS on the same
    backend the demo caps protect. `learn_try_admit` gates admission per
    `sub` (unspoofable, unlike IP) before accept (1013 try-again on reject);
    a successful admit owns one concurrency slot, released in ``finally`` no
    matter how the session ends.

    ``exchanges`` (TAND-012) is the frontend's per-session exchange-cap picker
    for the tandem partners — parsed defensively (a bad/missing value just
    falls through to the lesson's own YAML cap) and whitelisted to
    ``{5, 10, 15}`` here, so only those three values ever reach the pipeline;
    ``run_pipeline`` re-gates it again on the lesson actually being tandem.
    """
    try:
        sub = decode_session_jwt(token)
    except AuthError:
        await websocket.close(code=1008)
        return
    ok, _reason = learn_try_admit(sub)
    if not ok:
        await websocket.close(code=1013)
        return
    try:
        n = int(exchanges)
    except ValueError:
        n = 0
    exchanges_n = n if n in (5, 10, 15) else None
    try:
        await websocket.accept()
        # `topic` is the tandem conversation theme (ignored by non-tandem lessons).
        await run_pipeline(websocket, sub, voice, lesson, topic=topic, exchanges=exchanges_n)
    finally:
        learn_release(sub)


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


# /say in-flight guard (orphan-trace audit, 2026-08-21): a parallel
# test-runner violation once fired
# 21 concurrent /say calls for the same user_id, each queuing its own
# LLMContextFrame before the prior turn's LLM call had finished — 21
# concurrent `llm` spans on one trace. `task.queue_frame()` returns as soon
# as the frame is enqueued, well before the turn's LLM/TTS work completes, so
# "still processing" isn't observable from here without wiring a completion
# signal back from ClientWrapper — out of scope for this guard. This is the
# minimal version instead: `_say_locks` serializes two /say calls that land
# for the same user practically simultaneously (no `await` happens between
# the `.locked()` check and `.acquire()` below, so this is race-free on the
# single-threaded event loop), and `_say_last_queued_at` rejects a second
# call that lands within `_SAY_INFLIGHT_MIN_GAP_S` of the first — a
# heuristic backstop for the common case where the first turn is still being
# generated when the next call arrives, after the lock has already been
# released. Both dicts are keyed by `target_id` (the resolved id, post-auth)
# and, unlike ACTIVE_TASKS/ACTIVE_LESSONS in pipeline/factory.py, are never
# popped — they grow with the set of distinct users who have ever called
# /say, not just the ones currently connected. Accepted for this minimal
# version: the population is bounded by real signed-in/demo users, not
# request volume, and per-user cleanup would need the same identity-guard
# machinery ACTIVE_TASKS already carries for a map this small to be worth it.
_SAY_INFLIGHT_MIN_GAP_S = 1.0
_say_locks: dict[str, asyncio.Lock] = {}
_say_last_queued_at: dict[str, float] = {}


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
      Also rate-limited (E1), same as the demo branch — an authenticated
      caller can otherwise hammer /say for free.

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
        if not say_user_try_admit(sub):
            raise HTTPException(status_code=429, detail="too many requests")
        target_id = sub

    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is empty")
    if len(text) > say_max_chars:
        raise HTTPException(status_code=413, detail="text too long")
    task = ACTIVE_TASKS.get(target_id)
    if task is None:
        raise HTTPException(status_code=404, detail="No active session for that user_id")

    lock = _say_locks.setdefault(target_id, asyncio.Lock())
    if lock.locked():
        raise HTTPException(
            status_code=409, detail="a previous message for this user is still being processed"
        )
    async with lock:
        now = time.monotonic()
        last_queued = _say_last_queued_at.get(target_id)
        if last_queued is not None and (now - last_queued) < _SAY_INFLIGHT_MIN_GAP_S:
            raise HTTPException(
                status_code=409,
                detail="a previous message for this user is still being processed",
            )
        _say_last_queued_at[target_id] = now

        context = LLMContext([{"role": "user", "content": text}])
        await task.queue_frame(LLMContextFrame(context=context))
    return {"ok": True}


# Practice-mode (TAND-003): a whole recorded utterance is comfortably under a
# minute of opus/aac at typical bitrates — same cap style/order-of-magnitude
# as sprechen/routes.py's _MAX_AUDIO_BYTES, sized up for the 90s recorder cap
# (frontend/src/components/shared/recorder.ts) instead of Sprechen's shorter one.
_TANDEM_AUDIO_MAX_BYTES = 4_000_000


@app.post("/tandem/say-audio")
async def tandem_say_audio(
    audio: UploadFile = File(...),
    user_id: str = Depends(get_current_user_id),
):
    """Practice-mode counterpart to ``/say`` (TAND-003): the learner records a
    whole utterance at their own pace (tap record -> speak -> tap stop) instead
    of using the streaming-VAD Natural mode. The browser uploads the finished
    clip here; we transcribe it and inject the transcript into the SAME live
    pipeline ``/say/{user_id}`` feeds — same ``LLMContextFrame`` mechanism, so
    exchange counting, goodbye detection, and the tandem debrief (all hanging
    off ``ClientWrapper.astream``) keep working exactly as they do for a typed
    or spoken-streaming turn.

    Auth is ``get_current_user_id`` (the session JWT), same dependency every
    other authenticated HTTP route in this codebase uses. The JWT subject IS
    the ``ACTIVE_TASKS`` lookup key — there is no path/body user id to trust
    or spoof, unlike ``/say``'s two-branch dance (that route also serves the
    token-free public demo, which this one never does). Same per-user rate
    gate as ``/say``'s authenticated branch (``say_user_try_admit``) — without
    it a scripted caller could hammer Deepgram + the LLM for free.

    Transcription reuses ``satz/examiner.py::transcribe_attempt`` (Deepgram's
    prerecorded REST endpoint, nova-3) — same model ``services/stt.py``'s
    streaming STT uses. AGENT-001: the language is no longer hardcoded German
    inside that helper. This route derives it from ``ACTIVE_LESSONS``, the
    same live session ``ACTIVE_TASKS`` looked up two lines below, via
    ``lesson_language()`` — so a German tandem session still transcribes as
    German and Clara's English teacher room now transcribes as English,
    both server-side and both from the session that's actually live, never
    from client input. No new HTTP client/SDK needed: aiohttp is the
    established pattern for one-shot Deepgram calls in this codebase
    (satz/examiner.py, also used by sprechen/szenario/verbformen).
    """
    if not say_user_try_admit(user_id):
        raise HTTPException(status_code=429, detail="too many requests")

    task = ACTIVE_TASKS.get(user_id)
    if task is None:
        raise HTTPException(
            status_code=404,
            detail="No active session — start or resume the conversation first.",
        )

    data = await audio.read()
    if not data:
        raise HTTPException(status_code=422, detail="We didn't get any audio — try again.")
    if len(data) > _TANDEM_AUDIO_MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail="That recording is too long — try a shorter turn.",
        )

    # AGENT-001: derived from the SAME live session looked up above, not from
    # anything the client sent — Clara's teacher room runs English STT/TTS
    # (pipeline/factory.py::ENGLISH_LESSONS) while every tandem partner stays
    # German. "tandem" fallback only fires if ACTIVE_LESSONS and ACTIVE_TASKS
    # ever disagree, which preserves today's (German) behavior for that edge.
    language = lesson_language(ACTIVE_LESSONS.get(user_id, "tandem"))
    # COST-001: without a root span here, the `stt` generation inside
    # transcribe_attempt is a PARENTLESS span with no `user.id`, which is
    # exactly what agents/observability.py::_OrphanFragmentFilterExporter
    # drops before export — so every Practice-mode clip was billed by
    # Deepgram but invisible in Langfuse (found reconciling 2026-08-24:
    # 69 billed requests vs 63 traced, Δ = the 6 clips of one tandem
    # session). Same live-session join as idiom/routes.py: the trace files
    # into the live tandem/teacher session, and the cost stamp inside
    # transcribe_attempt rides along.
    session_id = get_trace_session(user_id)
    with propagate_trace_context(user_id=user_id, session_id=session_id), tracer.start_as_current_span(
        "tandem-say-audio"
    ) as span:
        span.set_attribute("user.id", user_id)
        if session_id:
            span.set_attribute("langfuse.session.id", session_id)
        try:
            transcript = await transcribe_attempt(data, audio.content_type, language=language)
        except Exception as e:  # noqa: BLE001 — a Deepgram outage must 502, not 500
            logger.warning(f"Tandem say-audio transcription failed: {type(e).__name__}: {e}")
            raise HTTPException(
                status_code=502,
                detail="Couldn't process the audio — try again in a moment.",
            )

    text = (transcript or "").strip()
    if not text:
        raise HTTPException(
            status_code=422,
            detail="We couldn't hear anything — try again a bit closer to the mic.",
        )

    context = LLMContext([{"role": "user", "content": text}])
    await task.queue_frame(LLMContextFrame(context=context))
    return {"transcript": text}
