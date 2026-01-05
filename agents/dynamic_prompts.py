"""
Here we will contruct the dynamic prompts that will be given to the AI Dynamically.
"""
from .load_prompts import load_prompts
prompts = load_prompts()
beginner_conversation_prompt = prompts["beginner_conversationalist_prompt"]
expert_conversation_prompt = prompts["expert_conversationalist_prompt"]

from dataclasses import dataclass
from langchain.agents.middleware import dynamic_prompt, ModelRequest

@dataclass
class Context:
    user_level: str = "expert"  #beginner, intermediate, advanced

@dynamic_prompt
def level_based_prompt(request: ModelRequest) -> str:
    level = request.runtime.context.user_level

    if level == "beginner":
        return beginner_conversation_prompt
    elif level == "expert":
        return expert_conversation_prompt