from dotenv import load_dotenv
from langchain.agents import create_agent
from langgraph.checkpoint.memory import InMemorySaver
from .dynamic_prompts import level_based_prompt, Context

load_dotenv()
CONVERSATIONAL_MODEL = "openai:gpt-4.1-nano-2025-04-14"

#gpt-4o-mini
#gpt-4.1-nano-2025-04-14

_raw_agent = create_agent(
    model=CONVERSATIONAL_MODEL,
    checkpointer=InMemorySaver(),
    middleware=[level_based_prompt],
    context_schema=Context
)


