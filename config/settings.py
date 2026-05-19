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