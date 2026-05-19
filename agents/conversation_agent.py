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
    stream_usage=True,                       # final usage chunk on streamed OpenAI calls (token counts → Langfuse)
    extra_body={
        # Pin to Cerebras for speed. Cost tracking via Langfuse is deferred —
        # see LEARNINGS.md 2026-05-19 entry.
        "provider": {
            "order": ["cerebras"],
            "allow_fallbacks": True,
        },
    },
)


def agent_assembly(user_id: int):
    return create_agent(
        model=_model,
        checkpointer=InMemorySaver(),
        middleware=[personalized_prompt],
        context_schema=Context,
    )
