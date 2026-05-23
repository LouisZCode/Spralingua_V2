"""TTSDurationTracker — per-turn TTS audio duration metric.

Sits between ``tts`` and ``rtvi_processor`` in the pipeline. Sums
``TTSAudioRawFrame.num_frames`` per turn (downstream) and converts to
milliseconds using the frame's own ``sample_rate``. On
``BotStoppedSpeakingFrame`` (system frame propagated upstream from
``BaseOutputTransport`` when TTS finishes streaming audio) it fires the
``on_turn_complete`` callback with the computed duration and resets.

The duration is the **authoritative** TTS audio length for the turn —
exact sample math, not wall-clock. Two consumers in this codebase:

1. ``ClientWrapper.flush_bot_output(audio_duration_ms)`` — pushes the
   bot-output RTVI message with the duration attached so the frontend
   can schedule the bubble reveal after audio playback ends.
2. (Future) attach to the Langfuse ``turn-N-TTS`` span as a metadata
   field for response-length analytics.

The callback may be sync or async; we ``await`` it when it returns a
coroutine. Errors in the callback are caught so a metric failure can
never break the pipeline.
"""

import inspect

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    TTSAudioRawFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


class TTSDurationTracker(FrameProcessor):
    """Sums TTS audio duration per turn; fires a callback on turn close."""

    def __init__(self, on_turn_complete=None, **kwargs):
        super().__init__(**kwargs)
        self._on_turn_complete = on_turn_complete
        self._turn_count = 0
        self._num_frames = 0
        self._sample_rate = 0
        self._speaking = False

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, BotStartedSpeakingFrame):
            self._turn_count += 1
            self._num_frames = 0
            self._sample_rate = 0
            self._speaking = True

        elif isinstance(frame, TTSAudioRawFrame) and self._speaking:
            self._num_frames += frame.num_frames
            if frame.sample_rate:
                self._sample_rate = frame.sample_rate

        elif isinstance(frame, BotStoppedSpeakingFrame):
            duration_ms = 0.0
            if self._speaking and self._sample_rate > 0:
                duration_ms = (self._num_frames / self._sample_rate) * 1000.0
            print(
                f"[TTS duration] turn={self._turn_count} "
                f"audio_duration_ms={duration_ms:.1f} "
                f"num_frames={self._num_frames} sample_rate={self._sample_rate}"
            )
            self._speaking = False
            self._num_frames = 0
            self._sample_rate = 0
            await self._fire(duration_ms)

        await self.push_frame(frame, direction)

    async def _fire(self, duration_ms: float):
        if self._on_turn_complete is None:
            return
        try:
            result = self._on_turn_complete(duration_ms)
            if inspect.isawaitable(result):
                await result
        except Exception as e:  # noqa: BLE001 — never break the pipeline on a metric
            print(f"[TTS duration] callback error: {type(e).__name__}: {e}")
