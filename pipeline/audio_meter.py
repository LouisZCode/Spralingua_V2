"""AudioSecondsMeter — sums seconds of audio actually forwarded to Deepgram.

Mute-semantics finding (2026-08-21, read against the vendored pipecat
0.0.98): `STTMuteFilter` (`.venv/…/pipecat/processors/filters/
stt_mute_filter.py::process_frame`) suppresses `InputAudioRawFrame`
LOCALLY while muted — that frame type is in the "only pass when not
muted" tuple, and its docstring says so explicitly: "The filter blocks
frames locally". A muted `InputAudioRawFrame` is dropped right there and
never reaches anything downstream — `DeepgramSTTService.run_stt` (whose
whole body is `await self._connection.send(audio)`) never sees it, so
Deepgram's websocket never receives it either. Deepgram bills for audio
actually streamed to it, so this meter is placed AFTER `stt_mute` and
BEFORE `stt` in `pipeline/factory.py`'s processor list — counting
everything that crosses that point already excludes every muted window
(BotStartedSpeaking → BotStoppedSpeaking, per BUG-001/MOBILE-001's
half-duplex fix) with no separate mute-state tracking needed here.

Per-client instance — no module-level state (per-client isolation is
load-bearing per CLAUDE.md). Must pass every frame through unchanged and
must never raise, same contract as `pipeline/observers.py` and
`pipeline/tts_duration.py`.
"""

from loguru import logger
from pipecat.frames.frames import Frame, InputAudioRawFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


class AudioSecondsMeter(FrameProcessor):
    """Sums billable STT input seconds (16-bit PCM) for one connection."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.total_seconds: float = 0.0

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, InputAudioRawFrame):
            try:
                if frame.sample_rate and frame.num_channels:
                    self.total_seconds += len(frame.audio) / (
                        frame.sample_rate * frame.num_channels * 2
                    )
            except Exception as e:  # noqa: BLE001 — a metric must never break the pipeline
                logger.warning(f"[AudioSecondsMeter] {type(e).__name__}: {e}")
        await self.push_frame(frame, direction)
