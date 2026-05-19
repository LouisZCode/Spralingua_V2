from langchain.agents import create_agent
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver

from config import openrouter_api_key, openrouter_base_url
from .dynamic_prompts import personalized_prompt, Context

CONVERSATIONAL_MODEL = "openai/gpt-oss-120b"

_model = ChatOpenAI(
    model=CONVERSATIONAL_MODEL,
    base_url=openrouter_base_url,
    api_key=openrouter_api_key,
    stream_usage=True,  # emit final usage chunk on streamed calls (needed for Langfuse token/cost)
)


def agent_assembly(user_id: int):
    return create_agent(
        model=_model,
        checkpointer=InMemorySaver(),
        middleware=[personalized_prompt],
        context_schema=Context,
    )
