from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain_cerebras import ChatCerebras
from langgraph.checkpoint.memory import InMemorySaver
from .dynamic_prompts import personalized_prompt, Context

load_dotenv()
CONVERSATIONAL_MODEL = "gpt-oss-120b"

_model = ChatCerebras(model=CONVERSATIONAL_MODEL)


def agent_assembly(user_id : int):
    return create_agent(
        model=_model,
        checkpointer=InMemorySaver(),
        middleware=[personalized_prompt],
        context_schema=Context
        )