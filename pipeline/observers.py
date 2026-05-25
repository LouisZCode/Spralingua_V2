"""
Pipeline-latency observer.

Emits **one Langfuse trace per turn**. The turn span IS the trace root
(no wrapping session/conversation span — Langfuse's Sessions view groups
traces by the shared ``langfuse.session.id`` attribute and auto-computes
session totals, so a session-level span would just duplicate that work).

Hierarchy in Langfuse:

    Langfuse Session (one per WebSocket, soft grouping)
    ├── Trace = turn-{lesson_id}                     ← root span
    │   ├── stt observation   (Deepgram, @traced_stt)
    │   ├── llm observation   (hand-rolled in pipecat_wrapper.astream)
    │   └── tts observation   (MiniMax, @traced_tts)
    ├── Trace = turn-{lesson_id}   ← next turn, next trace
    │   └── ...
    └── ...

Turn boundaries (= trace boundaries):

- **open** on ``UserStartedSpeakingFrame`` (voice) OR ``LLMContextFrame``
  (``/say`` typed input — no VAD events fire for that path). Opening at
  user-start (not user-stop) means ``@traced_stt`` finds a live
  ``TurnContextProvider`` while transcribing, so STT spans nest under the
  turn instead of becoming top-level orphans.
- **timestamp marker** on ``UserStoppedSpeakingFrame`` — no new span. We
  record the wall-clock ns so we can emit ``metrics.pipeline_ttfb_ms``
  (UserStopped → BotStarted) on the turn span at close, preserving the
  original "pipeline TTFB" number even though the turn span's own duration
  now spans the whole user-start → bot-start window.
- **close** on ``BotStartedSpeakingFrame`` (bot's first audio leaves the
  transport) or on ``EndFrame`` / ``CancelFrame`` if the turn somehow
  never produced bot audio.

Frame direction filter: ``BaseInputTransport`` emits the VAD frames via
``broadcast_frame()`` (``frame_processor.py:742-753``), which constructs
TWO Frame instances (one pushed DOWNSTREAM, one UPSTREAM) with distinct
``frame.id`` values. Without filtering, both reach this observer and the
``frame.id`` dedup misses, producing duplicate 0-duration turn spans. We
filter to ``FrameDirection.DOWNSTREAM`` — same pattern Pipecat's own
``user_bot_latency_log_observer.py:49-51`` uses.

Pipecat's auto-attached ``TurnTrackingObserver`` + ``TurnTraceObserver``
are disabled at the ``PipelineTask`` level (``enable_turn_tracking=False``)
so this observer is the sole writer of the turn span and the sole writer
of the ``TurnContextProvider`` singleton. The ``@traced_stt`` /
``@traced_tts`` decorators and our LLM span all look up that singleton at
service-dispatch time to find their parent — same mechanism Pipecat's own
``TurnTraceObserver`` uses (``turn_trace_observer.py:189–190``).
"""

import time
from collections import deque
from typing import Optional

from loguru import logger
from opentelemetry import trace
from opentelemetry.trace import Span
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    CancelFrame,
    EndFrame,
    LLMContextFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.observers.base_observer import BaseObserver, FramePushed
from pipecat.processors.frame_processor import FrameDirection
from pipecat.utils.tracing.turn_context_provider import TurnContextProvider


class PipelineLatencyObserver(BaseObserver):
    """Open a new Langfuse trace per turn. Turn span duration = UserStarted
    (or LLMContextFrame for typed input) → BotStarted. The original
    pipeline-TTFB number (UserStopped → BotStarted) is preserved as
    `metrics.pipeline_ttfb_ms` on the span. All session/user/lesson
    metadata is set on each turn's trace root."""

    def __init__(
        self,
        *,
        session_id: str,
        user_id: str,
        lesson_id: str,
        level: str,
        situation: str,
        voice: str,
        tts_service=None,
        max_frames: int = 100,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._session_id = session_id
        self._user_id = user_id
        self._lesson_id = lesson_id
        self._level = level
        self._situation = situation
        self._voice = voice
        # Optional TTS service reference. If it exposes ``reset_for_new_turn()``
        # (our ``FirstOnlyTracedMiniMaxTTS`` does), we call it on each turn
        # open to re-arm its one-span-per-turn gate. Without this, long bot
        # replies would split into multiple run_tts calls and each call after
        # the first would orphan into its own trace.
        self._tts_service = tts_service

        # Same dedup pattern Pipecat's own TurnTrackingObserver uses (see
        # `pipecat/observers/turn_tracking_observer.py:65–89`). Pipecat's
        # observer machinery fires `on_push_frame` once per processor edge
        # the frame traverses, so a single UserStoppedSpeakingFrame or
        # LLMContextFrame would trigger ~8 turn opens (one per edge of our
        # 9-processor pipeline). Bounded set + ring buffer keeps memory flat.
        self._processed_frames: set = set()
        self._frame_history: deque = deque(maxlen=max_frames)

        self._tracer = trace.get_tracer("spralingua")
        self._turn_span: Optional[Span] = None
        self._turn_count = 0
        # Wall-clock ns at UserStoppedSpeakingFrame. Used to compute
        # `metrics.pipeline_ttfb_ms` (UserStopped → BotStarted) at close,
        # preserving the original pipeline-TTFB number now that the turn
        # span's own duration spans UserStarted → BotStarted.
        self._user_stopped_ns: Optional[int] = None

    async def on_push_frame(self, data: FramePushed):
        # Drop the upstream half of broadcast_frame() pairs. Pipecat's VAD and
        # output transport emit UserStarted/UserStopped/BotStarted via
        # broadcast_frame (frame_processor.py:742-753) as TWO Frame instances
        # with distinct frame.id — without this filter the dedup below misses
        # and we open a turn span twice (the second open closes the first as a
        # 0-duration orphan). Pipecat's own observers use the same filter, see
        # observers/loggers/user_bot_latency_log_observer.py:49-51.
        if data.direction != FrameDirection.DOWNSTREAM:
            return

        frame = data.frame

        # Dedup: skip if we've already seen this frame on a prior edge.
        if frame.id in self._processed_frames:
            return
        self._processed_frames.add(frame.id)
        self._frame_history.append(frame.id)
        # Re-sync the set to the bounded history if it grew past the ring.
        if len(self._processed_frames) > len(self._frame_history):
            self._processed_frames = set(self._frame_history)

        try:
            if isinstance(frame, UserStartedSpeakingFrame):
                # Voice flow: VAD signals user started → open turn early so
                # @traced_stt finds the turn context while transcribing. Guard
                # prevents VAD chatter / mid-turn interruption from creating
                # an empty re-opened span; the direction filter above already
                # neutralizes the broadcast-pair duplication.
                if self._turn_span is None:
                    self._open_turn()
            elif isinstance(frame, UserStoppedSpeakingFrame):
                # Marker only: capture the timestamp so we can emit the
                # original pipeline-TTFB metric on the turn span at close.
                # Does NOT open or close anything — the turn is already open.
                self._user_stopped_ns = time.time_ns()
            elif isinstance(frame, LLMContextFrame):
                # Typed-turn (`/say`) flow: no VAD events fire for typed input,
                # so the LLMContextFrame is the only signal that a turn has
                # begun. In voice flow this also fires (converter emits it
                # right after UserStoppedSpeakingFrame), but the turn is
                # already open by then — guard prevents re-opening.
                if self._turn_span is None:
                    self._open_turn()
            elif isinstance(frame, BotStartedSpeakingFrame):
                self._close_turn()
            elif isinstance(frame, (EndFrame, CancelFrame)):
                # Belt-and-suspenders: if a turn somehow never reached
                # BotStartedSpeakingFrame (TTS failed, pipeline canceled mid-LLM),
                # close it now so its span ends before flush_traces() drains.
                self._close_turn()
        except Exception as e:  # noqa: BLE001 — tracing must never break the pipeline
            logger.warning(f"[PipelineLatencyObserver] {type(e).__name__}: {e}")

    def _open_turn(self):
        # Defensive: if a previous turn never closed (e.g. user interrupted
        # before bot started), close it before opening a new one.
        self._close_turn()
        self._user_stopped_ns = None  # reset per turn; set when UserStopped fires
        self._turn_count += 1
        # Root span — NO parent context, so this starts a brand-new Langfuse
        # trace. Each turn = its own trace, evaluable independently.
        self._turn_span = self._tracer.start_span(f"turn-{self._lesson_id}")
        # All trace-level metadata lives here since this IS the trace root.
        # `langfuse.session.id` is what makes Langfuse's Sessions view group
        # this trace with the other turns from the same WebSocket.
        self._turn_span.set_attribute("langfuse.session.id", self._session_id)
        self._turn_span.set_attribute("user.id", self._user_id)
        self._turn_span.set_attribute("lesson_id", self._lesson_id)
        self._turn_span.set_attribute("level", self._level)
        self._turn_span.set_attribute("situation", self._situation)
        self._turn_span.set_attribute("voice", self._voice)
        self._turn_span.set_attribute("turn.number", self._turn_count)
        # Push context for @traced_stt / @traced_tts and our hand-rolled LLM
        # span to find as parent. Without this they fall back to a service-level
        # span and become orphan traces (= the original bug we just fixed).
        TurnContextProvider.get_instance().set_current_turn_context(
            self._turn_span.get_span_context()
        )
        # Re-arm the TTS service's one-span-per-turn gate (if present).
        if self._tts_service is not None and hasattr(self._tts_service, "reset_for_new_turn"):
            self._tts_service.reset_for_new_turn()

    def _close_turn(self):
        if self._turn_span is not None:
            # Preserve the original pipeline-TTFB metric as an attribute even
            # though the span's own duration is now UserStarted → BotStarted.
            # Typed-input turns (no UserStopped) skip the attribute.
            if self._user_stopped_ns is not None:
                ttfb_ms = (time.time_ns() - self._user_stopped_ns) / 1_000_000
                self._turn_span.set_attribute("metrics.pipeline_ttfb_ms", ttfb_ms)
            self._turn_span.end()
            self._turn_span = None
            self._user_stopped_ns = None
            TurnContextProvider.get_instance().set_current_turn_context(None)
