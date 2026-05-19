"""
ClientWrapper: makes the LangChain agent compatible with Pipecat's LangchainProcessor.

Each client connection gets its own ClientWrapper instance, holding:
- agent: a fresh LangChain agent with its own InMemorySaver
- user_id: unique thread_id for conversation memory
- logger: per-session transcript logger
"""

import asyncio

from pipecat.frames.frames import CancelTaskFrame

from .conversation_agent import agent_assembly, CONVERSATIONAL_MODEL
from .dynamic_prompts import Context, get_last_system_prompt, prompts
from .observability import turn_callback_handler

GOODBYE_PHRASES = [
    "goodbye", "bye", "see you", "take care",
    "nice talking", "great talking", "good talking",
    "talk to you later", "talk soon", "have a good",
    "have a nice", "it was nice meeting",
]


def _contains_goodbye(text: str) -> bool:
    lowered = text.lower()
    return any(phrase in lowered for phrase in GOODBYE_PHRASES)


class ClientWrapper:
    model = CONVERSATIONAL_MODEL

    def __init__(self, user_id, logger, level="A1", situation="introducing_yourself", voice="happy_harry"):
        self.user_id = user_id
        self.logger = logger
        self.agent = agent_assembly(user_id)
        self.context = Context(
            user_level=level,
            situation=situation,
            agent_voice=voice,
        )
        self._pipeline_task = None  # Set by factory after pipeline creation
        self._end_task = None

        situation_data = prompts["levels"][level]["situations"][situation]
        self._max_exchanges = situation_data.get("number_of_exchanges", 8) if isinstance(situation_data, dict) else 8
        self._exchange_count = 0

    async def astream(self, input_dict, config=None):
        """Translates Pipecat format to agent format and streams tokens."""
        text = input_dict.get("input", "")
        messages = {"messages": [{"role": "user", "content": text}]}

        # Increment first so the handler/metadata reflect this turn's number.
        self._exchange_count += 1

        # Fresh Langfuse handler per turn → fresh trace_id.
        # session_id groups all turns of this WebSocket connection in the UI.
        handler = turn_callback_handler()
        run_config = {
            "configurable": {"thread_id": self.user_id},
            "callbacks": [handler],
            "metadata": {
                "langfuse_session_id": self.user_id,
                "langfuse_user_id": self.user_id,  # random UUID today; revisit when auth lands
                "langfuse_tags": [self.context.user_level, self.context.situation],
                "level": self.context.user_level,
                "situation": self.context.situation,
                "voice": self.context.agent_voice,
                "exchange": self._exchange_count,
            },
            "run_name": f"turn-{self._exchange_count}",
        }

        full_response = []

        async for token, metadata in self.agent.astream(
            messages,
            config=run_config,
            context=self.context,
            stream_mode="messages",
        ):
            if hasattr(token, "content") and token.content:
                full_response.append(token.content)
                yield token.content

                # Detect goodbye during streaming (post-yield code is unreliable)
                if (self._exchange_count >= self._max_exchanges
                        and self._end_task is None
                        and self._pipeline_task
                        and _contains_goodbye("".join(full_response))):
                    print(f"[END] Scheduling pipeline close (exchange {self._exchange_count}/{self._max_exchanges})")
                    self._end_task = asyncio.create_task(self._end_pipeline())

        # After first LLM call, capture system prompt for transcript
        if self.logger and not self.logger._system_prompt_written:
            prompt = get_last_system_prompt()
            if prompt:
                self.logger.write_system_prompt(prompt)

    async def _end_pipeline(self):
        """Wait for TTS to finish the goodbye, then force-close the pipeline."""
        await asyncio.sleep(2)
        if self._pipeline_task:
            await self._pipeline_task.queue_frame(CancelTaskFrame())
