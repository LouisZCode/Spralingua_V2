"""
Pipeline-latency observer.

Emits **one Langfuse trace per turn**. The turn span IS the trace root
(no wrapping session/conversation span — Langfuse's Sessions view groups
traces by the shared ``langfuse.session.id`` attribute and auto-computes
session totals, so a session-level span would just duplicate that work).

Hierarchy in Langfuse:

    Langfuse Session (one per WebSocket, soft grouping)
    ├── Trace = turn-{lesson_id}                     ← root span = pipeline TTFB
    │   ├── stt observation   (Deepgram, @traced_stt)
    │   ├── llm observation   (hand-rolled in pipecat_wrapper.astream)
    │   └── tts observation   (MiniMax, @traced_tts)
    ├── Trace = turn-{lesson_id}   ← next turn, next trace
    │   └── ...
    └── ...

Turn boundaries (= trace boundaries):

- **open** on ``UserStoppedSpeakingFrame`` (voice) OR ``LLMContextFrame``
  (``/say`` typed input — no VAD events fire for that path)
- **close** on ``BotStartedSpeakingFrame`` (bot's first audio leaves the
  transport — voice-to-voice TTFB end boundary) or on
  ``EndFrame`` / ``CancelFrame`` if the turn somehow never produced bot
  audio.

Pipecat's auto-attached ``TurnTrackingObserver`` + ``TurnTraceObserver``
are disabled at the ``PipelineTask`` level (``enable_turn_tracking=False``)
so this observer is the sole writer of the turn span and the sole writer
of the ``TurnContextProvider`` singleton. The ``@traced_stt`` /
``@traced_tts`` decorators and our LLM span all look up that singleton at
service-dispatch time to find their parent — same mechanism Pipecat's own
``TurnTraceObserver`` uses (``turn_trace_observer.py:189–190``).

**Known limitation (voice mode):** ``@traced_stt`` on Deepgram fires per
transcript, which happens *during* user speech — before
``UserStoppedSpeakingFrame``. Those STT spans will be orphan (each its own
trace) until we either open the turn earlier (``UserStartedSpeakingFrame``,
which includes user-speaking time in the TTFB metric) or change strategy.
This only affects voice mode; typed input via ``/say`` has no STT.
"""

from typing import Optional

from loguru import logger
from opentelemetry import trace
from opentelemetry.trace import Span
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    CancelFrame,
    EndFrame,
    LLMContextFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.observers.base_observer import BaseObserver, FramePushed
from pipecat.utils.tracing.turn_context_provider import TurnContextProvider


class PipelineLatencyObserver(BaseObserver):
    """Open a new Langfuse trace per turn, with turn duration = pipeline TTFB
    (UserStopped/LLMContextFrame → BotStarted). All session/user/lesson
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
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._session_id = session_id
        self._user_id = user_id
        self._lesson_id = lesson_id
        self._level = level
        self._situation = situation
        self._voice = voice

        self._tracer = trace.get_tracer("spralingua")
        self._turn_span: Optional[Span] = None
        self._turn_count = 0

    async def on_push_frame(self, data: FramePushed):
        frame = data.frame
        try:
            if isinstance(frame, UserStoppedSpeakingFrame):
                # Voice flow: VAD signals user stopped → open turn (will close
                # any stale half-open turn first for the interruption case).
                self._open_turn()
            elif isinstance(frame, LLMContextFrame):
                # Typed-turn (`/say`) flow: there is no UserStoppedSpeakingFrame,
                # so the LLMContextFrame is our first signal that a turn has
                # begun. In voice flow this also fires (the converter emits it
                # right after UserStoppedSpeakingFrame), but the turn is already
                # open by then — guard against re-opening.
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

    def _close_turn(self):
        if self._turn_span is not None:
            self._turn_span.end()
            self._turn_span = None
            TurnContextProvider.get_instance().set_current_turn_context(None)
