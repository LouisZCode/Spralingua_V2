import asyncio
import resource
import wave
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import aiohttp
from loguru import logger
from pydub import AudioSegment
from sqlalchemy.exc import SQLAlchemyError

from services import stt_deepgram, tts_minimax, transport_fastapi_ws

# AGENT-001: same two frame-construction imports main.py's /say endpoint
# uses to inject a typed turn into a live pipeline. _kickoff_turn below
# reuses that exact mechanism to make the agent speak first on connect.
from pipecat.frames.frames import LLMContextFrame
from pipecat.processors.aggregators.llm_context import LLMContext

from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.pipeline.runner import PipelineRunner
from pipecat.processors.frameworks.langchain import LangchainProcessor
from pipecat.processors.frameworks.rtvi import (
    RTVIObserver,
    RTVIObserverParams,
    RTVIProcessor,
)
from pipecat.processors.audio.audio_buffer_processor import AudioBufferProcessor
from pipecat.processors.filters.stt_mute_filter import (
    STTMuteConfig,
    STTMuteFilter,
    STTMuteStrategy,
)

from .audio_meter import AudioSecondsMeter
from .converters import TranscriptionToContextConverter
from .observers import PipelineLatencyObserver
from .tts_duration import TTSDurationTracker

from agents import ClientWrapper, CONVERSATIONAL_MODEL
from agents.pipecat_wrapper import log_topic_freeform
from agents.audio_costs import (
    DEEPGRAM_NOVA3_STREAMING_PER_MIN,
    stamp_audio_cost,
    stt_cost_usd,
    tts_cost_usd,
)
from agents.evaluator import evaluate
from agents.error_extractor import extract_errors
from agents.debrief import debrief as run_debrief
from agents.load_goals import load_goal
from agents.load_prompts import list_lesson_ids, load_prompts, tandem_lesson_ids
from agents.load_pronunciation import load_pronunciation_locale
from agents.pronunciation import assess_pronunciation
from agents.observability import (
    attach_trace_context,
    clear_trace_session,
    detach_trace_context,
    flush_traces,
    register_trace_session,
    tracer,
)

from grammar import load_taxonomy

from database import create_session_row, finalize_session_row, get_sessionmaker
from database.repository import (
    complete_daily_mode,
    credit_pattern_success,
    load_grammar_focus,
    load_pattern_examples,
    load_tandem_notes,
    load_user_level,
    load_user_name,
    load_vocab_words,
    record_drill_attempt,
    record_grammar_error,
)

# Cold-start slice: curated starter topics for a teacher session whose
# ledger is empty (see teacher/starters.py). Imported here, not lazily,
# same as every other prompt-layer dependency above.
from teacher.starters import starters_for_level

# CLARA-16: the exercise registry's own coverage map (drill -> covered
# taxonomy pattern ids), used below to build Context.exercise_catalog for
# teacher sessions. Same top-level-import layering as teacher.starters above.
from teacher.registry import coverage as teacher_coverage

# MEMORY-003 (2026-09-01): one helper, no new dependency. Reads the current
# process RSS from Linux's /proc (production runs uvicorn in a Linux container
# under Railway's cgroup memory accounting, which is exactly what the billing
# graph measures); returns None off-Linux (local dev on macOS), where the log
# line simply omits the number. The point is attribution: the next memory
# anomaly should name the session that caused it, straight from the logs.
def _rss_mb() -> float | None:
    try:
        with open("/proc/self/statm") as f:
            resident_pages = int(f.read().split()[1])
        return resident_pages * resource.getpagesize() / (1024 * 1024)
    except Exception:  # noqa: BLE001 — diagnostics only, must never throw
        return None


def _rss_log_suffix() -> str:
    mb = _rss_mb()
    return f" rss={mb:.0f}MB" if mb is not None else ""


# Live pipeline tasks keyed by user_id. Used by /say/{user_id} in main.py
# to inject typed turns into an active session. Per-client isolation rule
# from CLAUDE.md still holds — this is just a lookup, not shared state.
ACTIVE_TASKS: dict[str, PipelineTask] = {}

# Lesson id running under each live session, keyed the same as ACTIVE_TASKS.
# AGENT-001: lets an HTTP route that injects into a live session (e.g.
# /tandem/say-audio) derive that session's runtime language server-side
# (via lesson_language() below) instead of assuming one language for every
# caller of this registry.
ACTIVE_LESSONS: dict[str, str] = {}

# REL-002: the ClientWrapper behind each live session, keyed the same as
# ACTIVE_TASKS — registered and popped in run_pipeline in exactly the same
# places, under the same identity guard, as ACTIVE_TASKS/ACTIVE_LESSONS.
# Exists so a SIGTERM drain (begin_drain() below) can reach
# ClientWrapper.request_wrap_up() for every live session — ACTIVE_TASKS
# alone only gives you the PipelineTask to queue a frame into, not the
# wrapper that needs to force its exchange cap down first.
ACTIVE_WRAPPERS: dict[str, ClientWrapper] = {}

# REL-002: flips True exactly once per process, the moment the first
# SIGTERM is caught (main.py's signal handler -> begin_drain() below).
# Read via is_draining() rather than importing the bare name — a plain
# `from pipeline.factory import DRAINING` binds the boolean's VALUE at
# import time (booleans are immutable), so a later flip here would never
# be visible through that binding. GET /health and both WS accept paths
# (main.py) gate on is_draining().
DRAINING = False


def is_draining() -> bool:
    """True once a SIGTERM drain has started. See DRAINING above."""
    return DRAINING


# The voice runtime is German. These lessons stay English: `lesson_zero` is the
# open-conversation default (kept English by decision — its fake_profiles profile
# targets English), `welcome` is the front-page English concierge (product
# positioning), `goodbye_test` is a dev fixture, and `teacher` is the
# explanation agent (AGENT-001) — Clara explains German IN English by design.
# Content lessons (a1_l1, b1_l1) are German — remove each from this set as it
# converts.
ENGLISH_LESSONS = {"welcome", "goodbye_test", "lesson_zero", "teacher"}

# AGENT-001 (2026-08-11): Clara teaches in English but her voice runs with
# MiniMax's German language_boost — German-accented English, and properly
# pronounced German example words. STT / evaluators stay on lesson_language().
TTS_LANGUAGE_OVERRIDES = {"teacher": "de"}


def lesson_language(lesson_id: str) -> str:
    """STT/TTS language code for a lesson: 'en' for the English exceptions, 'de' otherwise."""
    return "en" if lesson_id in ENGLISH_LESSONS else "de"


def validate_lesson_languages() -> None:
    """Fail-loud startup cross-check of the two per-lesson language sources.

    ``ENGLISH_LESSONS`` drives STT/TTS/goal-eval language; the YAML ``locale:``
    drives Azure pronunciation — maintained independently, with nothing else
    tying them together. A lesson whose locale's primary subtag contradicts
    ``lesson_language()`` would be transcribed in one language and
    pronunciation-scored in another, silently. Called from the FastAPI
    lifespan, so a drifted lesson aborts startup like a bad DB or satz pack.
    """
    for lid in list_lesson_ids():
        locale = load_prompts(lid).get("locale")
        if locale and locale.split("-")[0].lower() != lesson_language(lid):
            raise RuntimeError(
                f"Lesson {lid!r}: locale {locale!r} contradicts "
                f"lesson_language()={lesson_language(lid)!r} — update "
                f"ENGLISH_LESSONS or the lesson's locale"
            )


class _NoOpTurnTraceObserver:
    """Stub installed on `PipelineTask._turn_trace_observer` to satisfy
    Pipecat's cleanup path (task.py:670–671), which calls
    ``end_conversation_tracing()`` whenever ``enable_tracing=True`` without
    checking whether the observer is actually a real instance. We use
    ``enable_turn_tracking=False`` so Pipecat's observer is never built;
    our own ``PipelineLatencyObserver`` owns the conversation/turn spans."""

    def end_conversation_tracing(self):
        pass


# BUG-009: Railway's edge proxy silently idle-kills a WebSocket after ~5min of
# no traffic. Pipecat's own transport-level pings are WS-protocol frames the
# edge terminates before they reach the browser, so they don't reset anything;
# a tandem session with a quiet stretch (learner reading, thinking) gets torn
# down server-side while the browser never sees a close frame (half-open
# socket — UI stays on LISTENING forever). A tiny recurring RTVI server
# message is a REAL application-data frame end-to-end, which is what actually
# resets a proxy's idle timer in both directions. 25s comfortably beats any
# ~5min ceiling even with jitter.
RTVI_HEARTBEAT_INTERVAL_S = 25

# GAME-001 v3: exchanges a tandem session must reach before it credits the
# daily "tandem" mode — TAND-012's shortest session option (5), so a quick
# hello-goodbye can't bank the day's slot. An agent-driven end also credits
# regardless (see the gate in the disconnect path).
TANDEM_STREAK_MIN_EXCHANGES = 5


async def _rtvi_heartbeat(rtvi_processor: RTVIProcessor, wrapper: ClientWrapper, user_id: str):
    """Per-connection keepalive (BUG-009). Reuses `rtvi_processor.send_server_message` —
    the exact call already proven a few lines below by the `session_started`
    push — which queues an `OutputTransportMessageUrgentFrame` from the RTVI
    processor's own position in the pipeline straight to `transport.output()`,
    so no new pipecat mechanism is needed. Gated on `wrapper._end_pending`
    (flipped as soon as the agent DECIDES to end, well before the EndFrame is
    actually queued — see agents/pipecat_wrapper.py) so the loop stops itself
    ahead of the graceful shutdown instead of racing it; the send is also
    wrapped so a transport hiccup can never crash or block the pipeline or
    teardown. Cancelled unconditionally in run_pipeline's `finally`, same
    lifecycle as `_session_watchdog` above — per-client, no module globals."""
    try:
        while True:
            await asyncio.sleep(RTVI_HEARTBEAT_INTERVAL_S)
            if wrapper._end_pending:
                return  # graceful EndFrame is already in flight — don't race it
            try:
                await rtvi_processor.send_server_message({"type": "heartbeat"})
            except Exception as e:  # noqa: BLE001 — must never crash the pipeline
                logger.warning(
                    f"RTVI heartbeat send failed (non-fatal): {type(e).__name__}: {e} user_id={user_id}"
                )
    except asyncio.CancelledError:
        return


async def _session_watchdog(task: PipelineTask, timeout_s: float, user_id: str):
    """Wall-clock cap on a single session (SEC-001; armed only when a timeout is
    passed, i.e. the public demo route). Sleeps `timeout_s`, then ends the
    pipeline gracefully via ``stop_when_done()`` — the same path the agent's
    goodbye uses, so any in-flight bot reply still finishes playing. Cancelled in
    run_pipeline's ``finally`` when the session ends on its own, so it never
    races a natural end."""
    try:
        await asyncio.sleep(timeout_s)
    except asyncio.CancelledError:
        return
    logger.info(f"Session wall-clock cap hit ({timeout_s}s); ending: user_id={user_id}")
    await task.stop_when_done()


# AGENT-001: settle delay before the kickoff turn fires, so the injected
# LLMContextFrame doesn't reach the pipeline before it has finished
# assembling (LLM/TTS processors wired, RTVI handshake in flight). Matches
# sim/chat.py's own 1.5s wait before its first /say call, for the identical
# reason ("let the pipeline finish assembling").
KICKOFF_SETTLE_S = 1.5


async def _kickoff_turn(task: PipelineTask, text: str, user_id: str) -> None:
    """Makes the agent speak first (AGENT-001) for lessons that set a
    `kickoff:` YAML key (today: only the teacher — Clara). Sleeps
    ``KICKOFF_SETTLE_S`` then injects ``text`` as a synthetic first user
    turn using the exact ``LLMContext``/``LLMContextFrame`` construction
    ``/say`` uses in main.py — so the kickoff runs a real, complete LLM → TTS
    turn: exchange counting, goodbye detection, Langfuse spans, and the
    bot-output push to the client all fire identically to a spoken or `/say`
    turn. `ClientWrapper.astream` (agents/pipecat_wrapper.py) recognizes this
    exact text as the kickoff sentinel and keeps the fake "user" line out of
    the transcript — only the bot's real greeting is recorded.

    Wrapped so any failure (a race with pipeline teardown, a queue_frame
    error) only logs a warning — a missing greeting must never kill a
    session; the learner can still speak first and get a normal reply.
    `asyncio.CancelledError` is a `BaseException`, not caught by the
    `except Exception` below, so a fast disconnect that cancels this task
    (see run_pipeline's `finally`) propagates untouched instead of being
    swallowed here.
    """
    try:
        await asyncio.sleep(KICKOFF_SETTLE_S)
        context = LLMContext([{"role": "user", "content": text}])
        await task.queue_frame(LLMContextFrame(context=context))
    except Exception as e:  # noqa: BLE001 — a missing greeting must never crash the pipeline
        logger.warning(
            f"Kickoff turn failed (non-fatal): {type(e).__name__}: {e} user_id={user_id}"
        )


# REL-002: how often begin_drain()'s wait loop polls ACTIVE_TASKS while
# waiting for every wrap-up turn to finish. config.settings.shutdown_drain_s
# (the overall deadline) is read at CALL time inside begin_drain(), not at
# module import, so an env change picked up right before this process
# started is honored even though this module was already imported earlier
# in startup.
_DRAIN_POLL_INTERVAL_S = 0.5


async def _wrap_up_turn(task: PipelineTask, wrapper: ClientWrapper, user_id: str) -> None:
    """REL-002: end one live session gracefully as part of a SIGTERM drain
    (see begin_drain() below).

    Reuses the exact ``LLMContext``/``LLMContextFrame`` injection mechanism
    ``/say`` (main.py) and ``_kickoff_turn`` (above) use to inject one
    synthetic user turn whose text is a stage direction, not dialogue.
    ``ClientWrapper.request_wrap_up()`` builds that text (English for
    `type: teacher`, German otherwise — the same split
    ``ClientWrapper``'s PERF-005 fallback reply already uses) and, in the
    same call, forces ``_max_exchanges`` down to ``_exchange_count + 1`` —
    so the reply this turn produces IS the session's last exchange.
    ``ClientWrapper.astream``'s own ``_exchange_count >= _max_exchanges``
    branch then schedules ``_end_pipeline()`` after the reply streams,
    exactly like any other agent-driven ending: the frontend gets its
    normal close + summary modal, and ``activity_session.ended_by`` records
    "agent", not "crash" — no new end-reason plumbing needed anywhere.

    Skips a session that is already ending on its own
    (``wrapper._end_pending`` already True) — injecting a second closing
    turn into a session that's already mid-goodbye would just add a stray
    unanswered turn behind the EndFrame that's already queued.

    Wrapped end-to-end, same contract as ``_kickoff_turn`` above: a failure
    here (a race with a session finishing on its own between
    ``begin_drain()``'s ``ACTIVE_TASKS`` snapshot and this call, a
    ``queue_frame`` error on an already-tearing-down task) only logs a
    warning — draining one session must never crash the drain of every
    other session, or the shutdown itself.
    """
    if wrapper._end_pending:
        logger.info(
            f"[DRAIN] Session already ending on its own — skipping wrap-up: "
            f"user_id={user_id}"
        )
        return
    try:
        text = wrapper.request_wrap_up()
        context = LLMContext([{"role": "user", "content": text}])
        await task.queue_frame(LLMContextFrame(context=context))
        logger.info(f"[DRAIN] Wrap-up turn injected: user_id={user_id}")
    except Exception as e:  # noqa: BLE001 — draining must never crash shutdown
        logger.warning(
            f"[DRAIN] Wrap-up turn injection failed (non-fatal): "
            f"{type(e).__name__}: {e} user_id={user_id}"
        )


async def begin_drain() -> None:
    """REL-002: the backend half of a graceful SIGTERM drain — see
    main.py's ``_install_sigterm_drain_handler`` for how this gets called
    and what happens around it (the ordering comment there explains the
    whole mechanism end to end).

    Sets ``DRAINING`` FIRST, synchronously, before any ``await`` — so every
    connect that reaches a WS accept path after this point
    (``main.py``'s ``is_draining()`` check, right after ``accept()``) sees
    the flag already True, with no window where a connect could slip in
    believing the server is still healthy. ``GET /health`` reads the same
    flag.

    Then triggers ``_wrap_up_turn`` (above) for every session currently in
    ``ACTIVE_TASKS`` — one fire-and-forget task per session, so a slow or
    stuck session's wrap-up can never block another session's — and waits,
    polling every ``_DRAIN_POLL_INTERVAL_S``, until ``ACTIVE_TASKS`` empties
    out or ``config.settings.shutdown_drain_s`` elapses, whichever comes
    first. With zero live sessions this returns immediately (the loop
    condition is false on its first check).

    Idempotency (a second SIGTERM landing mid-drain) is main.py's job, not
    this function's — it's what schedules this coroutine at most once per
    process. Calling it twice would just re-trigger wrap-up on whatever
    session's first attempt hasn't resolved yet, which ``_wrap_up_turn``'s
    own ``_end_pending`` skip already tolerates, but there's no reason to
    exercise that path.
    """
    global DRAINING
    DRAINING = True
    from config.settings import shutdown_drain_s  # read the live value at call time

    sessions = list(ACTIVE_TASKS.items())
    if not sessions:
        logger.info("[DRAIN] No live sessions at SIGTERM — nothing to wrap up")
        return

    logger.info(
        f"[DRAIN] SIGTERM drain starting: {len(sessions)} live session(s), "
        f"up to {shutdown_drain_s}s"
    )
    for drain_user_id, drain_task in sessions:
        wrapper = ACTIVE_WRAPPERS.get(drain_user_id)
        if wrapper is None:
            logger.warning(
                f"[DRAIN] No wrapper registered for a live task — skipping "
                f"wrap-up: user_id={drain_user_id}"
            )
            continue
        wrap_up_task = asyncio.create_task(_wrap_up_turn(drain_task, wrapper, drain_user_id))
        # Mirrors the BUG-006 pattern on ClientWrapper._end_task below —
        # nothing else awaits this task, so without a done-callback an
        # exception would vanish silently instead of showing up in logs.
        wrap_up_task.add_done_callback(
            lambda t, uid=drain_user_id: t.cancelled()
            or t.exception() is None
            or logger.error(f"[DRAIN] wrap-up task failed unexpectedly for {uid}: {t.exception()!r}")
        )

    elapsed = 0.0
    ticks = 0
    while ACTIVE_TASKS and elapsed < shutdown_drain_s:
        await asyncio.sleep(_DRAIN_POLL_INTERVAL_S)
        elapsed += _DRAIN_POLL_INTERVAL_S
        ticks += 1
        if ticks % 10 == 0:  # ~every 5s — the 0.5s poll interval itself would be log spam
            logger.info(
                f"[DRAIN] {len(ACTIVE_TASKS)} session(s) still live after {elapsed:.1f}s"
            )

    if ACTIVE_TASKS:
        logger.warning(
            f"[DRAIN] Deadline hit ({shutdown_drain_s}s) — {len(ACTIVE_TASKS)} "
            f"session(s) still live; handing off to normal shutdown anyway"
        )
    else:
        logger.info(f"[DRAIN] All session(s) wrapped up after {elapsed:.1f}s")


async def run_pipeline(websocket, user_id: str, voice: str = "happy_harry", lesson_id: str = "lesson_zero", topic: str = "", session_timeout_s: float | None = None, db_user_id: str | None = None, exchanges: int | None = None, pattern: str | None = None):
    """Builds and runs a full pipeline for a single client connection."""
    # One Langfuse Session per WebSocket connection. `user_id` is stable across
    # connections (per-tab UUID today, auth-derived later); `session_id` resets
    # on every Connect so the Langfuse UI shows one Session per conversation.
    # The same uuid is fed to Pipecat as `conversation_id`, so 1 connect = 1
    # conversation trace = 1 Langfuse session. `session_id` is ALSO the
    # `activity_session.id` primary key (a Postgres UUID column) — every DB
    # and logging use of it must stay this bare hex string. See
    # `trace_session_id` below for the Langfuse-only surface-prefixed form.
    session_id = uuid4().hex

    # Which `users` row this session's DB record FKs to. Defaults to `user_id`
    # (authed /learn → the real user). The public demo route passes
    # db_user_id="demo" so all anonymous visitors share one seeded sentinel row
    # (AUTH-001) instead of minting a `users` row per visitor; `user_id` itself
    # stays per-session for ACTIVE_TASKS routing, Langfuse, and the wrapper.
    db_user_id = db_user_id or user_id

    # Loaded here — ahead of the aiohttp block it used to live in — so its
    # `type` is known before the first Langfuse-facing use of the session id
    # (`attach_trace_context`, right below). `load_prompts` is a pure YAML
    # read with no side effects, so hoisting it this far is safe; it falls
    # back to lesson_zero on an unknown id, same as before.
    lesson_snapshot = load_prompts(lesson_id)

    # Langfuse-only session id: mirrors the readable-prefix convention the
    # drill surfaces already mint client-side (flow-, satz-, brf-,
    # interview-), so voice sessions read as tandem-/teacher-/lesson-/demo-
    # in the Langfuse UI instead of an undifferentiated hex blob. Every
    # Langfuse-facing use below reads `trace_session_id`; every DB/logging
    # use keeps the bare `session_id` from above. The demo socket
    # (`main.py::ws_demo_endpoint`) is the only caller passing
    # `db_user_id="demo"` — the same signal the disconnect-side streak gate
    # below already keys on — so it's checked first rather than falling
    # through the type map, which would otherwise misfile it under its
    # forced `welcome` lesson's own type ("respond" -> "lesson").
    if db_user_id == "demo":
        _trace_prefix = "demo"
    else:
        _trace_prefix = {"tandem": "tandem", "teacher": "teacher"}.get(
            lesson_snapshot.get("type"), "lesson"
        )
    trace_session_id = f"{_trace_prefix}-{session_id}"

    # Langfuse v4: attach `user.id`/`langfuse.session.id` as OTel baggage for
    # the whole connection. Every span created under this context — turn
    # spans, pipecat's STT/TTS spans, the wrapper's llm span, the
    # post-session evaluators' generation spans (asyncio tasks copy the
    # context) — gets both attributes stamped by the baggage processor in
    # agents/observability.py, so observation-level filtering works without
    # threading the ids through every call site. Detached at the end of the
    # disconnect `finally` below.
    _trace_ctx_token = attach_trace_context(user_id=user_id, session_id=trace_session_id)

    # B2: bound the MiniMax TTS session's requests — with no timeout a hung
    # MiniMax request falls back to aiohttp's 5-minute default, stalling this
    # client's turn (and, if awaited on the event loop elsewhere, worse).
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:

        # Transport: one per client (wraps this specific websocket)
        transport = transport_fastapi_ws(websocket)

        # Fresh services per client. Language is per-lesson: German runtime,
        # English only for the `welcome` concierge and other English exceptions.
        # `lesson_snapshot` was already loaded above (needed early for the
        # trace-session prefix); language still derives from the id of the
        # lesson that actually loaded, not the raw query param, so an
        # unknown-id fallback to lesson_zero still gets the right language.
        lesson_lang = lesson_language(lesson_snapshot.get("id", lesson_id))
        # AGENT-001: Clara's voice runs a German language_boost while everything
        # else about her session (STT, evaluators) stays on lesson_lang — see
        # TTS_LANGUAGE_OVERRIDES above.
        tts_lang = TTS_LANGUAGE_OVERRIDES.get(lesson_snapshot.get("id", lesson_id), lesson_lang)
        # STT-003 P2: optional per-lesson keyterm list (nova-3 keyterm prompting).
        # The snapshot is already loaded, so no ordering change — English lessons
        # simply carry no `keyterms:` key and pass None (param omitted).
        stt = stt_deepgram(language=lesson_lang, keyterms=lesson_snapshot.get("keyterms"))
        tts = tts_minimax(session, voice=voice, language=tts_lang)
        converter = TranscriptionToContextConverter()

        # TAND-012: per-session exchange cap for tandem only, whitelisted to
        # {5, 10, 15} by the frontend/main.py query param. Teacher no longer
        # uses an exchange picker — she caps at teacher.yaml's max_exchanges
        # (15, PAY-007) and is gated by daily talk count instead (see below). Any other
        # type (including a stray query param on a non-tandem /learn connect)
        # leaves the YAML's own max_exchanges untouched. Mutating the snapshot
        # dict (not just a local var) keeps the DB `lesson_snapshot` column
        # faithful to what the session actually ran with — `load_prompts`
        # returns a fresh dict per call, so this never leaks into another session.
        exchanges_override = (
            exchanges
            if lesson_snapshot.get("type") == "tandem" and exchanges in (5, 10, 15)
            else None
        )
        if exchanges_override is not None:
            lesson_snapshot["max_exchanges"] = exchanges_override

        # CLARA-15 P3: teacher-lessons-only, defaults False for every other
        # lesson type AND for the demo socket. Set below, inside the teacher
        # daily-count gate block, from that SAME already-loaded user row (no
        # extra query) — a developer gets it regardless of tier (they also
        # bypass the gate itself).
        forge_enabled: bool = False

        # SESS-001: one live voice session per account. A second simultaneous
        # connect used to overwrite this user's ACTIVE_TASKS entry (stealing
        # /say routing) and, worse, its disconnect popped the entry out from
        # under the FIRST session — the BUG-004 identity guard in the finally
        # below protects the other ordering, not that one. Owner's call: reject
        # the newcomer outright (close 4004; the client pre-checks
        # GET /sessions/active and shows a clear panel, this is the backstop).
        # Demo connects are exempt — public visitors must never be able to lock
        # each other out, and the demo's per-visitor ids make collisions
        # meaningless anyway. Known accepted gaps, both deliberate:
        #   - developers are NOT exempt (this is correctness, not billing — the
        #     owner's own account is the one that hit the bug);
        #   - the check-to-register window (gate here, ACTIVE_TASKS[user_id]
        #     assignment further down) is not atomic, so two connects landing
        #     within the same sub-second setup window can still both pass — that
        #     degenerate race just falls back to the pre-SESS-001 behavior, and
        #     a placeholder registration was rejected because a leaked entry
        #     would lock the account out until restart;
        #   - a session that dies UNGRACEFULLY server-side (edge half-open) holds
        #     the lock until the transport notices — bounded in practice, and a
        #     graceful close (End button, agent goodbye, tab close) frees it
        #     instantly.
        if db_user_id != "demo" and user_id in ACTIVE_TASKS:
            logger.info(
                f"session gate: already live for user {user_id} (4004) — "
                f"rejecting second connect"
            )
            await websocket.close(code=4004, reason="Session already live elsewhere")
            return

        # Clara daily-count gate (replaces the PAY-002 coin bundle for teacher).
        # free→0/day (locked), basic→1/day, premium→3/day, developer→∞.
        # Counted at accept time — the moment Start creates the session — so the
        # tally increments even if the talk ends early. Window is the coin day
        # (05:00 local, same as coins/engine.py::today_key / next_reset_at).
        # PAY-002 coin gate for tandem/conversation/respond stays below.
        if db_user_id != "demo" and lesson_snapshot.get("type") == "teacher":
            try:
                from datetime import timedelta as _td
                from sqlalchemy import func as _func, select as _select2
                from database.orm import ActivitySession as _AS2, User as _User2
                from coins.engine import next_reset_at as _nra2

                async with get_sessionmaker()() as _gate_db2:
                    _u2 = await _gate_db2.scalar(_select2(_User2).where(_User2.id == db_user_id))
                    # CLARA-15 P3: derived from this SAME already-loaded row
                    # — teacher lessons only (this whole block is gated on
                    # that above), no extra query.
                    if _u2 is not None and (_u2.role or "") == "developer":
                        forge_enabled = True
                    if _u2 is not None and (_u2.role or "") != "developer":
                        tier2 = _u2.tier or "free"
                        limit2 = {"free": 0, "basic": 1, "premium": 3}.get(tier2, 0)
                        if limit2 <= 0:
                            logger.info(f"teacher gate: free/locked for user {db_user_id} (4002)")
                            await websocket.close(code=4002, reason="Clara is a Basic feature")
                            return
                        nxt = _nra2(_u2.timezone)
                        day_start = nxt - _td(days=1)
                        # started_at is stored naive (datetime.now()), compare naive UTC
                        ds_naive = day_start.replace(tzinfo=None)
                        nr_naive = nxt.replace(tzinfo=None)
                        cnt = await _gate_db2.scalar(
                            _select2(_func.count()).select_from(_AS2).where(
                                _AS2.user_id == db_user_id,
                                _AS2.lesson_id == "teacher",
                                _AS2.started_at >= ds_naive,
                                _AS2.started_at < nr_naive,
                            )
                        )
                        used2 = int(cnt or 0)
                        if used2 >= limit2:
                            logger.info(
                                f"teacher gate: daily limit hit for user {db_user_id} "
                                f"tier={tier2} used={used2}/{limit2} (4003)"
                            )
                            await websocket.close(code=4003, reason="Daily Clara limit reached")
                            return
            except Exception as _ge2:
                # Don't fail closed on a 4003 path error — close 1011 so the
                # client can retry rather than silently admitting beyond the lim.
                # But a close error here must still block admission (fail closed).
                if "_gate_db2" in locals():
                    pass
                logger.warning(f"teacher daily gate check failed — closing 1011: {_ge2}")
                try:
                    await websocket.close(code=1011, reason="billing check failed")
                except Exception:
                    pass
                return

        # PAY-002 coin accept gate for tandem/conversation/respond (teacher
        # excluded — she is daily-count-gated above, not coin-gated). MUST run
        # BEFORE the activity_session INSERT — a rejected session never gets a row.
        # Bundle: tandem uses exchanges param × VOICE_EXCHANGE (default 10),
        # conversation/respond use YAML max_exchanges × VOICE_EXCHANGE.
        # Developer role bypasses. Demo socket is never charged or gated.
        if db_user_id != "demo" and lesson_snapshot.get("type") in ("tandem", "conversation", "respond"):
            try:
                from coins.prices import DAILY_ALLOWANCE as _DA, VOICE_EXCHANGE as _VE
                from coins.engine import today_key as _tk
                from sqlalchemy import select as _select
                from database.orm import User as _User

                lesson_type = lesson_snapshot.get("type")
                async with get_sessionmaker()() as _gate_db:
                    _user = await _gate_db.scalar(_select(_User).where(_User.id == db_user_id))
                    if _user is not None and (_user.role or "") != "developer":
                        if lesson_type == "tandem":
                            bundle_exchanges = exchanges if exchanges in (5, 10, 15) else 10
                            owed = bundle_exchanges * _VE
                        elif lesson_type in ("conversation", "respond"):
                            owed = int(lesson_snapshot.get("max_exchanges") or 0) * _VE
                        else:
                            owed = 0
                        if owed > 0:
                            _key = _tk(_user.timezone)
                            if _user.allowance_day != _key:
                                _allow = _DA.get(_user.tier or "free", 0)
                            else:
                                _allow = _user.allowance_remaining or 0
                            _purch = _user.purchased_coins or 0
                            _available = _allow + _purch
                            if _available < owed:
                                logger.info(
                                    f"coin gate: insufficient funds for user {db_user_id} "
                                    f"owed={owed} available={_available} (4001)"
                                )
                                await websocket.close(code=4001, reason="Not enough coins")
                                return
            except Exception as _ge:
                logger.warning(f"coin gate check failed — closing 1011: {_ge}")
                try:
                    await websocket.close(code=1011, reason="billing check failed")
                except Exception:
                    pass
                return

        # Ledger-backed prompt layers. The prompt middleware is sync, so these
        # async DB reads happen here, once at connect, and ride on Context.
        # Tandem lessons (TANDEM-001) carry all three; the teacher (AGENT-001)
        # carries only the grammar focus — no debrief means no notes, and vocab
        # weaving is a tandem behaviour, not a teaching one. Non-fatal: a DB
        # hiccup just means a less-personalised chat, never a failed connect.
        grammar_focus: list = []
        session_notes: list = []
        vocab_words: list = []
        student_name: str | None = None
        # LEVEL round: self-declared CEFR bucket, threaded like student_name.
        # Loaded once below for BOTH tandem and teacher (TAND-014a hoisted
        # this out of the teacher-only branch), and reused by teacher's
        # cold-start starters_for_level(...) call so there is still only one
        # DB read either way.
        student_level: str | None = None
        # CLARA-16: teacher-only full exercise catalog (Context.exercise_catalog)
        # — built below, once grammar_focus has settled (cold-start starters and
        # any `?pattern=` injection included), so the exclusion set is complete.
        # Empty for every other lesson type.
        exercise_catalog: list = []
        # CLARA-20: teacher-only, mirrors `pattern` once it's passed the same
        # `pattern in load_taxonomy()` validation as the grammar-focus
        # injection below (Context.picked_pattern) — None for every other
        # lesson type, or when no valid pattern was picked.
        picked_pattern: str | None = None
        snapshot_type = lesson_snapshot.get("type")
        if snapshot_type in ("tandem", "teacher"):
            try:
                async with get_sessionmaker()() as db:
                    grammar_focus = await load_grammar_focus(db, user_id=db_user_id)

                    # LEVEL round (TAND-014a): loaded here, unconditionally,
                    # for BOTH tandem and teacher — available for either
                    # type's own level_examples prompt layer
                    # (Context.student_level) even when the ledger isn't
                    # empty. Was teacher-only (inside the `elif` below);
                    # hoisted so tandem gets it too, without a second DB
                    # read. Teacher's cold-start starters_for_level(...) call
                    # further below still reuses this same value.
                    student_level = await load_user_level(db, user_id=db_user_id)

                    if snapshot_type == "tandem":
                        # TAND-008: memory is per partner — Paul's notes never
                        # reach Lena's prompt and vice versa.
                        session_notes = await load_tandem_notes(
                            db, user_id=db_user_id, lesson_id=lesson_id
                        )
                        # TAND-002: 7, not the default 10 — fewer words lowers the
                        # cramming pressure on Lena's short replies (vocab dose).
                        # TAND-012: a shorter session can't naturally fit 7 deck
                        # words, so scale down with the exchange cap — fewer
                        # exchanges means fewer words to weave, so the partner
                        # never crams.
                        # Teacher uses fixed lesson cap (YAML 15, PAY-007), no exchange picker
                        vocab_limit = 7 if snapshot_type == "teacher" else {5: 4, 10: 7, 15: 10}.get(exchanges_override or 0, 7)
                        vocab_words = await load_vocab_words(db, user_id=db_user_id, limit=vocab_limit)
                    elif snapshot_type == "teacher":
                        # Greet-by-name (v8 kickoff): first name only, never
                        # the full name — reduces to None if the name is
                        # unset or blank after stripping.
                        raw_name = await load_user_name(db, user_id=db_user_id)
                        if raw_name:
                            stripped_name = raw_name.strip()
                            student_name = stripped_name.split()[0] if stripped_name else None

                        # Cold-start slice: an empty ledger means an empty
                        # focus section, which leaves Clara with no legal
                        # `[[ÜBUNG: <id>]]` id at all. Fall back to three
                        # curated starters for the learner's level bucket,
                        # shaped like load_grammar_focus's own entries so
                        # _format_teacher_focus needs no special-casing;
                        # `seeded: True` lets the prompt layer (below) tell
                        # "typical for your level" apart from real slips.
                        if not grammar_focus:
                            grammar_focus = [
                                {
                                    "pattern_id": s["pattern_id"],
                                    "label": s["label"],
                                    "description": s["description"],
                                    # AGENT-002: starters_for_level (CLARA-19)
                                    # already carries the taxonomy's
                                    # wrong/right pair — just stopped short of
                                    # propagating here before.
                                    "wrong": s["wrong"],
                                    "right": s["right"],
                                    "examples": [],
                                    "seeded": True,
                                }
                                for s in starters_for_level(student_level)
                            ]
                        # `?pattern=`: the topic screen's picked focus/starter
                        # card, guaranteed onto the page. Without this the
                        # picked topic can fall outside the top-3 ledger
                        # ranking (or, pre-cold-start-slice, have nothing
                        # rendered at all) and leave Clara with no legal id
                        # for the very topic of the lesson — see
                        # evals/teacher/2026-08-28-baseline-v6/02-beginner-mara.md.
                        # main.py forwards `pattern` for every lesson type;
                        # re-gated to teacher here, and an unknown/absent id
                        # is ignored silently rather than rejected.
                        if pattern and pattern in load_taxonomy():
                            # CLARA-20: same validated id, threaded onto
                            # Context.picked_pattern so the teacher branch of
                            # conversational_prompt.py can look it up in the
                            # curated explanation bank.
                            picked_pattern = pattern
                            existing = next(
                                (p for p in grammar_focus if p["pattern_id"] == pattern),
                                None,
                            )
                            if existing is not None:
                                grammar_focus.remove(existing)
                                grammar_focus.insert(0, existing)
                            else:
                                taxon = load_taxonomy()[pattern]
                                examples = await load_pattern_examples(
                                    db, user_id=db_user_id, pattern_id=pattern
                                )
                                grammar_focus.insert(
                                    0,
                                    {
                                        "pattern_id": pattern,
                                        "label": taxon["label"],
                                        "description": taxon["description"],
                                        "wrong": taxon["wrong"],  # AGENT-002
                                        "right": taxon["right"],  # AGENT-002
                                        "examples": examples,
                                        # No `seeded` key — this is the
                                        # learner's actual choice, not a
                                        # level-typical suggestion.
                                    },
                                )
                        # AGENT-005 follow-up (TOPIC-FREEFORM): the topic
                        # screen's free-text box, distinguished from a
                        # tapped focus/starter card by the ABSENCE of a
                        # validated `?pattern=` (picked_pattern stays None
                        # for free text — see TeacherChat.tsx's own
                        # `pattern` state comment, which already documents
                        # this as the existing signal). Runs here, past the
                        # daily-talk gate above, so a rejected connect logs
                        # nothing. Empty topic ("I just want to talk") is
                        # not a demand signal — nothing to log.
                        elif pattern:
                            # A pattern was SENT but is not in the taxonomy
                            # (stale card after a rename, or a hand-edited
                            # URL): that is a card tap gone wrong, not free
                            # text — say so, and do not log it as demand.
                            logger.warning(
                                f"Teacher connect sent unknown pattern {pattern!r} "
                                f"(topic={topic!r}) — ignored, not logged as free text"
                            )
                        elif topic and topic.strip():
                            await log_topic_freeform(
                                topic.strip(),
                                user_id=db_user_id,
                                session_id=trace_session_id,
                            )
                logger.info(
                    f"{snapshot_type.capitalize()} layers: focus_patterns={len(grammar_focus)} "
                    f"notes={len(session_notes)} vocab_words={len(vocab_words)} "
                    f"name={'set' if student_name else 'none'} "
                    f"level={student_level or 'unset'} "
                    f"topic={topic!r} pattern={pattern!r} user={db_user_id}"
                )
            except (SQLAlchemyError, OSError) as e:  # noqa: BLE001 — non-fatal
                logger.warning(
                    f"{snapshot_type.capitalize()} layer fetch failed (non-fatal): {type(e).__name__}: {e}"
                )

        # CLARA-16: the full exercise catalog (teacher lessons only) — union
        # of every taxonomy pattern id `teacher/registry.py`'s adapters
        # actually cover, minus the ids already printed above in
        # `grammar_focus` (real ledger patterns, cold-start starters, and any
        # `?pattern=` injection are all resolved by this point, so this runs
        # AFTER that block, not inside it). Ordered by `load_taxonomy()`'s own
        # dict order (A1 -> B1, preserved from the YAML). This is what lets
        # Clara deal a pool-covered topic that falls outside her top-3 focus
        # list instead of live-forging a topic the pool already has a real
        # exercise for — see agents/conversational_prompt.py's teacher branch
        # (catalog_header) and agents/prompts/teacher.yaml's re-scoped
        # fallbacks. Non-fatal, same contract as the ledger reads just above:
        # a failure here only means Clara's prompt renders without the extra
        # list, never a failed connect.
        if snapshot_type == "teacher":
            try:
                taxonomy = load_taxonomy()
                focus_ids = {p["pattern_id"] for p in grammar_focus}
                covered_ids: set[str] = set()
                for pattern_ids in teacher_coverage().values():
                    covered_ids.update(pattern_ids)
                exercise_catalog = [
                    {"pattern_id": pid, "label": taxonomy[pid]["label"]}
                    for pid in taxonomy  # dict preserves YAML order (A1 -> B1)
                    if pid in covered_ids and pid not in focus_ids
                ]
            except Exception as e:  # noqa: BLE001 — non-fatal, like the ledger reads above
                logger.warning(
                    f"Exercise catalog build failed (non-fatal): {type(e).__name__}: {e}"
                )
                exercise_catalog = []

        # Per-session audio location (OBS-010). Derived before the DB insert
        # so audio_path is available for the row; kept here (not above the
        # gate) because mkdir is the only side-effect and the gate has no
        # use for it.
        audio_dir = Path("logs/conversations") / datetime.now().strftime("%Y-%m-%d")
        audio_dir.mkdir(parents=True, exist_ok=True)

        # Insert the activity_session row at connect (DATA-001). Non-fatal:
        # if the DB is down we log a warning and continue — audio export,
        # evaluators, and OTel flush MUST still run. The row is then UPDATEd
        # on disconnect with transcript + eval results. ``lesson_snapshot``
        # freezes the YAML at session start so future history UI shows what
        # the user actually saw, even if the YAML changes later.
        started_at = datetime.now()
        audio_path = str(audio_dir / f"{session_id}.mp3")
        try:
            async with get_sessionmaker()() as db:
                await create_session_row(
                    db,
                    session_id=session_id,
                    user_id=db_user_id,
                    lesson_id=lesson_id,
                    voice=voice,
                    started_at=started_at,
                    audio_path=audio_path,
                    lesson_snapshot=lesson_snapshot,
                )
        except (SQLAlchemyError, OSError) as e:  # noqa: BLE001 — non-fatal
            logger.warning(
                f"DB session insert failed (non-fatal): {type(e).__name__}: {e}"
            )

        # Per-client wrapper (agent + logger + context settings inside).
        # `session_id` (bare) and `trace_session_id` (Langfuse-prefixed) are
        # both passed — the wrapper's own DB/transcript fields need the
        # former, its hand-rolled `llm` span needs the latter.
        wrapper = ClientWrapper(user_id=user_id, session_id=session_id, trace_session_id=trace_session_id, voice=voice, lesson_id=lesson_id, topic=topic, grammar_focus=grammar_focus, session_notes=session_notes, vocab_words=vocab_words, exercise_catalog=exercise_catalog, max_exchanges_override=exchanges_override, student_name=student_name, student_level=student_level, forge_enabled=forge_enabled, picked_pattern=picked_pattern)
        llm = LangchainProcessor(chain=wrapper)

        # Per-client audio recorder.
        # `sample_rate=16000` matches Deepgram nova-3 (no resampling cost) and
        # is what Azure Pronunciation Assessment expects natively.
        # `enable_turn_audio` gates the per-user-turn audio stashing that the
        # post-session pronunciation evaluator (PRON-001) consumes — it is the
        # ONLY consumer (factory.py `_run_pronunciation`, itself gated on the
        # same `locale is not None`). Lessons without a `locale:` (teacher,
        # lesson_zero, welcome, …) would buffer every user turn in RAM for an
        # evaluator that provably skips them — pure waste, so the recorder
        # never collects those turns in the first place. (Memory review
        # 2026-09-01: AudioBufferProcessor + the turn stash are the 1.5 GB
        # spike class; this halves them for locale-less rooms.)
        audiobuffer = AudioBufferProcessor(
            num_channels=1,
            sample_rate=16000,
            enable_turn_audio=load_pronunciation_locale(lesson_id) is not None,
        )

        def _export_session_audio(wav_path, mp3_path, audio, sample_rate, num_channels):
            """Blocking WAV write + pydub/ffmpeg MP3 encode — runs in a worker
            thread so disconnect-time encoding never stalls other clients'
            pipelines on the event loop (BUG-003)."""
            with wave.open(str(wav_path), "wb") as wf:
                wf.setnchannels(num_channels)
                wf.setsampwidth(2)
                wf.setframerate(sample_rate)
                wf.writeframes(audio)
            AudioSegment.from_wav(str(wav_path)).export(
                str(mp3_path), format="mp3", bitrate="128k"
            )

        @audiobuffer.event_handler("on_audio_data")
        async def on_audio_data(buffer, audio, sample_rate, num_channels):
            base_path = audio_dir / session_id
            wav_path = base_path.with_suffix(".wav")
            mp3_path = base_path.with_suffix(".mp3")
            # Same non-fatal contract as the other disconnect-side steps: an
            # export failure (ffmpeg missing, disk full) must not crash the
            # pipeline, and the temp WAV is removed either way.
            try:
                await asyncio.to_thread(
                    _export_session_audio,
                    wav_path, mp3_path, audio, sample_rate, num_channels,
                )
                # MEMORY-003: RSS right after the encode spike (full PCM is
                # loaded a third time by AudioSegment) — this is where the
                # graph's tall narrow peaks were born.
                logger.info(
                    f"Audio saved to: {mp3_path} "
                    f"(session_audio={len(audio) / (1024 * 1024):.1f}MB PCM{_rss_log_suffix()})"
                )
            except Exception as e:  # noqa: BLE001 — audio export must not block cleanup
                logger.warning(f"Audio export failed (non-fatal): {type(e).__name__}: {e}")
            finally:
                wav_path.unlink(missing_ok=True)

        @audiobuffer.event_handler("on_user_turn_audio_data")
        async def on_user_turn_audio(_buffer, audio, sample_rate, num_channels):
            """Stash the user's just-completed turn audio on the wrapper for
            the post-session pronunciation evaluator (PRON-001). Fires on
            UserStoppedSpeakingFrame; verified at
            `.venv/.../pipecat/processors/audio/audio_buffer_processor.py:248`.
            """
            wrapper.append_user_turn_audio(bytes(audio), sample_rate)

        # RTVI processor + observer. Both user transcripts and bot text are
        # pushed once-per-turn by our own code, not by the observer:
        #   - user transcripts: TranscriptionToContextConverter pushes one
        #     consolidated bubble on UserStoppedSpeakingFrame (the same joined
        #     string it hands the LLM). So user_transcription_enabled is OFF —
        #     otherwise the observer would also forward every Deepgram segment,
        #     producing several stacked bubbles per spoken utterance.
        #   - bot text: ClientWrapper pushes one message per turn to avoid the
        #     framework's dual-path duplicate (LLM-side AggregatedTextFrame AND
        #     TTS-side TTSTextFrame both observed).
        rtvi_processor = RTVIProcessor()
        rtvi_observer = RTVIObserver(
            rtvi=rtvi_processor,
            params=RTVIObserverParams(
                user_transcription_enabled=False,
                bot_output_enabled=False,
                bot_llm_enabled=False,
                bot_tts_enabled=False,
                # bot_speaking_enabled drives botStartedSpeaking / botStoppedSpeaking on
                # the client — the frontend uses botStoppedSpeaking to delay rendering
                # the bot bubble until after the TTS audio finishes playing.
                bot_speaking_enabled=True,
                user_speaking_enabled=False,
                metrics_enabled=False,
            ),
        )

        # Push the session_id to the client as soon as the RTVI handshake
        # completes, so the frontend can later hit GET /sessions/{id} for the
        # post-session eval modal (EVAL-UI-001). Registering AFTER `RTVIProcessor()`
        # and firing on `on_client_ready` guarantees the JS listener is wired —
        # pushing earlier would silently drop the message.
        @rtvi_processor.event_handler("on_client_ready")
        async def _push_session_started(_processor):
            try:
                await rtvi_processor.send_server_message({
                    "type": "session_started",
                    "session_id": session_id,
                    "lesson_id": lesson_id,
                })
            except Exception as e:  # noqa: BLE001 — non-fatal
                logger.warning(
                    f"Failed to push session_started RTVI message (non-fatal): "
                    f"{type(e).__name__}: {e}"
                )

        # Give the wrapper the processor reference so it can push bot output.
        wrapper.rtvi_processor = rtvi_processor
        # Same for the converter, so it can push the consolidated user bubble
        # once per turn (one message on UserStoppedSpeakingFrame, mirroring how
        # the wrapper pushes bot text once per turn).
        converter.rtvi_processor = rtvi_processor

        # Tracks per-turn TTS audio duration (sample-count math on TTSAudioRawFrame).
        # Fires `wrapper.flush_bot_output` when TTS finishes the turn, which is also
        # the moment we push the bot-output RTVI message — so message arrival on the
        # client lines up with end-of-audio and carries the duration.
        tts_duration = TTSDurationTracker(on_turn_complete=wrapper.flush_bot_output)

        # Owns the conversation + turn spans with pipeline-TTFB semantics:
        # turn = UserStoppedSpeakingFrame → BotStartedSpeakingFrame (the "how fast
        # is our pipeline" number). Pipecat's own turn observers are suppressed
        # via `enable_turn_tracking=False` on the PipelineTask below. The tts
        # reference lets the observer re-arm the one-span-per-turn gate on
        # FirstOnlyTracedMiniMaxTTS (long bot replies trigger multiple run_tts
        # calls; we want exactly one TTS span per turn = the TTFB measurement).
        pipeline_observer = PipelineLatencyObserver(
            # Langfuse-only — the observer's only use of this value is the
            # `langfuse.session.id` attribute it stamps on each turn span.
            session_id=trace_session_id,
            user_id=user_id,
            lesson_id=lesson_id,
            voice=voice,
            tts_service=tts,
            # Required for BUG-002 audio↔text pairing: observer ticks the
            # wrapper's VAD-stop seq on every UserStoppedSpeakingFrame.
            wrapper=wrapper,
        )

        # BUG-001 / MOBILE-001 #3: on speakerphone the bot's voice loops back into
        # the mic. Our WebSocket transport gives the browser echo canceller no
        # reference signal (Pipecat #1771), so the echo reaches Deepgram + Silero
        # VAD and — with allow_interruptions=True — cancels the bot mid-sentence.
        # STTMuteFilter(ALWAYS) drops mic audio + VAD/transcription frames while the
        # bot speaks (half-duplex), so nothing loops back. Headphones already avoid
        # this; this fixes speakerphone. The typed /say path is unaffected.
        stt_mute = STTMuteFilter(config=STTMuteConfig(strategies={STTMuteStrategy.ALWAYS}))

        # AUDIO-COST-001: sums seconds of audio actually forwarded to Deepgram
        # this session, for the post-session-analysis cost stamp below.
        # Placed AFTER stt_mute — stt_mute drops InputAudioRawFrame locally
        # while muted (see pipeline/audio_meter.py's docstring for the
        # decisive pipecat source lines), so nothing muted ever reaches this
        # position; counting everything that does cross it already matches
        # what Deepgram's websocket actually bills for, no extra mute-state
        # tracking needed here.
        audio_meter = AudioSecondsMeter()

        pipeline = Pipeline([
            transport.input(),
            stt_mute,          # deafen STT while bot speaks — kills speakerphone echo
            audio_meter,       # sums billable seconds of what actually reaches Deepgram
            stt,
            converter,
            llm,
            tts,
            tts_duration,     # sums TTSAudioRawFrame.num_frames per turn, triggers bot-output push
            rtvi_processor,    # pushes RTVI client messages assembled by rtvi_observer + ClientWrapper
            transport.output(),
            audiobuffer,
        ])

        # `enable_tracing=True` is REQUIRED so services (Deepgram, MiniMax) set
        # their `_tracing_enabled` flag on StartFrame, which is what makes their
        # `@traced_stt` / `@traced_tts` decorators actually emit spans.
        # `enable_turn_tracking=False` suppresses Pipecat's own TurnTrackingObserver
        # + TurnTraceObserver — those use wall-clock turn semantics (StartFrame →
        # BotStoppedSpeakingFrame + 2.5s timeout) which includes user-think time,
        # VAD silence, audio playback. Our `PipelineLatencyObserver` replaces them
        # with pipeline-TTFB semantics (UserStopped → BotStarted) and owns the
        # conversation/turn span attributes itself.
        # `params.enable_metrics=True` lights up per-service `_metrics.ttfb`, which
        # the @traced_* decorators expose on the span (Deepgram first-transcript
        # latency, MiniMax first-audio-chunk latency).
        task = PipelineTask(
            pipeline,
            observers=[rtvi_observer, pipeline_observer],
            params=PipelineParams(enable_metrics=True),
            enable_tracing=True,
            enable_turn_tracking=False,
        )
        # Pipecat bug workaround: when enable_tracing=True + enable_turn_tracking=False,
        # PipelineTask sets `_turn_trace_observer = None` (task.py:269–280) but the
        # cleanup at task.py:670–671 only guards with hasattr, which returns True for a
        # None attribute. Result: 'NoneType' object has no attribute 'end_conversation_tracing'
        # during disconnect. Replace with a stub that no-ops cleanup — we own
        # conversation/turn lifecycle ourselves via PipelineLatencyObserver.
        task._turn_trace_observer = _NoOpTurnTraceObserver()
        wrapper._pipeline_task = task  # Let wrapper end the pipeline via EndTaskFrame

        # B4: a bot-side ErrorFrame (MiniMax/OpenRouter/Deepgram failure that
        # doesn't throw cleanly) is otherwise silent — the conversation just
        # freezes until Pipecat's default 300s idle-cancel. `on_pipeline_error`
        # fires for every ErrorFrame reaching the task from upstream
        # (task.py::_source_push_frame). Fatal errors already make Pipecat
        # queue its own CancelFrame right after this handler runs, so we only
        # need to act on the non-fatal case — log loudly and end the session
        # gracefully via the same stop_when_done() path the agent's own
        # goodbye uses (agents/pipecat_wrapper.py::_end_pipeline), so a failed
        # final turn doesn't hang for 5 minutes.
        @task.event_handler("on_pipeline_error")
        async def _on_pipeline_error(_task, frame):
            logger.error(
                f"Pipeline ErrorFrame (bot-side failure): session_id={session_id} "
                f"user_id={user_id} lesson_id={lesson_id} fatal={frame.fatal} "
                f"error={frame.error!r} exception={frame.exception!r}"
            )
            if frame.fatal:
                return  # Pipecat already cancels the pipeline for fatal errors
            try:
                await task.stop_when_done()
            except Exception as e:  # noqa: BLE001 — must not crash the event-handler path
                logger.warning(
                    f"stop_when_done() after ErrorFrame failed (non-fatal): "
                    f"{type(e).__name__}: {e}"
                )

        # User-ended sessions (Finish click, closed tab): without this handler
        # the pipeline outlives the socket and only Pipecat's 300s idle-cancel
        # tears it down — so the disconnect-side steps (debrief, DB finalize)
        # run ~5 minutes late and the post-session modal's 90s poll always
        # times out ("Notes unavailable right now."). The transport fires this
        # only when the CLIENT closes the socket (guarded in Pipecat by
        # `if not self._client.is_closing`), so the agent-driven goodbye path —
        # where WE close the socket after the pipeline ends — can't re-enter.
        # cancel(), not stop_when_done(): the listener is gone, there is no
        # audio worth finishing, and cancel is the same path the idle-timeout
        # took, after which the whole `finally:` below already runs cleanly.
        @transport.event_handler("on_client_disconnected")
        async def _on_client_disconnected(_transport, _client):
            try:
                await task.cancel()
            except Exception as e:  # noqa: BLE001 — must not crash the event-handler path
                logger.warning(
                    f"task.cancel() on client disconnect failed (non-fatal): "
                    f"{type(e).__name__}: {e}"
                )

        runner = PipelineRunner()

        await audiobuffer.start_recording()
        ACTIVE_TASKS[user_id] = task  # register so /say can inject typed turns
        ACTIVE_LESSONS[user_id] = lesson_id  # AGENT-001: this session's runtime language
        ACTIVE_WRAPPERS[user_id] = wrapper  # REL-002: kept in lockstep — same guard, same branch, below
        # Langfuse: let sibling HTTP requests (Clara's exercise routes) join
        # this conversation's Session. Prefixed form — teacher/routes.py only
        # ever uses this value as a `langfuse.session.id` span attribute.
        # Cleared in lockstep with the two above.
        register_trace_session(user_id, trace_session_id)

        # Wall-clock session cap (SEC-001 + MEMORY-002). The public demo route
        # always passed a timeout; authenticated /learn routes passed None
        # until MEMORY-002 (2026-09-01) gave them LEARNED_SESSION_TIMEOUT_S
        # (default 2h) — an abandoned tab used to keep a zombie pipeline (and
        # its growing audio buffers) alive for as long as the half-open
        # socket lasted. Cancelled first thing in `finally` so a session
        # that ends on its own never races the watchdog.
        watchdog = (
            asyncio.create_task(_session_watchdog(task, session_timeout_s, user_id))
            if session_timeout_s is not None
            else None
        )

        # BUG-009 keepalive. Started here — right after the pipeline task
        # exists — for every connection (not gated on lesson type or a demo
        # timeout like the watchdog above): any idle stretch on any lesson can
        # hit Railway's edge idle timeout. Cancelled in `finally` below.
        heartbeat_task = asyncio.create_task(
            _rtvi_heartbeat(rtvi_processor, wrapper, user_id)
        )

        # AGENT-001: makes the agent speak first when the lesson's YAML sets
        # a `kickoff:` key (today, only the teacher — Clara; every other
        # lesson leaves `wrapper._kickoff` None and gets no task at all,
        # keeping today's silence-until-the-learner-speaks behavior
        # unchanged). Data-driven per CLAUDE.md: no lesson_id check here,
        # just the wrapper field `ClientWrapper.__init__` already derived
        # from `load_prompts(lesson_id)`. Cancelled in `finally` below,
        # same lifecycle as `heartbeat_task` and `watchdog`.
        kickoff_task = (
            asyncio.create_task(_kickoff_turn(task, wrapper._kickoff, user_id))
            if wrapper._kickoff
            else None
        )

        logger.info(
            f"Client connected: user_id={user_id} session_id={session_id} | "
            f"lesson={lesson_id} voice={voice}{_rss_log_suffix()}"
        )

        # Hoisted out of the inner try-blocks so the DB finalize step below
        # can read them. They stay None when the corresponding evaluator
        # didn't run (no goals / no locale / evaluator crashed).
        result = None
        pron_result = None
        error_result = None
        # The tandem debrief (Phase 4) stores an already-enriched dict (labels +
        # per-pattern `retired` flags) rather than a bare model dump, so it lands
        # in `error_eval` on its own path below. Mutually exclusive with the
        # drill `error_result` — a session is one lesson, tandem or drill.
        debrief_eval_dict = None
        # Captures any exception from runner.run so the DB finalize can record
        # ended_by="crash". We re-raise immediately so the caller (the WS endpoint)
        # still sees the failure.
        exception_during_run = None
        try:
            await runner.run(task)
        except BaseException as e:
            exception_during_run = e
            raise
        finally:
            if watchdog is not None:
                watchdog.cancel()
            heartbeat_task.cancel()  # BUG-009 — per-client, never outlives this connection
            if kickoff_task is not None:
                kickoff_task.cancel()  # AGENT-001 — per-client, never outlives this connection
            # Identity-guarded: if the same user opened a second tab, its
            # connect overwrote our entry — popping blindly here would break
            # /say for that still-live session (BUG-004). SESS-001 now rejects
            # the overwrite up front for authenticated users (the 4004 gate
            # above); this guard stays as defense-in-depth for the demo
            # exemption and the accepted check-to-register race.
            if ACTIVE_TASKS.get(user_id) is task:
                ACTIVE_TASKS.pop(user_id, None)
                ACTIVE_LESSONS.pop(user_id, None)  # kept in lockstep — same guard, same branch
                ACTIVE_WRAPPERS.pop(user_id, None)  # REL-002: kept in lockstep — same guard, same branch
            # Value-guarded internally (only removes if still OUR session id),
            # so it is safe outside the identity guard above. Must match what
            # register_trace_session stored above (the prefixed form).
            clear_trace_session(user_id, trace_session_id)
            # MEMORY-003: per-session RSS at teardown — paired with the
            # connect-time reading, this is the whole leak story per session
            # in two log lines.
            logger.info(
                f"Session ended: user_id={user_id} session_id={session_id} | "
                f"lesson={lesson_id}{_rss_log_suffix()}"
            )
            # B3: must not be bare — an unhandled raise here would skip every
            # step after it in this `finally` (evaluators, DB finalize, OTel
            # flush), leaving the activity_session row ended_at=NULL forever.
            try:
                await audiobuffer.stop_recording()
            except Exception:  # noqa: BLE001 — audio buffer stop must not block cleanup
                logger.exception("Audio buffer stop_recording failed (non-fatal)")
            # Post-session evaluators (EVAL-001 / GRAM-001 Phase 2 / TANDEM-001
            # Phase 4 / PRON-001) — OBS-009: previously ran one after another
            # and were the bulk of the "Analyzing your session…" wall-clock
            # time; now parallelized under one parent span. Each step below
            # keeps its EXACT non-fatal try/except + logging contract from
            # before (same `except Exception as e:` catch, same messages) —
            # only the scheduling changed. Every step that touches the DB
            # already opened its OWN standalone `get_sessionmaker()()` session
            # scoped to its own try block (no session was ever shared between
            # these steps), so running them concurrently needs no new
            # session-isolation work. OTel propagates the current span via
            # contextvars into the tasks `asyncio.gather` spawns, so each
            # step's own `generation_span` calls auto-nest under
            # post-session-analysis with no manual context passing.
            with tracer.start_as_current_span("post-session-analysis") as analysis_span:
                analysis_span.set_attribute("langfuse.session.id", trace_session_id)
                analysis_span.set_attribute("user.id", user_id)
                analysis_span.set_attribute("lesson_id", lesson_id)

                # AUDIO-COST-001: session-level audio cost, independent of the
                # evaluator gate below — runs for every lesson type, Clara
                # included, since audio cost is real regardless of assessment
                # exemption. STT: what AudioSecondsMeter actually measured
                # reaching Deepgram this session (piece B above), priced at
                # the nova-3 STREAMING rate. TTS: total characters across bot
                # turns (ClientWrapper.bot_character_count — an approximation
                # of billed characters, see that method's docstring), priced
                # at the MiniMax per-char rate. Same non-fatal contract as
                # every other step in this block.
                try:
                    stt_seconds = round(audio_meter.total_seconds, 2)
                    tts_chars = wrapper.bot_character_count()
                    usage: dict = {}
                    cost: dict = {}
                    if stt_seconds > 0:
                        stt_usd = stt_cost_usd(stt_seconds, DEEPGRAM_NOVA3_STREAMING_PER_MIN)
                        usage["stt_audio_seconds"] = stt_seconds
                        cost["stt_audio_seconds"] = stt_usd
                    if tts_chars > 0:
                        tts_usd = tts_cost_usd(tts_chars)
                        usage["tts_characters"] = tts_chars
                        cost["tts_characters"] = tts_usd
                    if usage:
                        cost["total"] = sum(cost.values())
                        stamp_audio_cost(analysis_span, usage=usage, cost=cost)
                except Exception as e:  # noqa: BLE001 — cost stamping must not block cleanup
                    logger.warning(
                        f"Audio cost stamping failed (non-fatal): {type(e).__name__}: {e}"
                    )

                # AGENT-001.2 invariant: the teacher room (Clara, lesson_id
                # "teacher") is deliberately exempt from every learner-
                # assessment path below — it's a room for understanding, not
                # assessment, and nothing said there is ever written to the
                # user_errors ledger. Today that exemption is only an
                # accident of missing YAML keys: teacher.yaml has no `goals`
                # (load_goal() -> None, so _run_goal_eval and the drill
                # harvester inside _run_grammar_or_tandem both skip), no
                # `locale` (load_pronunciation_locale() -> None, so
                # _run_pronunciation skips), and its `type` is "teacher", not
                # "tandem" (so the debrief branch in _run_grammar_or_tandem
                # skips too). Any NEW evaluator added to this gather() must
                # preserve that exemption explicitly — do not assume a future
                # YAML edit keeps it true by accident.

                async def _run_goal_eval() -> None:
                    """Post-session evaluator (EVAL-001). Best-effort: any
                    failure here (LLM outage, missing goal entry, network) is
                    logged and swallowed so the other steps and DB finalize
                    always run."""
                    nonlocal result
                    try:
                        goal = load_goal(lesson_id)
                        if wrapper._transcript and goal is not None:
                            result = await evaluate(
                                transcript=wrapper.render_transcript(),
                                goals=goal["goals"],
                                pass_threshold=goal["pass_threshold"],
                                # Eval language tracks the lesson's runtime language, so a
                                # German lesson is judged as German and an English one as
                                # English during the mixed-content migration window.
                                language="German" if lesson_lang == "de" else "English",
                                session_id=trace_session_id,
                            )
                            passed_count = sum(1 for g in result.goals if g.passed)
                            logger.info(
                                f"Evaluation: passed={result.passed} "
                                f"score={result.score}/{result.pass_threshold} "
                                f"goals_passed={passed_count}/{len(result.goals)}"
                            )
                    except Exception as e:  # noqa: BLE001 — evaluator must not block cleanup
                        logger.warning(f"Evaluator failed (non-fatal): {type(e).__name__}: {e}")

                async def _run_grammar_or_tandem() -> None:
                    """Grammar-error harvest (GRAM-001 Phase 2, Harvester B)
                    OR tandem debrief (TANDEM-001 / GRAM-001 Phase 4) — the
                    two are mutually exclusive by lesson-type gating (a
                    session is one lesson, tandem or drill), so both keep
                    living in one task rather than racing each other."""
                    nonlocal error_result, debrief_eval_dict
                    # Same non-fatal contract and goal-gate as the evaluator
                    # above, so the goal-less lessons (lesson_zero / welcome /
                    # goodbye_test) are auto-excluded — no new YAML field.
                    # Classified slips land as `error_eval` on the row AND
                    # upsert the ledger with source="situation"; deliberately
                    # NOT surfaced in the drill modal (one feedback voice per
                    # mode). The ledger keys on `db_user_id` (the real users
                    # row, like the activity_session FK), never the
                    # per-session `user_id`.
                    try:
                        if wrapper._transcript and load_goal(lesson_id) is not None:
                            error_result = await extract_errors(
                                transcript=wrapper.render_transcript(),
                                session_id=trace_session_id,
                                user_id=db_user_id,
                                # STT-006: a voice transcript — the leading-word
                                # ASR-dropout guards apply, as for satz/szenario.
                                check_asr_artifacts=True,
                            )
                            if error_result.errors:
                                async with get_sessionmaker()() as db:
                                    for err in error_result.errors:
                                        # Each upsert is its own commit, wrapped so one
                                        # bad row can't abort the rest of the harvest.
                                        try:
                                            await record_grammar_error(
                                                db,
                                                user_id=db_user_id,
                                                pattern_id=err.pattern_id,
                                                sentence=err.sentence,
                                                corrected=err.corrected,
                                                note=err.note,
                                                source="situation",
                                                session_id=trace_session_id,
                                            )
                                        except Exception:  # noqa: BLE001 — one ledger row must not block the rest
                                            logger.exception(
                                                f"Grammar-ledger write failed (pattern {err.pattern_id})"
                                            )
                            logger.info(
                                f"Grammar harvest: patterns={len(error_result.errors)} "
                                f"lesson={lesson_id}"
                            )
                    except Exception as e:  # noqa: BLE001 — harvester must not block cleanup
                        logger.warning(f"Grammar extractor failed (non-fatal): {type(e).__name__}: {e}")

                    # Gated on the tandem lesson type — tandem has no goals
                    # and no locale, so goal eval, drill harvest, and pron
                    # assessment all auto-skip and the debrief is its ONE
                    # feedback voice. One structured-output call judges the
                    # session's target patterns (the same `grammar_focus` the
                    # prompt steered toward), harvests new errors, and writes
                    # a memory note; the streak/retire lifecycle is applied to
                    # the ledger right here. Runs for ALL end reasons (a
                    # user-ended tandem still gets its debrief). Same
                    # non-fatal contract as the evaluators above; the
                    # enriched result (taxonomy labels + per-pattern
                    # `retired` flags) is stored as `error_eval` for the
                    # debrief modal, and its `session_note` is what
                    # `load_tandem_notes` feeds into the next session's
                    # memory layer.
                    try:
                        if (
                            wrapper._transcript
                            and lesson_snapshot.get("type") == "tandem"
                            and wrapper._exchange_count < TANDEM_STREAK_MIN_EXCHANGES
                        ):
                            # LEDGER-002(c): a hello-and-quit tandem carries no
                            # real evidence to harvest — reuse the same floor
                            # GAME-001's streak credit already applies (below,
                            # at the `TANDEM_STREAK_MIN_EXCHANGES` check near
                            # the end of this function) rather than hardcoding
                            # a second `5`. Skip the debrief entirely: no LLM
                            # call, no ledger write, no drill_attempts mirror —
                            # a thin debrief off 0-1 turns is worse than none
                            # (CLAUDE.md's user_errors rule: a wrong row is
                            # worse than a wrong verdict, and the verdict here
                            # would be near-random on so little transcript).
                            logger.info(
                                f"Tandem debrief skipped: {wrapper._exchange_count} "
                                f"exchange(s) < floor {TANDEM_STREAK_MIN_EXCHANGES} (LEDGER-002)"
                            )
                        elif wrapper._transcript and lesson_snapshot.get("type") == "tandem":
                            focus = wrapper.context.grammar_focus
                            debrief_result = await run_debrief(
                                transcript=wrapper.render_transcript(),
                                focus=focus,
                                topic=topic,
                                session_id=trace_session_id,
                                # TAND-008: the judge reads Bot: lines as this
                                # partner. Lena's YAML predates the field.
                                partner=lesson_snapshot.get("partner_name", "Lena"),
                            )
                            retired_ids: set[str] = set()
                            async with get_sessionmaker()() as db:
                                # Target patterns: a clean spontaneous production advances
                                # the streak (retire at 2); a slip reopens/resets exactly
                                # like a fresh recurrence (record_grammar_error). Un-elicited
                                # targets are left untouched — no evidence either way.
                                for p in debrief_result.patterns:
                                    if not p.elicited:
                                        continue
                                    try:
                                        if p.produced_correctly:
                                            status = await credit_pattern_success(
                                                db,
                                                user_id=db_user_id,
                                                pattern_id=p.pattern_id,
                                                session_id=trace_session_id,
                                            )
                                            if status == "retired":
                                                retired_ids.add(p.pattern_id)
                                        elif p.evidence and p.evidence.strip().lower() != "none":
                                            await record_grammar_error(
                                                db,
                                                user_id=db_user_id,
                                                pattern_id=p.pattern_id,
                                                sentence=p.evidence,
                                                corrected=p.corrected or None,
                                                note=p.note or None,
                                                source="tandem",
                                                session_id=trace_session_id,
                                            )
                                    except Exception:  # noqa: BLE001 — one row must not block the rest
                                        logger.exception(
                                            f"Tandem ledger write failed (target {p.pattern_id})"
                                        )
                                    # DATA-008: mirror a missed target pattern
                                    # with a drill_attempts row too — the
                                    # tandem debrief previously never called
                                    # record_drill_attempt at all, so its
                                    # correctly-earned ledger rows were
                                    # structurally invisible to
                                    # load_top_errors/GET /me/stats (which
                                    # require a non-null pattern_id AND
                                    # correct=False on a drill_attempts row).
                                    # Own try/except, same non-fatal contract
                                    # — must never block the ledger write
                                    # above.
                                    try:
                                        if (
                                            p.elicited
                                            and not p.produced_correctly
                                            and p.evidence
                                            and p.evidence.strip().lower() != "none"
                                        ):
                                            await record_drill_attempt(
                                                db,
                                                user_id=db_user_id,
                                                exercise="tandem",
                                                item_ref=p.pattern_id,
                                                pattern_id=p.pattern_id,
                                                correct=False,
                                                modality="spoken",
                                                session_id=trace_session_id,
                                            )
                                    except Exception:  # noqa: BLE001 — one row must not block the rest
                                        logger.exception(
                                            f"Tandem drill-attempt log write failed (target {p.pattern_id})"
                                        )
                                # New (non-target) errors: plain ledger upserts, same as
                                # the drill harvester, source="tandem".
                                for e in debrief_result.new_errors:
                                    try:
                                        await record_grammar_error(
                                            db,
                                            user_id=db_user_id,
                                            pattern_id=e.pattern_id,
                                            sentence=e.sentence,
                                            corrected=e.corrected,
                                            note=e.note,
                                            source="tandem",
                                            session_id=trace_session_id,
                                        )
                                    except Exception:  # noqa: BLE001 — one row must not block the rest
                                        logger.exception(
                                            f"Tandem ledger write failed (new {e.pattern_id})"
                                        )
                                    # DATA-008: same drill_attempts mirror as the
                                    # target-pattern branch above, for
                                    # un-elicited/new errors — own try/except,
                                    # must never block the ledger write above.
                                    try:
                                        await record_drill_attempt(
                                            db,
                                            user_id=db_user_id,
                                            exercise="tandem",
                                            item_ref=e.pattern_id,
                                            pattern_id=e.pattern_id,
                                            correct=False,
                                            modality="spoken",
                                            session_id=trace_session_id,
                                        )
                                    except Exception:  # noqa: BLE001 — one row must not block the rest
                                        logger.exception(
                                            f"Tandem drill-attempt log write failed (new {e.pattern_id})"
                                        )
                            # Enrich for the debrief modal: taxonomy labels + which
                            # patterns retired this session. `session_note` rides along in
                            # the stored payload (load_tandem_notes reads it) but the modal
                            # keeps it private.
                            catalog = load_taxonomy()

                            def _label(pid: str) -> str:
                                pat = catalog.get(pid)
                                return pat["label"] if pat else pid

                            debrief_eval_dict = {
                                "kind": "tandem_debrief",
                                "session_note": debrief_result.session_note,
                                "patterns": [
                                    {
                                        "pattern_id": p.pattern_id,
                                        "label": _label(p.pattern_id),
                                        "elicited": p.elicited,
                                        "produced_correctly": p.produced_correctly,
                                        "evidence": p.evidence,
                                        "corrected": p.corrected,
                                        "note": p.note,
                                        "retired": p.pattern_id in retired_ids,
                                    }
                                    for p in debrief_result.patterns
                                ],
                                "new_errors": [
                                    {
                                        "pattern_id": e.pattern_id,
                                        "label": _label(e.pattern_id),
                                        "sentence": e.sentence,
                                        "corrected": e.corrected,
                                        "note": e.note,
                                    }
                                    for e in debrief_result.new_errors
                                ],
                            }
                            logger.info(
                                f"Tandem debrief: targets={len(debrief_result.patterns)} "
                                f"retired={len(retired_ids)} new={len(debrief_result.new_errors)} "
                                f"note={'yes' if debrief_result.session_note else 'no'}"
                            )
                    except Exception as e:  # noqa: BLE001 — debrief must not block cleanup
                        logger.warning(f"Tandem debrief failed (non-fatal): {type(e).__name__}: {e}")

                async def _run_pronunciation() -> None:
                    """Post-session pronunciation assessment (PRON-001). Same
                    non-fatal contract as the goal evaluator above: any
                    failure (missing key, Azure outage, count mismatch) is
                    logged and swallowed so audio export and logger close
                    still run."""
                    nonlocal pron_result
                    try:
                        locale = load_pronunciation_locale(lesson_id)
                        if locale is not None and wrapper.has_user_turn_audio():
                            pron_result = await assess_pronunciation(
                                user_turns=wrapper.iter_user_turn_audio(),
                                locale=locale,
                                session_id=trace_session_id,
                            )
                            logger.info(
                                f"Pronunciation: locale={pron_result.locale} "
                                f"pron={pron_result.aggregate.pron_score:.1f} "
                                f"acc={pron_result.aggregate.accuracy_score:.1f} "
                                f"turns_assessed={pron_result.aggregate.turns_assessed}"
                            )
                    except Exception as e:  # noqa: BLE001 — pronunciation must not block cleanup
                        logger.warning(f"Pronunciation assessment failed (non-fatal): {type(e).__name__}: {e}")

                # Each wrapper above is already fully non-fatal (every branch
                # is caught inside its own try/except and only logs), so
                # nothing propagates out of these coroutines — no
                # `return_exceptions=True` needed.
                await asyncio.gather(
                    _run_goal_eval(),
                    _run_grammar_or_tandem(),
                    _run_pronunciation(),
                )
            # Finalize the activity_session row (DATA-001). Runs after both
            # evaluators so their results land in the same UPDATE. Same non-fatal
            # contract: a DB outage logs a warning and we proceed to logger
            # close + OTel flush.
            try:
                if exception_during_run is not None:
                    ended_by = "crash"
                elif wrapper._end_pending:
                    ended_by = "agent"
                else:
                    # User clicked Finish, tab closed, or network dropped —
                    # the server can't tell these apart, see plan §Risks.
                    ended_by = "user"
                goal_eval_dict = result.model_dump() if result is not None else None
                pron_eval_dict = pron_result.model_dump() if pron_result is not None else None
                # error_eval carries the drill harvest (a model dump) OR the
                # tandem debrief (an already-enriched dict) — never both in one
                # session. The tandem dict wins when present.
                if debrief_eval_dict is not None:
                    error_eval_dict = debrief_eval_dict
                elif error_result is not None:
                    error_eval_dict = error_result.model_dump()
                else:
                    error_eval_dict = None
                passed = goal_eval_dict["passed"] if goal_eval_dict is not None else None
                async with get_sessionmaker()() as db:
                    # PAY-002 disconnect charge: one ledger row for the whole
                    # voice session, priced at exchange_count × VOICE_EXCHANGE.
                    # Capped at available (spend_capped) — multi-tab abuse
                    # (LEARN_MAX_CONCURRENT_PER_USER=3) can otherwise open
                    # concurrent sessions each admitted against the same
                    # pre-session balance, then disconnect all three and owe
                    # more than what's left. Never goes negative. Demo socket
                    # is never charged; developer role bypasses. Typed Practice-
                    # mode /say turns count as exchanges too — same session,
                    # same price (ClientWrapper._exchange_count already
                    # counts them). Crash-mid-session = uncharged partial
                    # session (consistent with the existing non-fatal
                    # disconnect-write philosophy — the accept gate already
                    # bounded the worst-case exposure before any audio ran).
                    # Must run BEFORE finalize_session_row so the same DB
                    # session/transaction covers both.
                    if (
                        db_user_id != "demo"
                        and lesson_snapshot.get("type")
                        in ("tandem", "conversation", "respond")
                    ):
                        try:
                            from coins.engine import spend_capped as _spend_capped
                            from coins.prices import VOICE_EXCHANGE as _VE2

                            _owed = int(wrapper._exchange_count or 0) * _VE2
                            if _owed > 0:
                                await _spend_capped(
                                    db,
                                    user_id=db_user_id,
                                    owed=_owed,
                                    kind="spend_voice",
                                    ref=session_id,
                                )
                        except Exception as _ce:  # noqa: BLE001 — charge must not block finalize
                            logger.warning(
                                f"coin voice disconnect charge failed (non-fatal): {_ce}"
                            )
                    await finalize_session_row(
                        db,
                        session_id=session_id,
                        ended_at=datetime.now(),
                        ended_by=ended_by,
                        transcript=wrapper.render_transcript() if wrapper._transcript else None,
                        goal_eval=goal_eval_dict,
                        pron_eval=pron_eval_dict,
                        error_eval=error_eval_dict,
                        passed=passed,
                    )
                    # GAME-001 v3: only a SUBSTANTIAL tandem conversation
                    # credits the "tandem" mode slot. v2's rule (any non-empty
                    # transcript) let a two-line drive-by count as the day's
                    # tandem — now it takes at least TAND-012's shortest full
                    # session (5 exchanges), or an agent-driven end, which
                    # means the conversation ran to its planned close (a
                    # 5-exchange session can legitimately close via goodbye
                    # at exchange 4, and must still count). Deliberately
                    # excluded, unchanged from v2: the demo sentinel (no
                    # streak at all), Clara (`type: teacher`, not a tandem
                    # lesson_id — she's exempt from every evaluator, this
                    # included), and the /learn lessons (not in
                    # tandem_lesson_ids()).
                    if (
                        db_user_id != "demo"
                        and lesson_id in tandem_lesson_ids()
                        and (
                            wrapper._exchange_count >= TANDEM_STREAK_MIN_EXCHANGES
                            or wrapper._end_pending
                        )
                    ):
                        await complete_daily_mode(
                            db, user_id=db_user_id, mode="tandem"
                        )
            except (SQLAlchemyError, OSError) as e:  # noqa: BLE001 — non-fatal
                logger.warning(
                    f"DB session finalize failed (non-fatal): {type(e).__name__}: {e}"
                )
            # B1: force_flush() is bounded (timeout_millis=2000 in
            # agents/observability.py) but still a synchronous, thread-blocking
            # OTel call — run it off the event loop so a slow Langfuse OTLP
            # endpoint can't freeze every other connected client's pipeline.
            await asyncio.to_thread(flush_traces)
            try:
                detach_trace_context(_trace_ctx_token)
            except Exception:  # noqa: BLE001 — context detach must not block cleanup
                logger.exception("Trace-context detach failed (non-fatal)")
            logger.info(f"Client disconnected: {user_id}")
