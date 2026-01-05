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

def tts_minimax(session):
    return MiniMaxHttpTTSService(
        api_key=minimax_api_key,
        group_id=minimax_group_id,
        aiohttp_session=session,
        model="speech-02-turbo",   # speech-02-turbo (fast), speech-02-hd (quality) - constructor param
        voice_id="german_bavarian_male_v2",  # Your cloned voice or system voice, german_bavarian_female, german_bavarian_male_v2 , Calm_Woman, - constructor param
        params=MiniMaxHttpTTSService.InputParams(
            speed=1.0,                 # 0.5 to 2.0
            pitch=0,                   # -12 to 12
            volume=1.0,                # 0 to 10
            emotion="neutral",         # happy, sad, angry, fearful, disgusted, surprised, neutral, fluent
            language=Language.EN,      # Language enum (ES, EN, DE, FR, etc.)
        )
    )