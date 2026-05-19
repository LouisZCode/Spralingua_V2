"""
Here we load the Text-To-Speech service. Right now we are using:

Minimax

You find here:
tts_minimax
"""
import aiohttp
import asyncio

from pipecat.services.minimax.tts import MiniMaxHttpTTSService
from config import minimax_api_key, minimax_group_id
from pipecat.transcriptions.language import Language

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

def tts_minimax(session, voice: str = "happy_harry"):
    voice_id = VOICE_MAP.get(voice, "german_bavarian_male_v2")
    return MiniMaxHttpTTSService(
        api_key=minimax_api_key,
        group_id=minimax_group_id,
        aiohttp_session=session,
        model="speech-2.8-turbo",   # speech-02-turbo (fast), speech-02-hd (quality) - constructor param
        voice_id=voice_id,
        params=MiniMaxHttpTTSService.InputParams(
            speed=1.0,                 # 0.5 to 2.0
            pitch=0,                   # -12 to 12
            volume=1.0,                # 0 to 10
            emotion="neutral",         # happy, sad, angry, fearful, disgusted, surprised, neutral, fluent
            language=Language.EN,      # Language enum (ES, EN, DE, FR, etc.)
        )
    )
