import wave
import aiohttp
from pydub import AudioSegment

from services import stt_deepgram, tts_minimax, transport_vad

from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.task import PipelineTask
from pipecat.pipeline.runner import PipelineRunner
from pipecat.processors.frameworks.langchain import LangchainProcessor
from pipecat.processors.audio.audio_buffer_processor import AudioBufferProcessor

from .converters import TranscriptionToContextConverter

# Your LangChain agent
from agents import conversation_agent
from agents.pipecat_wrapper import set_session_logger

# Session logging
from logs import setup_session_logger

async def pipeline():
    async with aiohttp.ClientSession() as session:

        # Local mic/speaker
        transport = transport_vad()

        # Speech-to-Text
        stt = stt_deepgram()

        # LLM (LangChain agent instead of OpenAI directly)
        llm = LangchainProcessor(chain=conversation_agent)

        # Text-to-Speech (MiniMax with custom params)
        tts = tts_minimax(session)

        # Simple frame converter (agent handles memory via InMemorySaver)
        converter = TranscriptionToContextConverter()

        # Session logger - extracts config dynamically from services
        session_logger = setup_session_logger(stt, tts, conversation_agent.model)
        set_session_logger(session_logger)  # Enable transcript logging

        # Audio buffer processor for recording
        audiobuffer = AudioBufferProcessor(num_channels=1)

        # Save audio when recording stops (WAV → MP3)
        @audiobuffer.event_handler("on_audio_data")
        async def on_audio_data(buffer, audio, sample_rate, num_channels):
            base_path = session_logger.session_dir / session_logger.session_id
            wav_path = base_path.with_suffix(".wav")
            mp3_path = base_path.with_suffix(".mp3")

            # Save WAV
            with wave.open(str(wav_path), "wb") as wf:
                wf.setnchannels(num_channels)
                wf.setsampwidth(2)  # 16-bit
                wf.setframerate(sample_rate)
                wf.writeframes(audio)

            # Convert to MP3
            audio_segment = AudioSegment.from_wav(str(wav_path))
            audio_segment.export(str(mp3_path), format="mp3", bitrate="128k")

            # Remove WAV (keep only MP3)
            wav_path.unlink()

            print(f"Audio saved to: {mp3_path}")

        pipeline = Pipeline([
            transport.input(),
            stt,
            converter,
            llm,
            tts,
            transport.output(),
            audiobuffer,  # After output - captures both streams
        ])

        task = PipelineTask(pipeline)
        runner = PipelineRunner()

        # Start recording
        await audiobuffer.start_recording()

        try:
            await runner.run(task)
        finally:
            await audiobuffer.stop_recording()
            session_logger.close()
