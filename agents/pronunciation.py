"""Post-session pronunciation assessment (PRON-001).

Per-turn Azure Speech "Pronunciation Assessment" scored against the user's
own Deepgram transcript (scripted mode, with miscue detection enabled).

Each user turn is fed to Azure as a 16 kHz mono PCM WAV built in-memory
from the bytes captured by Pipecat's ``AudioBufferProcessor`` per-turn
event. Calls run in parallel via ``asyncio.gather`` + ``asyncio.to_thread``
because the Azure SDK exposes a custom blocking ``Future`` (``.get()``),
not an ``asyncio`` future. Aggregate scores are computed client-side as
simple averages.

Prosody is only supported for ``en-US``. For other locales the prosody
score is ``None`` and the aggregator skips it.
"""

import asyncio
import io
import wave
from typing import Iterable

from loguru import logger
from pydantic import BaseModel, Field

import azure.cognitiveservices.speech as speechsdk
from agents.audio_costs import AZURE_PRONUNCIATION_PER_MIN, stamp_audio_cost, stt_cost_usd
from agents.observability import generation_span, record_generation_output
from config import azure_speech_key, azure_speech_region

# 16-bit mono PCM — the exact format `_score_turn` below builds its WAV from
# (and what `pipeline/factory.py`'s `on_user_turn_audio_data` handler hands
# us), so duration is 2 bytes/sample, one channel.
_PCM_BYTES_PER_SAMPLE = 2


class WordScore(BaseModel):
    word: str
    accuracy_score: float = Field(ge=0, le=100)
    error_type: str


class TurnScore(BaseModel):
    text: str
    recognized: str
    accuracy_score: float
    fluency_score: float
    completeness_score: float
    prosody_score: float | None
    pron_score: float
    words: list[WordScore]


class Aggregate(BaseModel):
    accuracy_score: float
    fluency_score: float
    completeness_score: float
    prosody_score: float | None
    pron_score: float
    turns_assessed: int


class PronunciationResult(BaseModel):
    locale: str
    turns: list[TurnScore]
    aggregate: Aggregate


async def assess_pronunciation(
    *,
    user_turns: Iterable[tuple[str, bytes, int]],
    locale: str,
    session_id: str | None = None,
) -> PronunciationResult:
    """Score every user turn and aggregate. Raises if credentials missing or all calls fail."""
    if not azure_speech_key:
        raise RuntimeError("AZURE_SPEECH_KEY not set; cannot run pronunciation assessment")

    turns = list(user_turns)
    if not turns:
        raise RuntimeError("No user turn audio captured")

    # OBS-007: one Langfuse trace per post-session assessment, filed into the
    # conversation's session. Not an LLM — no token usage; the observation
    # output is the aggregate scores, the per-turn detail stays in the DB row.
    with generation_span(
        "pronunciation-eval",
        system="azure",
        model="azure-pronunciation-assessment",
        operation="pronunciation",
        input_text=f"[{len(turns)} user turns, locale={locale}]",
        session_id=session_id,
    ) as span:
        scored: list[TurnScore | None] = await asyncio.gather(*[
            asyncio.to_thread(_score_turn, text, audio, sample_rate, locale)
            for text, audio, sample_rate in turns
        ])
        scored = [t for t in scored if t is not None]
        if not scored:
            raise RuntimeError("All Azure assessment calls returned no result")

        result = PronunciationResult(
            locale=locale,
            turns=scored,
            aggregate=_aggregate(scored),
        )
        record_generation_output(span, result.aggregate.model_dump_json())

        # AUDIO-COST: what was actually sent to Azure — every submitted turn,
        # not just the ones it managed to recognize (a rejected turn still
        # cost the API call). Computed straight from the raw PCM already in
        # hand, no extra pass over the audio or a session-file duration read.
        audio_seconds = round(
            sum(len(audio) / (sample_rate * _PCM_BYTES_PER_SAMPLE) for _, audio, sample_rate in turns),
            2,
        )
        if audio_seconds > 0:
            usd = stt_cost_usd(audio_seconds, AZURE_PRONUNCIATION_PER_MIN)
            stamp_audio_cost(
                span,
                usage={"audio_seconds": audio_seconds},
                cost={"audio_seconds": usd, "total": usd},
            )
    return result


def _score_turn(text: str, audio: bytes, sample_rate: int, locale: str) -> TurnScore | None:
    """One synchronous Azure call. Invoked off-thread via ``asyncio.to_thread``."""
    wav_bytes = io.BytesIO()
    with wave.open(wav_bytes, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(audio)

    push_stream = speechsdk.audio.PushAudioInputStream()
    push_stream.write(wav_bytes.getvalue())
    push_stream.close()

    speech_config = speechsdk.SpeechConfig(
        subscription=azure_speech_key,
        region=azure_speech_region,
    )
    audio_config = speechsdk.audio.AudioConfig(stream=push_stream)
    pron_config = speechsdk.PronunciationAssessmentConfig(
        reference_text=text,
        grading_system=speechsdk.PronunciationAssessmentGradingSystem.HundredMark,
        granularity=speechsdk.PronunciationAssessmentGranularity.Word,
        enable_miscue=True,
    )
    if locale == "en-US":
        pron_config.enable_prosody_assessment()

    recognizer = speechsdk.SpeechRecognizer(
        speech_config=speech_config,
        language=locale,
        audio_config=audio_config,
    )
    pron_config.apply_to(recognizer)

    result = recognizer.recognize_once_async().get()
    if result.reason != speechsdk.ResultReason.RecognizedSpeech:
        logger.warning(f"Azure did not recognize speech for turn ref={text!r} reason={result.reason}")
        return None

    pa = speechsdk.PronunciationAssessmentResult(result)
    words = [
        WordScore(
            word=w.word,
            accuracy_score=float(w.accuracy_score or 0.0),
            error_type=str(w.error_type),
        )
        for w in (pa.words or [])
    ]
    return TurnScore(
        text=text,
        recognized=result.text or "",
        accuracy_score=float(pa.accuracy_score or 0.0),
        fluency_score=float(pa.fluency_score or 0.0),
        completeness_score=float(pa.completeness_score or 0.0),
        prosody_score=float(pa.prosody_score) if pa.prosody_score is not None else None,
        pron_score=float(pa.pronunciation_score or 0.0),
        words=words,
    )


def _aggregate(turns: list[TurnScore]) -> Aggregate:
    n = len(turns)
    acc = sum(t.accuracy_score for t in turns) / n
    flu = sum(t.fluency_score for t in turns) / n
    comp = sum(t.completeness_score for t in turns) / n
    prosody_vals = [t.prosody_score for t in turns if t.prosody_score is not None]
    pros = (sum(prosody_vals) / len(prosody_vals)) if prosody_vals else None
    pron = sum(t.pron_score for t in turns) / n
    return Aggregate(
        accuracy_score=acc,
        fluency_score=flu,
        completeness_score=comp,
        prosody_score=pros,
        pron_score=pron,
        turns_assessed=n,
    )
