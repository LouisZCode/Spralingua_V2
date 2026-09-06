# Backend:  uvicorn main:app --host 0.0.0.0 --port 8765
# Frontend: cd frontend && npm run dev

import asyncio
import logging
import re
import signal
import time
import uuid
from contextlib import asynccontextmanager

import sentry_sdk
from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    HTTPException,
    Request,
    UploadFile,
    WebSocket,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
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
from config.settings import (
    allowed_origins,
    app_env,
    demo_session_timeout_s,
    learned_session_timeout_s,
    say_max_chars,
    sentry_dsn,
    sentry_environment,
    sentry_release,
)
from database import ActivitySession, dispose_engine, get_sessionmaker, init_engine
from database.repository import _as_utc_iso
from pipeline import run_pipeline
from pipeline.factory import (
    ACTIVE_LESSONS,
    ACTIVE_TASKS,
    ACTIVE_WRAPPERS,
    is_draining,
    lesson_language,
    validate_lesson_languages,
)
from recordings.service import schedule_recording
from satz import router as satz_router, sync_curated_content
from satz.examiner import transcribe_attempt
from security import (
    client_ip,
    demo_admission_reason,
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


# SEC-005: ALLOWED_ORIGINS defaulted to localhost with no startup check — a
# deployed env that forgot to set it (or set it to an empty string) would
# boot cleanly and then reject every browser request from the real
# frontend origin, with CORSMiddleware's silent same-origin-only behavior
# the only symptom. `app_env` is the same prod/dev signal
# `auth/tokens.py`'s JWT_SECRET check already uses: "dev" (the default)
# preserves local ergonomics, anything else is a deployed environment.
_DEFAULT_ALLOWED_ORIGINS = ["http://localhost:3000"]


def _check_allowed_origins() -> None:
    """Fail loud in a deployed env when ALLOWED_ORIGINS is empty or still
    the localhost dev default; warn (don't block boot) in dev."""
    if allowed_origins and allowed_origins != _DEFAULT_ALLOWED_ORIGINS:
        return
    reason = "empty" if not allowed_origins else "still the localhost dev default"
    msg = (
        f"ALLOWED_ORIGINS is {reason} ({allowed_origins!r}) with APP_ENV={app_env!r}. "
        "Every browser request from the real frontend origin will be rejected by "
        "CORS. Set ALLOWED_ORIGINS to the real frontend origin(s) before deploying."
    )
    if app_env == "dev":
        logger.warning(msg)
        return
    raise RuntimeError(msg)


# REL-002 (code half): let a live voice session end gracefully on Railway's
# deploy SIGTERM instead of dying mid-sentence.
#
# The problem: Railway sends SIGTERM to the OLD container's process group on
# every redeploy. uvicorn's own `Server.capture_signals()` installs a
# Python-level SIGTERM handler (`Server.handle_exit`, a plain
# `signal.signal(sig, self.handle_exit)` call made *inside* `Server.serve()`,
# BEFORE this app's lifespan startup ever runs) that, on receipt, flips
# `should_exit`, which — per Starlette/uvicorn's WebSocket protocol
# implementation — closes every open socket with a 1012 close frame almost
# immediately. The lifespan's OWN "wait up to ~10s for ACTIVE_TASKS" tail
# (below the `yield` further down) runs only AFTER that has already
# happened, so it only ever protected the DB finalize step, never the
# conversation itself.
#
# The fix intercepts SIGTERM one level higher, in asyncio, ahead of
# uvicorn's own handler:
#   1. `signal.getsignal(SIGTERM)` captures whatever uvicorn already
#      installed (`Server.handle_exit`, bound method) BEFORE we touch
#      anything — this is the handoff target once draining is done.
#   2. `loop.add_signal_handler(SIGTERM, ...)` REPLACES the Python-level
#      handler uvicorn just installed (asyncio's signal handling is not
#      additive — it takes over the slot). Only the main thread's running
#      loop can do this, hence the try/except below.
#   3. On the FIRST SIGTERM, our handler schedules
#      `pipeline.factory.begin_drain()`, which (a) flips `DRAINING` —
#      read by `GET /health` (503 `{"status": "draining"}`) and by both WS
#      accept paths below (close 1013 right after `accept()`, before any
#      DB insert / coin gate / Clara talk-count tally) — and (b) injects
#      one graceful wrap-up turn into every session in `ACTIVE_TASKS`
#      (`ClientWrapper.request_wrap_up()` + the same
#      `LLMContext`/`LLMContextFrame` mechanism `/say` and the kickoff
#      turn use). That reuses the EXACT end-of-session machinery an
#      agent-driven goodbye already uses — `stop_when_done()` -> graceful
#      `EndFrame` -> normal WS close -> `activity_session.ended_by =
#      "agent"` -> the frontend's normal SessionSummaryModal — so nothing
#      downstream of "a session ended" needs to learn a new code path.
#   4. `begin_drain()` waits (bounded by `SHUTDOWN_DRAIN_S`, polling every
#      0.5s) for `ACTIVE_TASKS` to empty out, or gives up at the deadline.
#      Either way, we then call the ORIGINAL uvicorn handler captured in
#      step 1 — `original(signal.SIGTERM, None)`, matching
#      `Server.handle_exit(self, sig, frame)`'s signature — so uvicorn's
#      normal shutdown proceeds exactly as it does today with zero live
#      sessions: any sockets still open get uvicorn's own 1012, then this
#      very lifespan's existing post-`yield` tail runs.
#   5. A second SIGTERM landing while a drain is already in flight just
#      logs and returns — it does NOT start a second drain, and (unlike
#      uvicorn's own stock behavior) does NOT escalate to a forced exit;
#      the bounded deadline in step 4 is what caps worst-case shutdown
#      time instead.
#
# Operator note (the developer's own step, not this code's): Railway's
# `RAILWAY_DEPLOYMENT_DRAINING_SECONDS` dashboard setting must exceed
# `SHUTDOWN_DRAIN_S` (config/settings.py) by a healthy margin — roughly
# +15s — so the post-session evaluators (goal / grammar / pronunciation,
# `asyncio.gather`ed under `post-session-analysis` in
# `pipeline/factory.py`'s disconnect `finally:` block) still have time to
# run AFTER the wrap-up turn's `EndFrame` closes the pipeline, or Railway
# force-kills the container mid-evaluator.
#
# SIGINT is untouched — only SIGTERM is intercepted here.
def _install_sigterm_drain_handler() -> None:
    try:
        original_handler = signal.getsignal(signal.SIGTERM)
    except (ValueError, OSError) as e:
        # getsignal() itself can fail off the main thread.
        logger.warning(
            f"SIGTERM drain handler not installed (getsignal failed): "
            f"{type(e).__name__}: {e} — keeping default uvicorn shutdown behavior"
        )
        return

    loop = asyncio.get_running_loop()
    state = {"draining": False}

    def _on_sigterm() -> None:
        if state["draining"]:
            logger.info("SIGTERM received again while already draining — ignoring")
            return
        state["draining"] = True
        logger.info("SIGTERM received — starting graceful drain before shutdown")

        async def _drain_then_handoff() -> None:
            try:
                from pipeline.factory import begin_drain

                await begin_drain()
            except Exception:  # noqa: BLE001 — draining must never block shutdown
                logger.exception(
                    "Graceful drain failed (non-fatal) — handing off to uvicorn's "
                    "own shutdown anyway"
                )
            finally:
                logger.info("Drain complete — handing off to uvicorn's own SIGTERM handler")
                if callable(original_handler):
                    try:
                        original_handler(signal.SIGTERM, None)
                    except Exception:  # noqa: BLE001 — a stranded process is worse than a log line
                        logger.exception(
                            "uvicorn's SIGTERM handler raised during handoff — "
                            "the process may not exit until Railway's SIGKILL"
                        )
                else:
                    # Defensive only: in every real deployment uvicorn installs a
                    # real bound-method handler here, so this branch should be
                    # unreachable in practice. Nothing sane to hand off to.
                    logger.error(
                        f"No callable original SIGTERM handler to hand off to "
                        f"(was {original_handler!r}) — process may not exit cleanly"
                    )

        drain_task = asyncio.create_task(_drain_then_handoff())
        # BUG-006 pattern: nothing awaits this task, so without a
        # done-callback an exception would vanish into asyncio's default
        # handler instead of the log.
        drain_task.add_done_callback(
            lambda t: t.cancelled()
            or t.exception() is None
            or logger.error(f"SIGTERM drain task failed unexpectedly: {t.exception()!r}")
        )

    try:
        loop.add_signal_handler(signal.SIGTERM, _on_sigterm)
        logger.info("SIGTERM graceful-drain handler installed")
    except (NotImplementedError, RuntimeError, ValueError) as e:
        # NotImplementedError: unsupported platform (Windows). RuntimeError:
        # not the main thread, or no running loop the way we expect (an
        # in-process ASGI test client can hit this). ValueError: bad signal
        # number (shouldn't happen for a hardcoded SIGTERM, kept defensive).
        # Either way: log and keep uvicorn's own default SIGTERM behavior —
        # this is a best-effort upgrade, never a hard boot requirement.
        logger.warning(
            f"SIGTERM drain handler not installed ({type(e).__name__}: {e}) — "
            "keeping default uvicorn shutdown behavior"
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    # REL-002: installed first — no dependency on Postgres or anything else
    # below, and every second before Railway's SIGTERM can arrive is a
    # second closer to a container recycling out from under a live session.
    _install_sigterm_drain_handler()
    # Ordered next and deliberately ahead of init_engine below: it needs no
    # Postgres connection, so a misconfigured ALLOWED_ORIGINS fails loud (or
    # warns, in dev) before spending time on anything that does.
    _check_allowed_origins()
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
    # Belt and braces for the DB finalize: ACTIVE_TASKS empties early in each
    # session's disconnect block (before its evaluators and activity_session
    # UPDATE), and uvicorn already waits for the WebSocket handler tasks that
    # run that finalize before it reaches this lifespan tail — so on the
    # normal path (and the REL-002 drain path, which hands off to uvicorn)
    # this loop breaks at once. It still guards the case where a pipeline
    # is registered but its handler is not among uvicorn's tracked tasks.
    for _ in range(20):  # up to ~10s
        if not ACTIVE_TASKS:
            break
        await asyncio.sleep(0.5)
    await dispose_engine()


# Sentry error reporting (OBS-013), env-gated and inert by default. Must run
# before FastAPI(...) is constructed so its Starlette/FastAPI integration
# can instrument the app. Errors only — traces_sample_rate=0.0 because
# Langfuse already owns latency (see CLAUDE.md's Langfuse section), and
# send_default_pii=False keeps request bodies/headers/user data out of a
# third-party SDK by default. When SENTRY_DSN is unset nothing below runs
# and the app boots exactly as it did before this feature existed.
if sentry_dsn:
    sentry_sdk.init(
        dsn=sentry_dsn,
        environment=sentry_environment,
        send_default_pii=False,
        traces_sample_rate=0.0,
        release=sentry_release,
    )
    logger.info(f"Sentry error reporting: ON (environment={sentry_environment!r})")
else:
    logger.info("Sentry error reporting: OFF (SENTRY_DSN not set)")


# SEC-005: /docs, /redoc and the raw /openapi.json schema stay on in dev
# (APP_ENV=dev, the default) but are switched off in any deployed
# environment — an unauthenticated, world-readable map of every route,
# request/response shape and auth scheme is free recon for an attacker and
# serves no learner-facing purpose.
_DEV = app_env == "dev"
app = FastAPI(
    lifespan=lifespan,
    docs_url="/docs" if _DEV else None,
    redoc_url="/redoc" if _DEV else None,
    openapi_url="/openapi.json" if _DEV else None,
)

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


def _cors_headers_for(request: Request) -> dict[str, str]:
    """Echo the ``Access-Control-Allow-Origin`` header CORSMiddleware would
    have added, for use by the catch-all exception handler below.

    Starlette/FastAPI special-case an ``Exception``-keyed handler: it's
    pulled out of ``ExceptionMiddleware`` and installed on
    ``ServerErrorMiddleware`` instead, which sits OUTSIDE ``CORSMiddleware``
    in the stack (see ``fastapi.applications.FastAPI.build_middleware_stack``
    — ``if key in (500, Exception): error_handler = value``). A response
    built by that handler is sent directly, bypassing CORSMiddleware's
    header injection entirely, so a browser ``fetch()`` from an allowed
    origin would see an opaque CORS failure instead of the 500 body. This
    mirrors ``CORSMiddleware.send``'s "simple response" origin-echo branch
    (the only branch this app's config ever takes — ``allow_origins`` here
    is never ``["*"]`` and ``allow_credentials`` is never set).
    """
    origin = request.headers.get("origin")
    if origin and origin in allowed_origins:
        return {"Access-Control-Allow-Origin": origin, "Vary": "Origin"}
    return {}


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """OBS-013: last-resort safety net for an unhandled exception in any route.

    Without this, an unhandled exception was a bare 500 plus one stdout line
    with nothing watching it. Logs the full traceback with method + path +
    the caller's user id (best-effort — this app has no ``request.state``
    attachment for the current user; ``Depends(get_current_user_id)``/
    ``_bearer_subject`` resolve it per-route instead, so we reuse the same
    Authorization-header decode ``_bearer_subject`` already uses elsewhere)
    and returns a generic JSON 500 that never leaks internals to the client.

    ``HTTPException`` is untouched: FastAPI registers its own handler for
    that exact class, and Starlette's handler lookup walks the raised
    exception's MRO from most to least specific, so an ``HTTPException``
    always matches its own handler before ever falling through to this one.

    Sentry: Starlette hoists the ``Exception``-keyed handler into
    ``ServerErrorMiddleware``, which sentry-sdk's Starlette patch does not
    instrument, so the event would otherwise arrive only via the auto-enabled
    LoguruIntegration hooking the log call below. The explicit
    ``capture_exception`` makes that independent of loguru; the SDK's
    DedupeIntegration drops the duplicate (same exception object).
    """
    user_id = None
    try:
        user_id = _bearer_subject(request)
    except Exception:
        pass
    logger.opt(exception=exc).error(
        f"Unhandled exception on {request.method} {request.url.path} (user={user_id!r})"
    )
    if sentry_dsn:
        sentry_sdk.capture_exception(exc)
    return JSONResponse(
        status_code=500,
        content={"detail": "internal error"},
        headers=_cors_headers_for(request),
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
# Coins (PAY-002): balance + timezone.
from coins.routes import router as coins_router  # noqa: E402 — after payments, avoids circular import

app.include_router(coins_router)
# Grammar-pattern explanation bank (GRAM-009): GET /grammar/pattern/{id} —
# the same curated pair/point/test Clara's kickoff draws from
# (grammar/explanations.yaml), surfaced to every typed drill's wrong-answer
# card and the /development focus cards. Free (no coin gate), auth-only.
# Imported directly from the submodule, not re-exported by grammar/__init__.py
# — see that file's GRAM-009 comment for why (auth.routes imports FROM
# grammar, so grammar/__init__.py eagerly importing grammar.routes, which
# needs auth.deps, would cycle).
from grammar.routes import router as grammar_router  # noqa: E402 — after auth, avoids circular import

app.include_router(grammar_router)


@app.get("/health")
async def health():
    """Readiness check (INFRA-003): verifies Postgres is still reachable.

    Startup is fail-loud (lifespan), but the DB can die *after* startup —
    session writes are non-fatal by design, so nothing else would surface
    it. Returning 503 here lets Railway's healthcheck stop routing to an
    instance that has silently stopped persisting.

    REL-002: also 503s once a SIGTERM drain has started — Railway (or any
    load balancer polling this route) should stop sending new traffic to an
    instance that's already shutting down, same intent as the DB check
    above but checked first since it's free (no DB round trip).
    """
    if is_draining():
        return JSONResponse(status_code=503, content={"status": "draining"})
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


@app.get("/sessions/active")
async def session_active(request: Request):
    """SESS-001: is a live voice session already running for this account?

    The client-side half of the one-session gate: entry flows call this
    right before opening the WebSocket and show a clear "finish your
    other session first" panel instead of connecting into a 4004. Reads
    the in-process registry only — no DB.

    Registered BEFORE ``/sessions/{session_id}`` below: Starlette matches
    routes in registration order, and ``{session_id}`` happily captures the
    literal string "active" — declaring this route second would make it
    permanently unreachable, silently falling through to ``get_session``
    with ``session_id="active"`` instead.
    """
    sub = _bearer_subject(request)
    if sub is None:
        raise HTTPException(status_code=401, detail="missing or invalid token")
    return {
        "active": sub in ACTIVE_TASKS,
        "lesson": ACTIVE_LESSONS.get(sub),
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
        "started_at": _as_utc_iso(row.started_at) if row.started_at else None,
        "ended_at": _as_utc_iso(row.ended_at) if row.ended_at else None,
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
    pattern: str = "",
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

    ``pattern`` (cold-start slice) is Clara's topic screen forwarding the
    taxonomy pattern id of whichever focus/starter card the learner picked.
    Unlike ``exchanges`` there's no fixed value set to whitelist against here
    (no taxonomy import in this module) — it rides through as an opaque
    string, and ``run_pipeline`` does the real work: re-gating it to
    ``type: teacher`` sessions and validating it against the taxonomy's known
    ids, silently ignoring anything else (wrong lesson type, unknown id,
    absent).
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
        # REL-002: reject a new connect started during a SIGTERM drain,
        # right after accept() and BEFORE run_pipeline does any DB insert /
        # coin gate / Clara talk-count tally — mirrors where the 4001-4004
        # rejections below (inside run_pipeline) already sit, just one gate
        # earlier so nothing is ever charged/counted for a connect that's
        # about to be told to retry elsewhere.
        if is_draining():
            logger.info(f"drain: rejecting new connect for user {sub} (1013)")
            await websocket.close(code=1013, reason="Server is draining — try again shortly")
            return
        # `topic` is the tandem conversation theme (ignored by non-tandem lessons).
        await run_pipeline(
            websocket, sub, voice, lesson, topic=topic,
            exchanges=exchanges_n, pattern=pattern or None,
            # MEMORY-002: same watchdog the demo route always had — without it
            # an abandoned tab kept a zombie pipeline (and its growing audio
            # buffers) alive for as long as the half-open socket lasted.
            session_timeout_s=learned_session_timeout_s,
        )
    finally:
        learn_release(sub)


# Demo user ids are minted client-side as `demo-<uuid4>`; pin the shape so the
# public route can't be driven with arbitrary ids.
_DEMO_USER_ID_RE = re.compile(r"^demo-[A-Za-z0-9_-]{1,64}$")

# REL-001 follow-up (P2-IMPL): the demo's anonymous visitor token, minted
# client-side (frontend/src/lib/demoVisitor.ts::getDemoVisitorId, a
# crypto.randomUUID() — 36 chars — persisted in localStorage) and replayed on
# every demo connect as `?visitor=`. Deliberately lenient: an invalid or
# missing token degrades to `None`, never a rejection — a cached older
# frontend bundle mid-deploy (no `?visitor=` at all) must still connect.
_DEMO_VISITOR_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


@app.get("/demo/status")
async def demo_status(request: Request):
    """Public, unauthenticated pre-flight for the front-page demo (PRODUCT-017).

    The demo websocket rejects an inadmissible visitor by closing *before*
    ``accept()`` with a WS code (1008 policy / 1013 try-again) — the docstring
    on ``ws_demo_endpoint`` below used to claim this reaches the browser as
    that code, but it doesn't: uvicorn's websockets implementation turns a
    close-before-accept into an HTTP 403 handshake rejection (see
    ``asgi_send`` in ``uvicorn/protocols/websockets/websockets_impl.py``), and
    the browser's WebSocket API collapses ANY handshake failure — including
    that 403 — into a bare ``onerror``/``onclose(1006)`` with no code or
    reason ever exposed to JS. So the frontend has no way to tell "the server
    is draining" from "you're rate-limited" from "the network is down" by
    inspecting the failed connection. This route exists to give it that
    signal *before* it tries to connect at all, by re-running the exact same,
    side-effect-free admission check (`security.demo_admission_reason` —
    doesn't touch the rate-limit windows or concurrency counters) the socket
    itself will run.

    Always 200 (never 503) so the frontend can read the JSON body — a 503
    would be indistinguishable from "the backend itself is unreachable",
    which is the "offline" case this route needs to be distinguishable from.
    Draining (REL-002) takes priority over the demo-specific admission
    reasons since it's the same condition ``/health`` reports first and for
    the same reason (free, no DB round trip). No DB access, no coin gate —
    this must stay cheap since a bored visitor's frontend may poll it.
    """
    if is_draining():
        return {"status": "draining"}
    reason = demo_admission_reason(client_ip(request))
    status = {
        "ok": "ok",
        "server_busy": "busy",
        "too_many_concurrent": "rate_limited",
        "rate_limited": "rate_limited",
    }[reason]
    return {"status": status}


@app.websocket("/ws/demo/{user_id}")
async def ws_demo_endpoint(websocket: WebSocket, user_id: str):
    """Public, unauthenticated front-page demo socket (SEC-001).

    Hardened sibling of /ws/{user_id}: the lesson and voice are forced
    server-side (a visitor can't drive an arbitrary lesson or voice), the Origin
    header is checked (WebSocket handshakes bypass browser CORS), and global +
    per-IP concurrency/rate caps gate admission. Rejections before ``accept()``
    (1008 policy / 1013 try-again) do NOT reach the browser as that code —
    uvicorn turns a close-before-accept into an HTTP 403 handshake rejection,
    which the WebSocket API surfaces as an opaque ``onerror``/1006 with no
    code or reason (see ``GET /demo/status`` above, which exists precisely so
    the frontend can learn the real reason before it ever opens this socket).
    A successful admit owns one concurrency slot, released in ``finally`` no
    matter how the session ends.

    REL-001 follow-up (P2-IMPL): reads one optional query param, ``visitor``
    — the frontend's per-browser anonymous demo-visitor token (Proposal-2).
    Read directly off ``websocket.query_params`` rather than as a typed
    function parameter, on purpose: a bad or absent value must degrade to
    ``None`` and a debug log, never a rejection, so a visitor on a cached
    older frontend bundle (no ``?visitor=`` at all) still connects during a
    deploy overlap. Threaded into ``run_pipeline`` -> the ``activity_session``
    INSERT; ``/ws/{user_id}`` never passes one.
    """
    if not origin_allowed(websocket.headers.get("origin")):
        await websocket.close(code=1008)
        return
    if not _DEMO_USER_ID_RE.match(user_id):
        await websocket.close(code=1008)
        return
    raw_visitor = websocket.query_params.get("visitor")
    visitor_id = raw_visitor if raw_visitor and _DEMO_VISITOR_RE.match(raw_visitor) else None
    if raw_visitor and visitor_id is None:
        # Client-supplied string: truncate before it reaches the log.
        logger.debug(f"demo connect: ignoring malformed visitor token {raw_visitor[:80]!r}")
    ip = client_ip(websocket)
    ok, _reason = demo_try_admit(ip)
    if not ok:
        await websocket.close(code=1013)
        return
    try:
        await websocket.accept()
        # REL-002: same drain gate as /ws/{user_id} above.
        if is_draining():
            logger.info(f"drain: rejecting new demo connect for {user_id} (1013)")
            await websocket.close(code=1013, reason="Server is draining — try again shortly")
            return
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
            visitor_id=visitor_id,
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
    background_tasks: BackgroundTasks = None,
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

    REL-001 follow-up (P2-IMPL): once the transcript is non-empty AND the
    ``LLMContextFrame`` has actually reached the live pipeline — never for a
    404/422/502 that stored nothing — the clip is archived under its own
    ``ref_kind="session_turn"`` (one row per clip; the session MP3 keeps
    ``ref_kind="activity_session"``), linked to the bare session hex via
    ``get_trace_session`` and ordered by ``ClientWrapper.exchange_count``.
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
    active_lesson_id = ACTIVE_LESSONS.get(user_id, "tandem")
    language = lesson_language(active_lesson_id)
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

    # REL-001 follow-up (P2-IMPL): the clip has now reached the pipeline —
    # archive it. Surface mirrors pipeline/factory.py's own voice-session
    # surface map for these three lesson types.
    if active_lesson_id == "teacher":
        _clip_surface = "teacher"
    elif active_lesson_id in ("tandem", "tandem_paul"):
        _clip_surface = "tandem"
    else:
        _clip_surface = "lesson"
    if session_id is None:
        logger.warning(
            f"Tandem say-audio: no trace session for user {user_id} — clip not archived"
        )
    else:
        # get_trace_session returns the surface-prefixed trace id
        # (tandem-<hex> / teacher-<hex> / lesson-<hex>) — the bare session
        # hex (activity_session.id) is everything after the first "-".
        # `partition` rather than `split(...)[1]` so an unprefixed id can
        # never raise here, after the turn has already been queued.
        _prefix, _sep, bare_session_id = session_id.partition("-")
        if not _sep:
            bare_session_id = session_id
        wrapper = ACTIVE_WRAPPERS.get(user_id)
        # The turn just queued above will become exchange
        # `exchange_count + 1` — see ClientWrapper.exchange_count's docstring.
        # Known, accepted: unlike `/say` this route has no per-user in-flight
        # lock, so two clips posted faster than one LLM turn would read the
        # same count and share an `item_id` — a display-order cosmetic only
        # (each object still gets its own uuid key); the double-tap that
        # triggers it is not a real Practice-mode gesture.
        clip_item_id = str(wrapper.exchange_count + 1) if wrapper is not None else None
        schedule_recording(
            background_tasks,
            user_id=user_id,
            surface=_clip_surface,
            exercise=active_lesson_id,
            ref_kind="session_turn",
            ref_id=bare_session_id,
            item_id=clip_item_id,
            data=data,
            content_type=audio.content_type,
            transcript=text,
        )

    return {"transcript": text}
