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
DEEPGRAM_MODEL = "nova-2"
DEEPGRAM_PROVIDER = "deepgram"


def stt_deepgram():
    return DeepgramSTTService(
        api_key=deepgram_api_key,
        live_options=LiveOptions(
            language="en",             # Language input
            model=DEEPGRAM_MODEL,      # Deepgram model
            smart_format=True,         # Better formatting
            utterance_end_ms=1000,     # Wait 1s after last word for utterance boundary
        )
    )
