import wave
from uuid import uuid4

import aiohttp
from loguru import logger
from pydub import AudioSegment

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
from agents.load_pronunciation import load_pronunciation_locale
from agents.pronunciation import assess_pronunciation
from agents.observability import flush_traces

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


async def run_pipeline(websocket, user_id: str, level: str = "A1", situation: str = "introducing_yourself", voice: str = "happy_harry", lesson_id: str = "lesson_zero"):
    """Builds and runs a full pipeline for a single client connection."""
    # One Langfuse Session per WebSocket connection. `user_id` is stable across
    # connections (per-tab UUID today, auth-derived later); `session_id` resets
    # on every Connect so the Langfuse UI shows one Session per conversation.
    # The same uuid is fed to Pipecat as `conversation_id`, so 1 connect = 1
    # conversation trace = 1 Langfuse session.
    session_id = uuid4().hex

    async with aiohttp.ClientSession() as session:

        # Transport: one per client (wraps this specific websocket)
        transport = transport_fastapi_ws(websocket)

        # Fresh services per client
        stt = stt_deepgram()
        tts = tts_minimax(session, voice=voice)
        converter = TranscriptionToContextConverter()

        # Per-client logger
        session_logger = setup_session_logger(stt, tts, CONVERSATIONAL_MODEL)

        # Per-client wrapper (agent + logger + context settings inside)
        wrapper = ClientWrapper(user_id=user_id, session_id=session_id, logger=session_logger, level=level, situation=situation, voice=voice, lesson_id=lesson_id)
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

        @audiobuffer.event_handler("on_audio_data")
        async def on_audio_data(buffer, audio, sample_rate, num_channels):
            base_path = session_logger.session_dir / session_logger.session_id
            wav_path = base_path.with_suffix(".wav")
            mp3_path = base_path.with_suffix(".mp3")

            with wave.open(str(wav_path), "wb") as wf:
                wf.setnchannels(num_channels)
                wf.setsampwidth(2)
                wf.setframerate(sample_rate)
                wf.writeframes(audio)

            audio_segment = AudioSegment.from_wav(str(wav_path))
            audio_segment.export(str(mp3_path), format="mp3", bitrate="128k")
            wav_path.unlink()
            print(f"Audio saved to: {mp3_path}")

        @audiobuffer.event_handler("on_user_turn_audio_data")
        async def on_user_turn_audio(_buffer, audio, sample_rate, num_channels):
            """Stash the user's just-completed turn audio on the wrapper for
            the post-session pronunciation evaluator (PRON-001). Fires on
            UserStoppedSpeakingFrame; verified at
            `.venv/.../pipecat/processors/audio/audio_buffer_processor.py:248`.
            """
            wrapper.append_user_turn_audio(bytes(audio), sample_rate)

        # RTVI processor + observer. The observer handles user transcripts;
        # bot text is pushed by ClientWrapper itself (one message per turn) to
        # avoid the framework's dual-path duplicate (LLM-side AggregatedTextFrame
        # AND TTS-side TTSTextFrame both observed).
        rtvi_processor = RTVIProcessor()
        rtvi_observer = RTVIObserver(
            rtvi=rtvi_processor,
            params=RTVIObserverParams(
                user_transcription_enabled=True,
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

        # Give the wrapper the processor reference so it can push bot output.
        wrapper.rtvi_processor = rtvi_processor

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
            level=level,
            situation=situation,
            voice=voice,
            tts_service=tts,
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

        print(f"Client connected: user_id={user_id} session_id={session_id} | lesson={lesson_id} level={level} situation={situation} voice={voice}")

        try:
            await runner.run(task)
        finally:
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
                    session_logger.write_pronunciation(pron_result)
                    logger.info(
                        f"Pronunciation: locale={pron_result.locale} "
                        f"pron={pron_result.aggregate.pron_score:.1f} "
                        f"acc={pron_result.aggregate.accuracy_score:.1f} "
                        f"turns_assessed={pron_result.aggregate.turns_assessed}"
                    )
            except Exception as e:  # noqa: BLE001 — pronunciation must not block cleanup
                logger.warning(f"Pronunciation assessment failed (non-fatal): {type(e).__name__}: {e}")
            session_logger.close()
            flush_traces()  # drain queued OTel spans before the process moves on
            print(f"Client disconnected: {user_id}")
