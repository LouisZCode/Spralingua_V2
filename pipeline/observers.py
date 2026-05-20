"""
Turn trace observer — emits Langfuse Generations for STT and TTS per turn.

Read-only Pipecat processor: every frame passes through unchanged. Per
spoken turn (``UserStoppedSpeakingFrame`` → ``BotStartedSpeakingFrame``) it
emits two FLAT sibling Generations (not nested):

    turn-N-STT    UserStoppedSpeakingFrame  → LLMFullResponseStartFrame
    turn-N-TTS    first TextFrame           → BotStartedSpeakingFrame

The third Generation per turn, ``turn-N-LLM``, is owned by
``agents/pipecat_wrapper.py::ClientWrapper.astream`` (which has direct
access to the user message + chain stream + first-token moment + final
usage chunk). It measures LLM TTFT via an atomic
``.end(end_time=ttft_ns, output=full_text, usage_details=...)`` call.

All three are ``as_type="generation"`` so Langfuse's typed analytics
(model filter, usage analytics, cost rules) work for each model in the
pipeline.

Each Generation is anchored to the session via
``langfuse.propagate_attributes()`` — the v4 canonical way to set
``session_id`` / ``user_id`` / ``tags`` / ``trace_name`` from non-LangChain
code (verified against langfuse 4.6.1).

Flat sibling traces (not nested): ``langfuse_client.start_observation()``
returns a detached span; it does not make itself "current" in the OTel
context, so subsequent observations do not auto-nest as children. The flat
schema mirrors the historical layout and is enough to answer the
bottleneck question (which model is slow on this turn).

Position in the pipeline: between ``llm`` (LangchainProcessor) and ``tts``.
That position sees ``UserStoppedSpeakingFrame`` (passes through from
upstream), ``LLMFullResponseStartFrame`` and ``TextFrame`` (emitted by
LangchainProcessor going downstream), and ``BotStartedSpeakingFrame``
(system frame, propagated upstream from ``BaseOutputTransport``).

``LLMFullResponseStartFrame`` is emitted by LangchainProcessor BEFORE
``chain.astream()`` is invoked, so it's genuinely "chain dispatch" rather
than TTFT-later (verified at
``.venv/.../pipecat/processors/frameworks/langchain.py``).

Typed turns (via /say HTTP) are intentionally NOT covered in v1 — they
lack ``UserStoppedSpeakingFrame``. Follow-up will add an alt-trigger.

Cost: deferred. ``usage_details`` lives on ``turn-N-LLM`` only (token
counts come from the LLM stream's final chunk).
"""

from langfuse import propagate_attributes

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    CancelTaskFrame,
    EndFrame,
    Frame,
    LLMFullResponseStartFrame,
    TextFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from agents.observability import langfuse_client
from services.stt import DEEPGRAM_MODEL, DEEPGRAM_PROVIDER
from services.tts import MINIMAX_MODEL, MINIMAX_PROVIDER, VOICE_MAP


class TurnTraceObserver(FrameProcessor):
    """Emit STT and TTS sibling traces per spoken turn with corrected boundaries."""

    def __init__(self, user_id: str, session_id: str, level: str, situation: str, voice: str, **kwargs):
        super().__init__(**kwargs)
        self.user_id = user_id
        self.session_id = session_id
        self.level = level
        self.situation = situation
        self.voice = voice

        self._turn_count = 0
        self._first_token_seen = False
        self._stt_obs = None
        self._stt_ctx = None
        self._tts_obs = None
        self._tts_ctx = None

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        try:
            if isinstance(frame, UserStoppedSpeakingFrame):
                self._open_stt()
            elif isinstance(frame, LLMFullResponseStartFrame):
                self._close_stt()
            elif (
                isinstance(frame, TextFrame)
                and self._stt_obs is None       # STT has already closed
                and self._tts_obs is None       # TTS not yet open
                and not self._first_token_seen  # only the first one this turn
                and self._turn_count > 0        # ignore stray frames before any turn
            ):
                self._open_tts()
            elif isinstance(frame, BotStartedSpeakingFrame):
                self._close_tts()
            elif isinstance(frame, (EndFrame, CancelTaskFrame)):
                self._safe_cleanup()
        except Exception as e:  # noqa: BLE001 — tracing must never break the pipeline
            print(f"[Turn trace] {type(e).__name__}: {e}")
            self._safe_cleanup()

        await self.push_frame(frame, direction)

    # ------- STT trace -------

    def _open_stt(self):
        # Defensive: release any half-open prior turn before starting a new one.
        self._safe_cleanup()

        self._turn_count += 1
        self._first_token_seen = False

        self._stt_ctx = propagate_attributes(
            session_id=self.session_id,
            user_id=self.user_id,
            tags=[self.level, self.situation, "STT"],
            trace_name=f"turn-{self._turn_count}-STT",
        )
        self._stt_ctx.__enter__()

        self._stt_obs = langfuse_client.start_observation(
            name=f"turn-{self._turn_count}-STT",
            as_type="generation",
            model=DEEPGRAM_MODEL,
            metadata={
                "service": DEEPGRAM_PROVIDER,
                "boundary": "UserStoppedSpeakingFrame → LLMFullResponseStartFrame",
                "level": self.level,
                "situation": self.situation,
                "voice": self.voice,
                "exchange": self._turn_count,
            },
        )

    def _close_stt(self):
        if self._stt_obs is not None:
            self._stt_obs.end()
            self._stt_obs = None
        if self._stt_ctx is not None:
            self._stt_ctx.__exit__(None, None, None)
            self._stt_ctx = None

    # ------- TTS trace -------

    def _open_tts(self):
        self._first_token_seen = True

        self._tts_ctx = propagate_attributes(
            session_id=self.session_id,
            user_id=self.user_id,
            tags=[self.level, self.situation, "TTS"],
            trace_name=f"turn-{self._turn_count}-TTS",
        )
        self._tts_ctx.__enter__()

        self._tts_obs = langfuse_client.start_observation(
            name=f"turn-{self._turn_count}-TTS",
            as_type="generation",
            model=MINIMAX_MODEL,
            metadata={
                "service": MINIMAX_PROVIDER,
                "voice": self.voice,
                "voice_id": VOICE_MAP.get(self.voice),
                "boundary": "first TextFrame → BotStartedSpeakingFrame",
                "level": self.level,
                "situation": self.situation,
                "exchange": self._turn_count,
            },
        )

    def _close_tts(self):
        if self._tts_obs is not None:
            self._tts_obs.end()
            self._tts_obs = None
        if self._tts_ctx is not None:
            self._tts_ctx.__exit__(None, None, None)
            self._tts_ctx = None

    # ------- cleanup -------

    def _safe_cleanup(self):
        """Best-effort: close any open span and exit any open propagate context."""
        for obs_attr in ("_stt_obs", "_tts_obs"):
            obs = getattr(self, obs_attr, None)
            if obs is not None:
                try:
                    obs.end()
                except Exception:
                    pass
                setattr(self, obs_attr, None)
        for ctx_attr in ("_stt_ctx", "_tts_ctx"):
            ctx = getattr(self, ctx_attr, None)
            if ctx is not None:
                try:
                    ctx.__exit__(None, None, None)
                except Exception:
                    pass
                setattr(self, ctx_attr, None)
        self._first_token_seen = False
