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
from pipecat.processors.frameworks.rtvi import (
    RTVIBotOutputMessage,
    RTVIBotOutputMessageData,
)
from pydantic import ConfigDict


class _BotOutputDataWithDuration(RTVIBotOutputMessageData):
    """Bot-output payload that carries the turn's TTS audio duration.

    Pipecat's ``RTVIBotOutputMessageData`` defaults to Pydantic's ``extra=ignore``,
    so adding ``audio_duration_ms`` as a free field would be silently dropped on
    serialization. ``extra="allow"`` opts in to round-tripping our extra so the
    frontend can read it and schedule the bubble reveal after audio playback.
    """

    model_config = ConfigDict(extra="allow")
    audio_duration_ms: float


class _BotOutputMessageWithDuration(RTVIBotOutputMessage):
    """Bot-output envelope whose ``data`` is typed as the duration-aware subclass.

    Without this override the outer model's field type is the *base*
    ``RTVIBotOutputMessageData`` and Pydantic uses *that* schema when serializing,
    which strips the ``audio_duration_ms`` field even when the instance is the
    subclass. Pointing the field at the subclass keeps the extra during
    ``model_dump()``.
    """

    data: _BotOutputDataWithDuration

from .conversation_agent import agent_assembly, CONVERSATIONAL_MODEL
from .dynamic_prompts import Context, get_last_system_prompt
from .fake_profiles import load_profile
from .load_prompts import load_prompts
from .observability import langfuse_client

# `max_exchanges` is now per-lesson, read from the YAML in `ClientWrapper.__init__`.
# End-of-call fires when either the count cap is reached OR a goodbye phrase
# appears in the agent's reply — whichever comes first.

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
        self.rtvi_processor = None  # Set by factory; used to push bot output to the client
        self._end_task = None

        self._max_exchanges = load_prompts(lesson_id)["max_exchanges"]
        self._exchange_count = 0

        # Bot reply is buffered here at the end of each LLM stream. The push to
        # the client happens later, when TTSDurationTracker fires its on_turn_complete
        # callback (`flush_bot_output`) with the authoritative audio duration. That
        # way the message arrival on the client lines up with the server-side end of
        # audio, and the frontend can schedule the bubble reveal precisely.
        self._pending_bot_text: str | None = None

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

                        # End trigger: either count cap reached OR goodbye phrase
                        # appears. Scheduled in-stream (post-yield code is unreliable).
                        if (self._end_task is None
                                and self._pipeline_task
                                and (self._exchange_count >= self._max_exchanges
                                     or _contains_goodbye("".join(full_response)))):
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

                # Stash the full reply; the actual push to the client now happens
                # in `flush_bot_output`, invoked by TTSDurationTracker once TTS has
                # finished streaming audio (server-side end-of-speech). The framework
                # would otherwise dispatch duplicates (AggregatedTextFrame on the LLM
                # side AND TTSTextFrame on the TTS side); we still emit exactly one
                # message per turn, just timed against the audio.
                self._pending_bot_text = "".join(full_response).strip() or None

        # After first LLM call, capture system prompt for transcript
        if self.logger and not self.logger._system_prompt_written:
            prompt = get_last_system_prompt()
            if prompt:
                self.logger.write_system_prompt(prompt)

    async def flush_bot_output(self, audio_duration_ms: float) -> None:
        """Push the buffered bot reply to the RTVI client with the turn's audio duration.

        Called by ``TTSDurationTracker.on_turn_complete`` after the last TTS audio
        frame has gone out. ``audio_duration_ms`` is the authoritative server-side
        TTS audio length; the frontend uses it (plus the ``botStartedSpeaking``
        timestamp it captures on receive) to reveal the bubble after playback
        finishes in the browser.
        """
        text = self._pending_bot_text
        if not text or self.rtvi_processor is None:
            self._pending_bot_text = None
            return

        msg = _BotOutputMessageWithDuration(
            data=_BotOutputDataWithDuration(
                text=text,
                spoken=True,
                aggregated_by="turn",
                audio_duration_ms=audio_duration_ms,
            )
        )
        await self.rtvi_processor.push_transport_message(msg)
        self._pending_bot_text = None

    async def _end_pipeline(self):
        """Wait for TTS to finish the goodbye, then force-close the pipeline."""
        await asyncio.sleep(2)
        if self._pipeline_task:
            await self._pipeline_task.queue_frame(CancelTaskFrame())
