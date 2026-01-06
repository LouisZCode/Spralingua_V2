"""
Here we will contruct the dynamic prompts that will be given to the AI Dynamically.
"""
from .load_prompts import load_prompts
prompts = load_prompts()
conversation_prompt = prompts["conversationalist_prompt"]

from dataclasses import dataclass
from langchain.agents.middleware import dynamic_prompt, ModelRequest

@dataclass
class Context:
    #This will come from the database in the future:
    user_name : str = "Luis"
    topic : str = "the user"
    user_level: str = "A1"
    current_topic :str = "topic_1"




@dynamic_prompt
def personalized_prompt(request: ModelRequest) -> str:
    ctx = request.runtime.context
    conversation_goal = prompts["conversation_goal"][ctx.user_level][ctx.current_topic]

    #These ones maybe to come from the user selection in the UI? specially the last []
    agent_story = prompts["agent_story"]["happy_harry"]
    agent_personality = prompts["agent_personality"]["friendly"]

    return conversation_prompt.format(
        name = ctx.user_name,
        conversation_goal = conversation_goal,
        topic = ctx.topic,
        agent_story = agent_story,
        agent_personality = agent_personality
    )
