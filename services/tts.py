"""
Here we load the Text-To-Speech service. Right now we are using:

Minimax

You find here:
tts_minimax
"""
import aiohttp
import asyncio
from typing import AsyncGenerator

from pipecat.frames.frames import Frame
from pipecat.services.minimax.tts import MiniMaxHttpTTSService
from config import minimax_api_key, minimax_group_id
from pipecat.transcriptions.language import Language

# Single source of truth — both tts_minimax() and the TTS trace observer
# in pipeline/observers.py read these. Keep in sync with the constructor below.
MINIMAX_MODEL = "speech-2.8-turbo"   # speech-02-turbo (fast), speech-02-hd (quality)
MINIMAX_PROVIDER = "minimax"

# STT/TTS run in German by default (the runtime is German); the front-page
# `welcome` concierge is the English exception. Maps the short code that
# pipeline/factory.py derives per lesson → MiniMax's Language enum.
_TTS_LANGUAGE = {"de": Language.DE, "en": Language.EN}

# Available voices: key → MiniMax voice_id
# Cloned voices are kept in sync with the MiniMax account.
# See ARCHITECTURE.md → "Voice Inventory" for the source-of-truth table.
VOICE_MAP = {
    # System/preset voices (not in the account's cloned-voice list)
    "happy_harry": "german_bavarian_male_v2",
    "sophie": "german_bavarian_female",
    "calm_woman": "Calm_Woman",
    "German-Male": "German_PlayfulMan",
    # Cloned voices (exist in the MiniMax account)
    "luis_clone": "moss_audio_744c4375-eb2b-11f0-b8d7-fa843b4be43a",     # best Luis clone (old luis_voice_clone deleted)
    "German_Female": "moss_audio_4872e74b-124f-11f1-841b-1e2fac512910",  # German female (won A/B vs deleted v2)
}

class FirstOnlyTracedMiniMaxTTS(MiniMaxHttpTTSService):
    """MiniMax TTS that emits exactly ONE ``@traced_tts`` span per turn.

    For long bot replies, MiniMax splits text at sentence boundaries and
    calls ``run_tts`` once per sentence. Each call hits Pipecat's
    ``@traced_tts`` decorator. The first call's span lands inside the
    active turn (good — that's the pipeline-TTFB measurement). Calls 2+
    fire AFTER ``BotStartedSpeakingFrame`` has closed the turn span and
    cleared ``TurnContextProvider``, so they fall back to a service-level
    parent and become standalone orphan traces.

    Fix: gate the decorator via ``self._tracing_enabled``. ``@traced_tts``
    short-circuits without creating a span when that flag is False (see
    ``pipecat/utils/tracing/service_decorators.py:171–173``). We toggle it
    off for calls 2+ within a turn — audio still streams normally, just
    no span. ``PipelineLatencyObserver`` calls ``reset_for_new_turn()`` on
    each turn open to re-arm the gate.

    The single emitted span captures the FULL synthesis time of the first
    sentence — the TTFB-shaped measurement we want.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # True = next run_tts call is allowed to emit its span.
        # Flipped to False after the first call this turn, re-armed by
        # `reset_for_new_turn()` (called by PipelineLatencyObserver).
        self._allow_tts_span = True

    def reset_for_new_turn(self) -> None:
        """Re-arm the span gate so the next ``run_tts`` call creates a span."""
        self._allow_tts_span = True

    async def run_tts(self, text: str) -> AsyncGenerator[Frame, None]:
        if self._allow_tts_span:
            # First call this turn — let @traced_tts behave normally.
            self._allow_tts_span = False
            async for item in super().run_tts(text):
                yield item
        else:
            # Subsequent call — temporarily disable tracing so @traced_tts
            # skips span creation. Restore on exit so a transient toggle
            # doesn't bleed into other state.
            saved = self._tracing_enabled
            self._tracing_enabled = False
            try:
                async for item in super().run_tts(text):
                    yield item
            finally:
                self._tracing_enabled = saved


def tts_minimax(session, voice: str = "happy_harry", language: str = "de"):
    voice_id = VOICE_MAP.get(voice, "german_bavarian_male_v2")
    return FirstOnlyTracedMiniMaxTTS(
        api_key=minimax_api_key,
        group_id=minimax_group_id,
        aiohttp_session=session,
        model=MINIMAX_MODEL,           # constructor param (see MiniMax TTS gotcha in CLAUDE.md)
        voice_id=voice_id,
        params=MiniMaxHttpTTSService.InputParams(
            speed=1.0,                 # 0.5 to 2.0
            pitch=0,                   # -12 to 12
            volume=1.0,                # 0 to 10
            emotion="neutral",         # happy, sad, angry, fearful, disgusted, surprised, neutral, fluent
            language=_TTS_LANGUAGE.get(language, Language.DE),  # "de" runtime; "en" for the welcome concierge
        )
    )
