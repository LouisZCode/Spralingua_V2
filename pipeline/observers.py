"""
STT trace observer — emits one Langfuse Generation per spoken user turn.

Read-only Pipecat processor: every frame is passed through unchanged. Per
spoken turn (UserStartedSpeakingFrame → ... → UserStoppedSpeakingFrame) it
manages a Langfuse Generation observation that times the STT stage and
captures the final transcript text. Generation primitive (not Span) is
intentional — lets us A/B different STT providers by latency / output text in
the future, with the model name recorded on each call.

The Generation is created as a NEW top-level trace (sibling of the LangChain
`turn-{N}-LLM` trace) and grouped under the same Langfuse Session via
``langfuse.propagate_attributes()`` (the v4 canonical way to set
``session_id`` / ``user_id`` / ``tags`` / ``trace_name`` — see
``.venv/.../langfuse/_client/propagation.py``).

Cost is intentionally not recorded — see ``LEARNINGS.md`` 2026-05-19 entry.
"""

import time
from typing import Optional

from langfuse import propagate_attributes

from pipecat.frames.frames import (
    Frame,
    InterimTranscriptionFrame,
    TranscriptionFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from agents.observability import langfuse_client
from services.stt import DEEPGRAM_MODEL, DEEPGRAM_PROVIDER


class STTTraceObserver(FrameProcessor):
    """Observe STT frames and emit a Langfuse Generation per spoken turn."""

    def __init__(self, user_id: str, level: str, situation: str, voice: str, **kwargs):
        super().__init__(**kwargs)
        self.user_id = user_id
        self.level = level
        self.situation = situation
        self.voice = voice

        self._turn_count = 0
        self._buffer = ""
        self._interim_count = 0
        self._start_time: Optional[float] = None
        self._generation = None    # LangfuseGeneration handle, alive across frames
        self._prop_ctx = None      # propagate_attributes() context manager handle

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        try:
            if isinstance(frame, UserStartedSpeakingFrame):
                self._open_turn()
            elif isinstance(frame, InterimTranscriptionFrame):
                self._interim_count += 1
            elif isinstance(frame, TranscriptionFrame):
                self._buffer += frame.text + " "
            elif isinstance(frame, UserStoppedSpeakingFrame):
                self._close_turn()
        except Exception as e:  # noqa: BLE001 — tracing must never break the pipeline
            print(f"[STT trace] {type(e).__name__}: {e}")
            self._safe_cleanup()

        # Always pass the frame through; this is a read-only observer.
        await self.push_frame(frame, direction)

    # ------- helpers -------

    def _open_turn(self):
        self._turn_count += 1
        self._buffer = ""
        self._interim_count = 0
        self._start_time = time.time()

        # Enter the propagation context FIRST — start_observation below
        # inherits these trace-level attributes, which makes the new trace
        # appear under the right Langfuse Session in the UI.
        # `propagate_attributes` is a top-level langfuse function, NOT a
        # method on the client (verified against langfuse 4.6.1).
        self._prop_ctx = propagate_attributes(
            session_id=self.user_id,
            user_id=self.user_id,
            tags=[self.level, self.situation, "STT"],
            trace_name=f"turn-{self._turn_count}-STT",
        )
        self._prop_ctx.__enter__()

        # Inner generation name is stage-specific (not turn-numbered) so the
        # UI tree reads "turn-3-STT (trace) → deepgram_transcribe (generation)"
        # — same pattern will give "turn-3-TTS → minimax_synthesize" later.
        self._generation = langfuse_client.start_observation(
            name="deepgram_transcribe",
            as_type="generation",
            model=DEEPGRAM_MODEL,
            input={"event": "user_started_speaking"},
            metadata={
                "provider": DEEPGRAM_PROVIDER,
                "level": self.level,
                "situation": self.situation,
                "voice": self.voice,
                "exchange": self._turn_count,
            },
        )

    def _close_turn(self):
        if self._generation is None:
            return
        transcript = self._buffer.strip()
        duration_s = (time.time() - self._start_time) if self._start_time else None

        self._generation.update(
            output=transcript or None,
            metadata={
                "interim_transcriptions": self._interim_count,
                "duration_s": duration_s,
                "char_count": len(transcript),
            },
        )
        self._generation.end()
        self._generation = None

        if self._prop_ctx is not None:
            self._prop_ctx.__exit__(None, None, None)
            self._prop_ctx = None
        self._start_time = None

    def _safe_cleanup(self):
        """Best-effort cleanup on error so we don't leak open spans / OTel contexts."""
        try:
            if self._generation is not None:
                self._generation.end()
        except Exception:
            pass
        self._generation = None
        try:
            if self._prop_ctx is not None:
                self._prop_ctx.__exit__(None, None, None)
        except Exception:
            pass
        self._prop_ctx = None
        self._start_time = None
