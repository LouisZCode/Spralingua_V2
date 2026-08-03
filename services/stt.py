"""
Here we load the Speech-To-Text service. Right now we are using:

Deepgram

You find here:
stt_deepgram
"""
from pipecat.services.deepgram.stt import DeepgramSTTService
from deepgram import LiveOptions
from config import deepgram_api_key

# Single source of truth — both stt_deepgram() and the STT trace observer
# in pipeline/observers.py read these. Keep in sync with LiveOptions below.
DEEPGRAM_MODEL = "nova-3"
DEEPGRAM_PROVIDER = "deepgram"


def stt_deepgram(language: str = "de", keyterms: list[str] | None = None):
    return DeepgramSTTService(
        api_key=deepgram_api_key,
        live_options=LiveOptions(
            language=language,         # "de" runtime; "en" for the welcome concierge (set per-lesson in factory)
            model=DEEPGRAM_MODEL,      # Deepgram model
            smart_format=True,         # Better formatting
            utterance_end_ms=1000,     # Wait 1s after last word for utterance boundary
            # STT-005: German "um" is a load-bearing conjunction (um…zu), but
            # nova-3's default filler stripping eats it even in fluent speech
            # (A/B-verified 2026-08-03 on the prerecorded endpoint). de only —
            # English lessons keep the cleaner filler-free transcripts.
            filler_words=(language == "de"),
            # STT-003 P2: keyterm prompting biases nova-3 toward words this
            # lesson is likely to elicit (regional greetings, setting nouns) —
            # words a general model may normalise or mishear. Per-lesson list
            # from the YAML `keyterms:` via factory; nova-3-only, ignored on
            # nova-2. None (English lessons, or no list) omits the param.
            keyterm=list(keyterms) if keyterms else None,
        )
    )
