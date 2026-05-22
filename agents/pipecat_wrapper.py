"""
ClientWrapper: makes the LangChain agent compatible with Pipecat's LangchainProcessor.

Each client connection gets its own ClientWrapper instance, holding:
- agent: a fresh LangChain agent with its own InMemorySaver
- user_id: unique thread_id for conversation memory
- logger: per-session transcript logger
"""

import asyncio
import time

from langfuse import propagate_attributes
from pipecat.frames.frames import CancelTaskFrame

from .conversation_agent import agent_assembly, CONVERSATIONAL_MODEL
from .dynamic_prompts import Context, get_last_system_prompt
from .fake_profiles import load_profile
from .observability import langfuse_client

# Per-client cap for goodbye detection. Once this many exchanges have
# happened AND the agent emits a goodbye phrase, `_end_pipeline` is
# scheduled. Previously per-situation via
# `prompts.yaml::levels.*.situations.*.number_of_exchanges`; hoisted here
# now that the language-learning yaml content lives in the archive.
MAX_EXCHANGES = 8

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

    def __init__(self, user_id, session_id, logger, level="A1", situation="introducing_yourself", voice="happy_harry", lesson_id="lesson_zero"):
        self.user_id = user_id
        self.session_id = session_id
        self.logger = logger
        self.agent = agent_assembly(user_id)
        self.context = Context(
            lesson_id=lesson_id,
            user_level=level,
            situation=situation,
            agent_voice=voice,
            profile=load_profile(user_id),
        )
        self._pipeline_task = None  # Set by factory after pipeline creation
        self._end_task = None

        self._max_exchanges = MAX_EXCHANGES
        self._exchange_count = 0

    async def astream(self, input_dict, config=None):
        """Translates Pipecat format to agent format and streams tokens.

        Owns the per-turn ``turn-N-LLM`` Langfuse Generation: opens at
        chain dispatch with the user message as input, accumulates streamed
        tokens into a buffer, captures the first-token timestamp (TTFT),
        extracts the final ``usage_metadata`` chunk for token counts, and
        on stream completion writes ``output`` + ``usage_details`` via
        ``.update(...)`` (only valid while the span is still recording per
        langfuse 4.6.1 ``span.py:654``), then closes with
        ``.end(end_time=ttft_ns)`` so duration = TTFT.

        ``.end()`` accepts only ``end_time`` in 4.6.1 (``span.py:206``);
        all other fields must be set via ``.update()`` first.
        """
        text = input_dict.get("input", "")
        messages = {"messages": [{"role": "user", "content": text}]}

        self._exchange_count += 1

        run_config = {"configurable": {"thread_id": self.user_id}}

        full_response = []
        ttft_ns = None
        final_usage = None

        with propagate_attributes(
            session_id=self.session_id,
            user_id=self.user_id,
            tags=[self.context.user_level, self.context.situation, "LLM"],
            trace_name=f"turn-{self._exchange_count}-LLM",
        ):
            gen = langfuse_client.start_observation(
                name=f"turn-{self._exchange_count}-LLM",
                as_type="generation",
                model=CONVERSATIONAL_MODEL,
                input=text,
                metadata={
                    "service": "cerebras-via-openrouter",
                    "level": self.context.user_level,
                    "situation": self.context.situation,
                    "voice": self.context.agent_voice,
                    "exchange": self._exchange_count,
                },
            )
            try:
                async for token, _ in self.agent.astream(
                    messages,
                    config=run_config,
                    context=self.context,
                    stream_mode="messages",
                ):
                    if hasattr(token, "content") and token.content:
                        if ttft_ns is None:
                            ttft_ns = time.time_ns()
                        full_response.append(token.content)
                        yield token.content

                        # Detect goodbye during streaming (post-yield code is unreliable)
                        if (self._exchange_count >= self._max_exchanges
                                and self._end_task is None
                                and self._pipeline_task
                                and _contains_goodbye("".join(full_response))):
                            print(f"[END] Scheduling pipeline close (exchange {self._exchange_count}/{self._max_exchanges})")
                            self._end_task = asyncio.create_task(self._end_pipeline())

                    if getattr(token, "usage_metadata", None):
                        final_usage = token.usage_metadata
            finally:
                usage = None
                if final_usage:
                    usage = {
                        "input": final_usage.get("input_tokens"),
                        "output": final_usage.get("output_tokens"),
                        "total": final_usage.get("total_tokens"),
                    }
                # Write fields BEFORE end (post-end updates are dropped).
                gen.update(
                    output="".join(full_response) or None,
                    usage_details=usage,
                )
                gen.end(end_time=ttft_ns)

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
