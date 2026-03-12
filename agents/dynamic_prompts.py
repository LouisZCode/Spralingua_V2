"""
Here we will construct the dynamic prompts that will be given to the AI Dynamically.
"""
from .load_prompts import load_prompts
prompts = load_prompts()
conversation_prompt = prompts["conversationalist_prompt"]

from dataclasses import dataclass
from langchain.agents.middleware import dynamic_prompt, ModelRequest

@dataclass
class Context:
    # This will come from the database / UI selection in the future:
    user_name: str = "Luis"
    user_level: str = "A1"
    situation: str = "introducing_yourself"
    agent_voice: str = "happy_harry"
    agent_personality: str = "friendly"

# Module-level variable to capture last generated prompt (for logging)
_last_system_prompt = None

def get_last_system_prompt() -> str:
    """Return the last generated system prompt (for transcript logging)."""
    return _last_system_prompt

@dynamic_prompt
def personalized_prompt(request: ModelRequest) -> str:
    global _last_system_prompt
    ctx = request.runtime.context

    level = prompts["levels"][ctx.user_level]
    language_rules = level["language_rules"]

    situation_data = level["situations"][ctx.situation]
    if isinstance(situation_data, dict):
        situation = situation_data.get("description", "")
        opening_phrase = situation_data.get("opening_phrase", "")
        conversation_flow = situation_data.get("conversation_flow", "")
        required_vocabulary = situation_data.get("required_vocabulary", "")
        topic_rules = situation_data.get("topic_rules", "")
        steering = situation_data.get("steering", "")
    else:
        # Backward compat: plain string
        situation = situation_data
        opening_phrase = conversation_flow = required_vocabulary = topic_rules = steering = ""

    agent_story = prompts["agent_story"].get(ctx.agent_voice, "")
    agent_personality = prompts["agent_personality"][ctx.agent_personality]

    prompt = conversation_prompt.format(
        language_rules=language_rules,
        situation=situation,
        opening_phrase=opening_phrase,
        conversation_flow=conversation_flow,
        required_vocabulary=required_vocabulary,
        topic_rules=topic_rules,
        agent_story=agent_story,
        agent_personality=agent_personality,
        steering=steering,
    )
    _last_system_prompt = prompt
    return prompt
