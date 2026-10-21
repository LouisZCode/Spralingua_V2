"""
Here we load the Text-To-Speech service. Right now we are using:

Minimax

You find here:
tts_minimax
"""
import aiohttp
import asyncio
import time
from io import BytesIO
from typing import AsyncGenerator

from loguru import logger
from pipecat.frames.frames import ErrorFrame, Frame, TTSAudioRawFrame
from pipecat.services.minimax.tts import MiniMaxHttpTTSService
from config import (
    minimax_api_key,
    minimax_group_id,
    openrouter_api_key,
    openrouter_base_url,
    tts_or_fallback,
    tts_or_model,
)
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
    "calm_woman": "Calm_Woman",  # Clara's voice until 2026-08-29 — kept so it's never lost
    "german_sweet_lady": "German_SweetLady",  # Clara 2026-08-29 → 2026-09-01 (trialing EN+DE quality)
    "clara_ttv": "ttv-voice-2026090116474926-NrZTvKOW",  # Clara since 2026-09-01 (MiniMax TTV voice-design)
    "German-Male": "German_PlayfulMan",
    # Cloned voices (exist in the MiniMax account)
    "luis_clone": "moss_audio_744c4375-eb2b-11f0-b8d7-fa843b4be43a",     # best Luis clone (old luis_voice_clone deleted)
    "German_Female": "moss_audio_4872e74b-124f-11f1-841b-1e2fac512910",  # German female (won A/B vs deleted v2)
}

# OpenRouter fallback voice substitutions (TTS-FB-001). OpenRouter proxies the
# same MiniMax speech models, but only voice ids that are NOT bound to a
# specific MiniMax account resolve there — verified live 2026-10-21:
#   PASS through OpenRouter unchanged: clara_ttv, German_PlayfulMan,
#     Calm_Woman, German_SweetLady (+ the 45 stock English voices).
#   502 (account-bound, need a substitute): both moss_audio clones,
#     german_bavarian_male_v2, german_bavarian_female.
# Keyed by MiniMax voice_id (not the app key) — run_tts only sees the id.
OR_TTS_VOICE_SUBSTITUTES = {
    "moss_audio_4872e74b-124f-11f1-841b-1e2fac512910": "German_SweetLady",   # German_Female (demo + Lena)
    "moss_audio_744c4375-eb2b-11f0-b8d7-fa843b4be43a": "German_PlayfulMan",  # luis_clone
    "german_bavarian_male_v2": "German_PlayfulMan",   # happy_harry (pipeline default)
    "german_bavarian_female": "German_SweetLady",    # sophie
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


class OpenRouterFallbackMiniMaxTTS(FirstOnlyTracedMiniMaxTTS):
    """MiniMax TTS that falls back to OpenRouter when MiniMax yields no audio.

    The credit-exhaustion failure mode (MiniMax ``base_resp.status_code`` 2053,
    seen in production 2026-10-21) is INVISIBLE to pipecat: the HTTP response
    is 200, so ``MiniMaxHttpTTSService.run_tts`` simply yields no audio frames
    and the learner hears silence while every span stays green. This subclass
    wraps each turn: if the primary generator completes without a single
    ``TTSAudioRawFrame`` — or dies with a non-cancel exception — the same text
    is synthesized through OpenRouter's ``/audio/speech`` endpoint (same
    MiniMax model, billed to the existing OpenRouter credits) and yielded as
    the same frame type, so nothing downstream changes.

    Account-bound MiniMax voice ids 502 through OpenRouter's provider, so
    ``OR_TTS_VOICE_SUBSTITUTES`` swaps those for a pass-through voice — a
    slightly different voice during an outage beats silence.
    """

    _OR_TIMEOUT_S = 15.0
    _ALERT_EVERY_S = 600.0  # rate-limit the Sentry/log alarm to 1 per 10 min
    _last_alert_ts: float = 0.0  # class-level: one alarm for the outage, not per turn

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.or_fallback_used = 0  # turns this service fell back (introscope/tests)

    async def run_tts(self, text: str) -> AsyncGenerator[Frame, None]:
        got_audio = False
        try:
            async for item in super().run_tts(text):
                if isinstance(item, TTSAudioRawFrame):
                    got_audio = True
                yield item
        except asyncio.CancelledError:
            # Interruption/barge-in cancels the generator — never "fall back"
            # from a deliberate cancellation.
            raise
        except Exception as e:  # noqa: BLE001 — provider failure is exactly the fallback case
            logger.warning(f"TTS primary (MiniMax) raised {type(e).__name__}: {e} — trying OpenRouter fallback")
        if not got_audio:
            async for item in self._run_openrouter_fallback(text):
                yield item

    async def _run_openrouter_fallback(self, text: str) -> AsyncGenerator[Frame, None]:
        """Synthesize ``text`` via OpenRouter; yield the same frame type as MiniMax.

        Never raises — a total failure yields ``ErrorFrame`` so the turn ends
        visibly wrong instead of hanging in green-span silence (the exact bug
        this class exists to prevent).
        """
        voice_id = OR_TTS_VOICE_SUBSTITUTES.get(self._voice_id, self._voice_id)
        substituted = voice_id != self._voice_id
        self.or_fallback_used += 1
        self._alarm(substituted)
        try:
            # aiohttp session is per-client (factory) and shared with the MiniMax
            # leg — same lifecycle, no extra connection pool.
            async with self._session.post(
                f"{openrouter_base_url}/audio/speech",
                headers={
                    "Authorization": f"Bearer {openrouter_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": tts_or_model,
                    "input": text,
                    "voice": voice_id,
                    # OpenRouter's MiniMax route only accepts mp3 (400 on pcm).
                    "response_format": "mp3",
                },
                timeout=aiohttp.ClientTimeout(total=self._OR_TIMEOUT_S),
            ) as resp:
                if resp.status != 200:
                    body = (await resp.text())[:200]
                    logger.error(f"TTS OpenRouter fallback HTTP {resp.status}: {body}")
                    yield ErrorFrame(error=f"TTS fallback HTTP {resp.status}")
                    return
                mp3 = await resp.read()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — see docstring
            logger.error(f"TTS OpenRouter fallback failed: {type(e).__name__}: {e}")
            yield ErrorFrame(error="TTS fallback failed")
            return

        # mp3 -> PCM (pydub/ffmpeg, already a dep for session exports). The
        # output transport re-chunks and resamples from frame.sample_rate, so
        # one frame with the true rate is all it needs.
        try:
            from pydub import AudioSegment  # local import: heavy module, fallback-only path

            seg = AudioSegment.from_file(BytesIO(mp3), format="mp3")
            sample_rate = getattr(self, "sample_rate", 24000) or 24000
            seg = seg.set_frame_rate(sample_rate).set_channels(1)
            if seg.raw_data:
                yield TTSAudioRawFrame(
                    audio=seg.raw_data, sample_rate=sample_rate, num_channels=1
                )
            else:
                yield ErrorFrame(error="TTS fallback returned empty audio")
        except Exception as e:  # noqa: BLE001 — decode failure must not hang the turn
            logger.error(f"TTS fallback mp3 decode failed: {type(e).__name__}: {e}")
            yield ErrorFrame(error="TTS fallback decode failed")

    @classmethod
    def _alarm(cls, substituted: bool) -> None:
        """One loud warning per outage window — this is the operator signal."""
        now = time.monotonic()
        if now - cls._last_alert_ts < cls._ALERT_EVERY_S:
            return
        cls._last_alert_ts = now
        logger.warning(
            "TTS FALLBACK ACTIVE — MiniMax returned no audio; synthesizing via "
            f"OpenRouter ({tts_or_model}){' with substituted voice' if substituted else ''}. "
            "Check MiniMax points/credits (console → Resource Packages)."
        )
        try:
            import sentry_sdk  # no-op unless SENTRY_DSN initialized the SDK

            sentry_sdk.capture_message(
                "TTS fallback active: MiniMax returned no audio (credit-exhausted?)",
                level="warning",
            )
        except Exception:  # noqa: BLE001 — alerting must never break audio
            pass


def tts_minimax(session, voice: str = "happy_harry", language: str = "de"):
    voice_id = VOICE_MAP.get(voice, "german_bavarian_male_v2")
    cls = (
        OpenRouterFallbackMiniMaxTTS
        if (tts_or_fallback and openrouter_api_key)
        else FirstOnlyTracedMiniMaxTTS
    )
    return cls(
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
