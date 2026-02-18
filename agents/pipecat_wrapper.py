"""
ClientWrapper: makes the LangChain agent compatible with Pipecat's LangchainProcessor.

Each client connection gets its own ClientWrapper instance, holding:
- agent: a fresh LangChain agent with its own InMemorySaver
- user_id: unique thread_id for conversation memory
- logger: per-session transcript logger
"""

from .conversation_agent import agent_assembly, CONVERSATIONAL_MODEL
from .dynamic_prompts import Context, get_last_system_prompt


class ClientWrapper:
    model = CONVERSATIONAL_MODEL

    def __init__(self, user_id, logger):
        self.user_id = user_id
        self.logger = logger
        self.agent = agent_assembly(user_id)

    async def astream(self, input_dict, config=None):
        """Translates Pipecat format to agent format and streams tokens."""
        text = input_dict.get("input", "")
        messages = {"messages": [{"role": "user", "content": text}]}

        run_config = {"configurable": {"thread_id": self.user_id}}

        async for token, metadata in self.agent.astream(
            messages,
            config=run_config,
            context=Context(),
            stream_mode="messages"
        ):
            if hasattr(token, "content") and token.content:
                yield token.content

        # After first LLM call, capture system prompt for transcript
        if self.logger and not self.logger._system_prompt_written:
            prompt = get_last_system_prompt()
            if prompt:
                self.logger.write_system_prompt(prompt)
