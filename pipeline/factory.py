import wave
import aiohttp
from pydub import AudioSegment

from services import stt_deepgram, tts_minimax, transport_fastapi_ws

from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.task import PipelineTask
from pipecat.pipeline.runner import PipelineRunner
from pipecat.processors.frameworks.langchain import LangchainProcessor
from pipecat.processors.audio.audio_buffer_processor import AudioBufferProcessor

from .converters import TranscriptionToContextConverter
from .observers import STTTraceObserver, TTSTraceObserver

from agents import ClientWrapper, CONVERSATIONAL_MODEL
from agents.observability import langfuse_client

from logs import setup_session_logger


# Live pipeline tasks keyed by user_id. Used by /say/{user_id} in main.py
# to inject typed turns into an active session. Per-client isolation rule
# from CLAUDE.md still holds — this is just a lookup, not shared state.
ACTIVE_TASKS: dict[str, PipelineTask] = {}


async def run_pipeline(websocket, user_id: str, level: str = "A1", situation: str = "introducing_yourself", voice: str = "happy_harry"):
    """Builds and runs a full pipeline for a single client connection."""
    async with aiohttp.ClientSession() as session:

        # Transport: one per client (wraps this specific websocket)
        transport = transport_fastapi_ws(websocket)

        # Fresh services per client
        stt = stt_deepgram()
        tts = tts_minimax(session, voice=voice)
        converter = TranscriptionToContextConverter()
        stt_observer = STTTraceObserver(
            user_id=user_id, level=level, situation=situation, voice=voice,
        )
        tts_observer = TTSTraceObserver(
            user_id=user_id, level=level, situation=situation, voice=voice,
        )

        # Per-client logger
        session_logger = setup_session_logger(stt, tts, CONVERSATIONAL_MODEL)

        # Per-client wrapper (agent + logger + context settings inside)
        wrapper = ClientWrapper(user_id=user_id, logger=session_logger, level=level, situation=situation, voice=voice)
        llm = LangchainProcessor(chain=wrapper)

        # Per-client audio recorder
        audiobuffer = AudioBufferProcessor(num_channels=1)

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

        pipeline = Pipeline([
            transport.input(),
            stt,
            stt_observer,      # read-only — emits a Langfuse Generation per spoken turn
            converter,
            llm,
            tts,
            tts_observer,      # read-only — emits a Langfuse Generation per agent reply
            transport.output(),
            audiobuffer,
        ])

        task = PipelineTask(pipeline)
        wrapper._pipeline_task = task  # Let wrapper end the pipeline via EndTaskFrame
        runner = PipelineRunner()

        await audiobuffer.start_recording()
        ACTIVE_TASKS[user_id] = task  # register so /say can inject typed turns

        print(f"Client connected: {user_id} | level={level} situation={situation} voice={voice}")

        try:
            await runner.run(task)
        finally:
            ACTIVE_TASKS.pop(user_id, None)
            await audiobuffer.stop_recording()
            session_logger.close()
            langfuse_client.flush()  # drain queued spans before the process moves on
            print(f"Client disconnected: {user_id}")
