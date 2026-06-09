import asyncio
import wave
from datetime import datetime
from uuid import uuid4

import aiohttp
from loguru import logger
from pydub import AudioSegment
from sqlalchemy.exc import SQLAlchemyError

from services import stt_deepgram, tts_minimax, transport_fastapi_ws

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

from .converters import TranscriptionToContextConverter
from .observers import PipelineLatencyObserver
from .tts_duration import TTSDurationTracker

from agents import ClientWrapper, CONVERSATIONAL_MODEL
from agents.evaluator import evaluate
from agents.load_goals import load_goal
from agents.load_prompts import load_prompts
from agents.load_pronunciation import load_pronunciation_locale
from agents.pronunciation import assess_pronunciation
from agents.observability import flush_traces

from database import create_session_row, finalize_session_row, get_sessionmaker

from logs import setup_session_logger


# Live pipeline tasks keyed by user_id. Used by /say/{user_id} in main.py
# to inject typed turns into an active session. Per-client isolation rule
# from CLAUDE.md still holds — this is just a lookup, not shared state.
ACTIVE_TASKS: dict[str, PipelineTask] = {}


class _NoOpTurnTraceObserver:
    """Stub installed on `PipelineTask._turn_trace_observer` to satisfy
    Pipecat's cleanup path (task.py:670–671), which calls
    ``end_conversation_tracing()`` whenever ``enable_tracing=True`` without
    checking whether the observer is actually a real instance. We use
    ``enable_turn_tracking=False`` so Pipecat's observer is never built;
    our own ``PipelineLatencyObserver`` owns the conversation/turn spans."""

    def end_conversation_tracing(self):
        pass


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


async def run_pipeline(websocket, user_id: str, voice: str = "happy_harry", lesson_id: str = "lesson_zero", session_timeout_s: float | None = None, db_user_id: str | None = None):
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

    async with aiohttp.ClientSession() as session:

        # Transport: one per client (wraps this specific websocket)
        transport = transport_fastapi_ws(websocket)

        # Fresh services per client
        stt = stt_deepgram()
        tts = tts_minimax(session, voice=voice)
        converter = TranscriptionToContextConverter()

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
        lesson_snapshot = load_prompts(lesson_id)
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

        # Per-client wrapper (agent + logger + context settings inside)
        wrapper = ClientWrapper(user_id=user_id, session_id=session_id, logger=session_logger, voice=voice, lesson_id=lesson_id)
        llm = LangchainProcessor(chain=wrapper)

        # Per-client audio recorder.
        # `sample_rate=16000` matches Deepgram nova-2 (no resampling cost) and
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

        pipeline = Pipeline([
            transport.input(),
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
        runner = PipelineRunner()

        await audiobuffer.start_recording()
        ACTIVE_TASKS[user_id] = task  # register so /say can inject typed turns

        # Wall-clock session cap (SEC-001). Armed only when a timeout is passed
        # (the public demo route does; /learn passes None → no watchdog, so its
        # behavior is unchanged). Cancelled first thing in `finally` so a session
        # that ends on its own never races the watchdog.
        watchdog = (
            asyncio.create_task(_session_watchdog(task, session_timeout_s, user_id))
            if session_timeout_s is not None
            else None
        )

        print(f"Client connected: user_id={user_id} session_id={session_id} | lesson={lesson_id} voice={voice}")

        # Hoisted out of the inner try-blocks so the DB finalize step below
        # can read them. They stay None when the corresponding evaluator
        # didn't run (no goals / no locale / evaluator crashed).
        result = None
        pron_result = None
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
            # Identity-guarded: if the same user opened a second tab, its
            # connect overwrote our entry — popping blindly here would break
            # /say for that still-live session (BUG-004).
            if ACTIVE_TASKS.get(user_id) is task:
                ACTIVE_TASKS.pop(user_id, None)
            await audiobuffer.stop_recording()
            # Post-session evaluator (EVAL-001). Best-effort: any failure here
            # (LLM outage, missing goal entry, network) is logged and swallowed
            # so audio export and logger close always run.
            try:
                goal = load_goal(lesson_id)
                if wrapper._transcript and goal is not None:
                    result = await evaluate(
                        transcript=wrapper.render_transcript(),
                        goals=goal["goals"],
                        pass_threshold=goal["pass_threshold"],
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
            # Post-session pronunciation assessment (PRON-001). Same non-fatal
            # contract as the goal evaluator above: any failure (missing key,
            # Azure outage, count mismatch) is logged and swallowed so audio
            # export and logger close still run.
            try:
                locale = load_pronunciation_locale(lesson_id)
                if locale is not None and wrapper.has_user_turn_audio():
                    pron_result = await assess_pronunciation(
                        user_turns=wrapper.iter_user_turn_audio(),
                        locale=locale,
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
                        passed=passed,
                    )
            except (SQLAlchemyError, OSError) as e:  # noqa: BLE001 — non-fatal
                logger.warning(
                    f"DB session finalize failed (non-fatal): {type(e).__name__}: {e}"
                )
            session_logger.close()
            flush_traces()  # drain queued OTel spans before the process moves on
            print(f"Client disconnected: {user_id}")
