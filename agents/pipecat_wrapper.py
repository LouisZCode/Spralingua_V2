"""
Here you will find the wrapper to make the langchain create agent work with pipecat.
"""

from .conversation import _raw_agent, CONVERSATIONAL_MODEL
from .dynamic_prompts import Context

# Wrapper function for Pipecat compatibility
async def _astream(input_dict, config=None):
    """Translates Pipecat format to agent format and streams tokens."""
    text = input_dict.get("input", "")
    messages = {"messages": [{"role": "user", "content": text}]}

    # Add thread_id for InMemorySaver
    run_config = {"configurable": {"thread_id": "voice-session"}}

    # Use stream_mode="messages" for token-by-token streaming
    async for token, metadata in _raw_agent.astream(
        messages,
        config=run_config,
        context=Context(user_level="expert"),
        stream_mode="messages"
    ):
        # Only yield content from model node (not tool calls)
        if hasattr(token, "content") and token.content:
            yield token.content


# Export wrapper that Pipecat can use
class conversation_agent:
    model = CONVERSATIONAL_MODEL
    astream = staticmethod(_astream)