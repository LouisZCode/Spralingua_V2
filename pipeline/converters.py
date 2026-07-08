"""
Simple frame converters for Pipecat pipelines.
"""

from loguru import logger

from pipecat.frames.frames import (
    Frame,
    TranscriptionFrame,
    LLMContextFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameProcessor, FrameDirection
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frameworks.rtvi import (
    RTVIUserTranscriptionMessage,
    RTVIUserTranscriptionMessageData,
)


class TranscriptionToContextConverter(FrameProcessor):
    """Converts TranscriptionFrame to LLMContextFrame with VAD gating.

    Buffers transcriptions while user is speaking.
    Only sends to LLM after user stops speaking.
    Does NOT track history (agent uses InMemorySaver).
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._buffer = ""
        self.rtvi_processor = None  # Set by factory; used to push the consolidated user bubble
        # Carry the latest TranscriptionFrame metadata so the consolidated
        # RTVI message reuses the real frame's user_id / timestamp.
        self._last_user_id = ""
        self._last_timestamp = ""

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, UserStartedSpeakingFrame):
            # User started - clear buffer for new utterance.
            # BUG-007: a non-empty buffer here means a Deepgram final landed
            # AFTER the VAD-stop flush (network-lagged utterance tail) and is
            # about to be discarded — it never reached the LLM/transcript. Log
            # frequency (loguru sink -> session_NNN.log) before deciding on a
            # grace window (P2) or speech_final-driven flush (P3).
            if self._buffer.strip():
                logger.warning(f"Discarding late STT text: {self._buffer!r}")
            self._buffer = ""
            await self.push_frame(frame, direction)

        elif isinstance(frame, TranscriptionFrame):
            # Accumulate transcription text
            self._buffer += frame.text + " "
            self._last_user_id = frame.user_id
            self._last_timestamp = frame.timestamp

        elif isinstance(frame, UserStoppedSpeakingFrame):
            # User stopped - send accumulated text to LLM
            text = self._buffer.strip()
            if text:
                context = LLMContext([{"role": "user", "content": text}])
                await self.push_frame(LLMContextFrame(context=context))
                # Push ONE consolidated user bubble to the client (the same text
                # the LLM just received). Reuses the bot's push path; the
                # frontend's onUserTranscript renders it as a single bubble per
                # turn instead of one per Deepgram segment.
                if self.rtvi_processor is not None:
                    await self.rtvi_processor.push_transport_message(
                        RTVIUserTranscriptionMessage(
                            data=RTVIUserTranscriptionMessageData(
                                text=text,
                                user_id=self._last_user_id,
                                timestamp=self._last_timestamp,
                                final=True,
                            )
                        )
                    )
            self._buffer = ""
            await self.push_frame(frame, direction)

        else:
            # Pass all other frames through
            await self.push_frame(frame, direction)
