"""
env api keay load, variables, and defaults

"""

import os
from dotenv import load_dotenv

load_dotenv()

#Deepgram
deepgram_api_key=os.getenv("DEEPGRAM_API_KEY")

#Minimax
minimax_api_key=os.getenv("MINIMAX_API_KEY")
minimax_group_id=os.getenv("MINIMAX_GROUP_ID")

#OpenRouter
openrouter_api_key = os.getenv("OPENROUTER_API_KEY")
openrouter_base_url = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")

#Langfuse
langfuse_public_key  = os.getenv("LANGFUSE_PUBLIC_KEY")
langfuse_secret_key  = os.getenv("LANGFUSE_SECRET_KEY")
langfuse_base_url    = os.getenv("LANGFUSE_BASE_URL")
langfuse_environment = os.getenv("LANGFUSE_TRACING_ENVIRONMENT", "dev")

#Azure Speech (Pronunciation Assessment, PRON-001)
azure_speech_key    = os.getenv("AZURE_SPEECH_KEY")
azure_speech_region = os.getenv("AZURE_SPEECH_REGION", "eastus")