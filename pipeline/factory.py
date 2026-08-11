import asyncio
import wave
from datetime import datetime
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

from .converters import TranscriptionToContextConverter
from .observers import PipelineLatencyObserver
from .tts_duration import TTSDurationTracker

from agents import ClientWrapper, CONVERSATIONAL_MODEL
from agents.evaluator import evaluate
from agents.error_extractor import extract_errors
from agents.debrief import debrief as run_debrief
from agents.load_goals import load_goal
from agents.load_prompts import list_lesson_ids, load_prompts, tandem_lesson_ids
from agents.load_pronunciation import load_pronunciation_locale
from agents.pronunciation import assess_pronunciation
from agents.observability import flush_traces, tracer

from grammar import load_taxonomy

from database import create_session_row, finalize_session_row, get_sessionmaker
from database.repository import (
    complete_daily_mode,
    credit_pattern_success,
    load_grammar_focus,
    load_tandem_notes,
    load_vocab_words,
    record_grammar_error,
)

from logs import setup_session_logger


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


async def run_pipeline(websocket, user_id: str, voice: str = "happy_harry", lesson_id: str = "lesson_zero", topic: str = "", session_timeout_s: float | None = None, db_user_id: str | None = None, exchanges: int | None = None):
    """Builds and runs a full pipeline for a single client connection."""
    # One Langfuse Session per WebSocket connection. `user_id` is stable across
    # connections (per-tab UUID today, auth-derived later); `session_id` resets
    # on every Connect so the Langfuse UI shows one Session per conversation.
    # The same uuid is fed to Pipecat as `conversation_id`, so 1 connect = 1
    # conversation trace = 1 Langfuse session.
    session_id = uuid4().hex

    # Which `users` row this session's DB record FKs to. Defaults to `user_id`
    # (authed /learn → the real user). The public demo route passes
    # db_user_id="demo" so all anonymous visitors share one seeded sentinel row
    # (AUTH-001) instead of minting a `users` row per visitor; `user_id` itself
    # stays per-session for ACTIVE_TASKS routing, Langfuse, and the wrapper.
    db_user_id = db_user_id or user_id

    # B2: bound the MiniMax TTS session's requests — with no timeout a hung
    # MiniMax request falls back to aiohttp's 5-minute default, stalling this
    # client's turn (and, if awaited on the event loop elsewhere, worse).
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:

        # Transport: one per client (wraps this specific websocket)
        transport = transport_fastapi_ws(websocket)

        # Fresh services per client. Language is per-lesson: German runtime,
        # English only for the `welcome` concierge and other English exceptions.
        # The snapshot is loaded first because `load_prompts` falls back to
        # lesson_zero on an unknown id — the language must follow that fallback
        # (German STT over the English lesson_zero prompt would transcribe the
        # user's English as noise), so it derives from the id of the lesson
        # that actually loaded, not the raw query param.
        lesson_snapshot = load_prompts(lesson_id)
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

        # TAND-009 (2026-08-11): per-session exchange cap for tandem, whitelisted
        # to {5, 10, 15} by the frontend/main.py query param. Only tandem lessons
        # honor it — anything else (including a stray query param on a non-tandem
        # /learn connect) leaves the YAML's own max_exchanges untouched. Mutating
        # the snapshot dict (not just a local var) keeps the DB `lesson_snapshot`
        # column faithful to what the session actually ran with — `load_prompts`
        # returns a fresh dict per call, so this never leaks into another session.
        exchanges_override = (
            exchanges
            if lesson_snapshot.get("type") == "tandem" and exchanges in (5, 10, 15)
            else None
        )
        if exchanges_override is not None:
            lesson_snapshot["max_exchanges"] = exchanges_override

        # Per-client logger
        session_logger = setup_session_logger(stt, tts, CONVERSATIONAL_MODEL)

        # Insert the activity_session row at connect (DATA-001). Non-fatal:
        # if the DB is down we log a warning and continue — audio export,
        # evaluators, logger close, and OTel flush MUST still run. The row
        # is then UPDATEd on disconnect with transcript + eval results.
        # ``lesson_snapshot`` freezes the YAML at session start so future
        # history UI shows what the user actually saw, even if the YAML
        # changes later.
        started_at = datetime.now()
        audio_path = str(
            (session_logger.session_dir / session_logger.session_id).with_suffix(".mp3")
        )
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

        # Ledger-backed prompt layers. The prompt middleware is sync, so these
        # async DB reads happen here, once at connect, and ride on Context.
        # Tandem lessons (TANDEM-001) carry all three; the teacher (AGENT-001)
        # carries only the grammar focus — no debrief means no notes, and vocab
        # weaving is a tandem behaviour, not a teaching one. Non-fatal: a DB
        # hiccup just means a less-personalised chat, never a failed connect.
        grammar_focus: list = []
        session_notes: list = []
        vocab_words: list = []
        snapshot_type = lesson_snapshot.get("type")
        if snapshot_type in ("tandem", "teacher"):
            try:
                async with get_sessionmaker()() as db:
                    grammar_focus = await load_grammar_focus(db, user_id=db_user_id)
                    if snapshot_type == "tandem":
                        # TAND-008: memory is per partner — Paul's notes never
                        # reach Lena's prompt and vice versa.
                        session_notes = await load_tandem_notes(
                            db, user_id=db_user_id, lesson_id=lesson_id
                        )
                        # TAND-002: 7, not the default 10 — fewer words lowers the
                        # cramming pressure on Lena's short replies (vocab dose).
                        # TAND-009: a shorter session can't naturally fit 7 deck
                        # words, so scale down with the exchange cap — fewer
                        # exchanges means fewer words to weave, so the partner
                        # never crams.
                        vocab_limit = {5: 4, 10: 7, 15: 10}.get(exchanges_override or 0, 7)
                        vocab_words = await load_vocab_words(db, user_id=db_user_id, limit=vocab_limit)
                logger.info(
                    f"{snapshot_type.capitalize()} layers: focus_patterns={len(grammar_focus)} "
                    f"notes={len(session_notes)} vocab_words={len(vocab_words)} "
                    f"topic={topic!r} user={db_user_id}"
                )
            except (SQLAlchemyError, OSError) as e:  # noqa: BLE001 — non-fatal
                logger.warning(
                    f"{snapshot_type.capitalize()} layer fetch failed (non-fatal): {type(e).__name__}: {e}"
                )

        # Per-client wrapper (agent + logger + context settings inside)
        wrapper = ClientWrapper(user_id=user_id, session_id=session_id, logger=session_logger, voice=voice, lesson_id=lesson_id, topic=topic, grammar_focus=grammar_focus, session_notes=session_notes, vocab_words=vocab_words, max_exchanges_override=exchanges_override)
        llm = LangchainProcessor(chain=wrapper)

        # Per-client audio recorder.
        # `sample_rate=16000` matches Deepgram nova-3 (no resampling cost) and
        # is what Azure Pronunciation Assessment expects natively.
        # `enable_turn_audio=True` activates the `_process_turn_recording` code
        # path that fires `on_user_turn_audio_data` on each UserStoppedSpeakingFrame
        # — that event drives the post-session pronunciation evaluator (PRON-001).
        audiobuffer = AudioBufferProcessor(
            num_channels=1,
            sample_rate=16000,
            enable_turn_audio=True,
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
            base_path = session_logger.session_dir / session_logger.session_id
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
                logger.info(f"Audio saved to: {mp3_path}")
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
            session_id=session_id,
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

        pipeline = Pipeline([
            transport.input(),
            stt_mute,          # deafen STT while bot speaks — kills speakerphone echo
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

        # Wall-clock session cap (SEC-001). Armed only when a timeout is passed
        # (the public demo route does; /learn passes None → no watchdog, so its
        # behavior is unchanged). Cancelled first thing in `finally` so a session
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

        logger.info(f"Client connected: user_id={user_id} session_id={session_id} | lesson={lesson_id} voice={voice}")

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
            # /say for that still-live session (BUG-004).
            if ACTIVE_TASKS.get(user_id) is task:
                ACTIVE_TASKS.pop(user_id, None)
                ACTIVE_LESSONS.pop(user_id, None)  # kept in lockstep — same guard, same branch
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
                analysis_span.set_attribute("langfuse.session.id", session_id)
                analysis_span.set_attribute("user.id", user_id)
                analysis_span.set_attribute("lesson_id", lesson_id)

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
                                session_id=session_id,
                            )
                            session_logger.write_evaluation(result)
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
                                session_id=session_id,
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
                                                session_id=session_id,
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
                        if wrapper._transcript and lesson_snapshot.get("type") == "tandem":
                            focus = wrapper.context.grammar_focus
                            debrief_result = await run_debrief(
                                transcript=wrapper.render_transcript(),
                                focus=focus,
                                topic=topic,
                                session_id=session_id,
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
                                                session_id=session_id,
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
                                                session_id=session_id,
                                            )
                                    except Exception:  # noqa: BLE001 — one row must not block the rest
                                        logger.exception(
                                            f"Tandem ledger write failed (target {p.pattern_id})"
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
                                            session_id=session_id,
                                        )
                                    except Exception:  # noqa: BLE001 — one row must not block the rest
                                        logger.exception(
                                            f"Tandem ledger write failed (new {e.pattern_id})"
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
                                session_id=session_id,
                            )
                            session_logger.write_pronunciation(
                                pron_result,
                                dropped_audio=wrapper._dropped_audio_count,
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
                    # GAME-001 v2: only a real tandem conversation credits
                    # the "tandem" mode slot — not the older "any finished
                    # conversation" rule. Deliberately excluded: the demo
                    # sentinel (no streak at all), Clara (`type: teacher`,
                    # not a tandem lesson_id — she's exempt from every
                    # evaluator, this included), the /learn lessons (not in
                    # tandem_lesson_ids()), and a tandem session that never
                    # actually exchanged anything (wrapper._transcript empty
                    # — same truthiness finalize_session_row just used above
                    # for "did anything happen").
                    if (
                        db_user_id != "demo"
                        and lesson_id in tandem_lesson_ids()
                        and wrapper._transcript
                    ):
                        await complete_daily_mode(
                            db, user_id=db_user_id, mode="tandem"
                        )
            except (SQLAlchemyError, OSError) as e:  # noqa: BLE001 — non-fatal
                logger.warning(
                    f"DB session finalize failed (non-fatal): {type(e).__name__}: {e}"
                )
            # B3: same non-fatal contract as above — a close failure must not
            # skip the OTel flush that follows it.
            try:
                session_logger.close()
            except Exception:  # noqa: BLE001 — logger close must not block cleanup
                logger.exception("Session logger close failed (non-fatal)")
            # B1: force_flush() is bounded (timeout_millis=2000 in
            # agents/observability.py) but still a synchronous, thread-blocking
            # OTel call — run it off the event loop so a slow Langfuse OTLP
            # endpoint can't freeze every other connected client's pipeline.
            await asyncio.to_thread(flush_traces)
            logger.info(f"Client disconnected: {user_id}")
