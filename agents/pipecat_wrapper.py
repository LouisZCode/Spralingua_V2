"""
ClientWrapper: makes the LangChain agent compatible with Pipecat's LangchainProcessor.

Each client connection gets its own ClientWrapper instance, holding:
- agent: a fresh LangChain agent with its own InMemorySaver
- user_id: unique thread_id for conversation memory
- logger: per-session transcript logger
"""

import asyncio
import time

from loguru import logger
from pipecat.processors.frameworks.rtvi import (
    RTVIBotOutputMessage,
    RTVIBotOutputMessageData,
)
from pipecat.utils.tracing.turn_context_provider import get_current_turn_context
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
from .observability import tracer

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

    def __init__(self, user_id, session_id, logger, voice="happy_harry", lesson_id="lesson_zero"):
        self.user_id = user_id
        self.session_id = session_id
        self.logger = logger
        self.agent = agent_assembly(user_id)
        self.context = Context(
            lesson_id=lesson_id,
            agent_voice=voice,
            profile=load_profile(user_id),
        )
        self._pipeline_task = None  # Set by factory after pipeline creation
        self.rtvi_processor = None  # Set by factory; used to push bot output to the client
        self._end_task = None
        # End-of-call is detected mid-LLM-stream but DEFERRED until the bot
        # finishes speaking. Otherwise stop_when_done() races: the EndFrame
        # reaches the TurnTrackingObserver and ends the turn before TTS gets
        # the bot's last TextFrame, which orphans the final TTS span (no
        # turn context → falls back to service-level parent → separate trace).
        self._end_pending: bool = False

        self._max_exchanges = load_prompts(lesson_id)["max_exchanges"]
        self._exchange_count = 0

        # Bot reply is buffered here at the end of each LLM stream. The push to
        # the client happens later, when TTSDurationTracker fires its on_turn_complete
        # callback (`flush_bot_output`) with the authoritative audio duration. That
        # way the message arrival on the client lines up with the server-side end of
        # audio, and the frontend can schedule the bubble reveal precisely.
        self._pending_bot_text: str | None = None

        # Full per-turn transcript captured in-memory as (role, text) tuples.
        # Consumed on disconnect by the post-session evaluator (EVAL-001) so it
        # has the full conversation to judge against the lesson's pass_criterion.
        self._transcript: list[tuple[str, str]] = []

        # Per-turn audio + text are captured separately and paired at
        # disconnect via this shared counter. Drift here corrupts every
        # downstream pronunciation score — see LEARNINGS.md 2026-05-26.
        # Counter is ticked by PipelineLatencyObserver on every
        # UserStoppedSpeakingFrame; audio stamps in `append_user_turn_audio`
        # (called from factory.py), text stamps in `astream`'s finally below.
        self._current_vad_seq: int = 0
        self._user_turn_audio: list[tuple[int, bytes, int]] = []
        self._user_turn_text: list[tuple[int, str]] = []
        self._dropped_audio_count: int = 0

    async def astream(self, input_dict, config=None):
        """Translates Pipecat format to agent format and streams tokens.

        Owns the per-turn LLM OTel span. Pipecat's built-in TurnTraceObserver
        (enabled via ``PipelineTask(enable_tracing=True)``) opens a turn span
        on each spoken turn and registers it with ``TurnContextProvider``; we
        look it up here and start a child span so the LLM call nests under
        the turn alongside the auto-emitted STT and TTS spans.

        Attributes emitted follow OTel ``gen_ai.*`` semantic conventions so
        Langfuse's cost engine can read ``input_tokens`` / ``output_tokens``
        directly; ``input`` / ``output`` carry the user message and full
        reply for evaluation; the custom block (voice/lesson_id/exchange)
        preserves the per-turn metadata we used to put on the manual
        Langfuse Generation.

        ``LangchainProcessor`` itself is uninstrumented (it's a
        ``FrameProcessor``, not an ``LLMService``), so this is the only LLM
        span on the trace.
        """
        text = input_dict.get("input", "")
        messages = {"messages": [{"role": "user", "content": text}]}

        self._exchange_count += 1

        run_config = {"configurable": {"thread_id": self.user_id}}

        full_response = []
        ttft_ns = None
        final_usage = None

        # ``get_current_turn_context()`` returns ``None`` if tracing is
        # disabled OR if no turn span is active right now. Either way OTel
        # falls back to the current context, which is the right behavior:
        # no parent → span becomes a root span, harmless.
        turn_ctx = get_current_turn_context()
        span_start_ns = time.time_ns()

        with tracer.start_as_current_span(
            "llm",
            context=turn_ctx,
        ) as llm_span:
            llm_span.set_attribute("gen_ai.system", "openrouter")
            llm_span.set_attribute("gen_ai.request.model", CONVERSATIONAL_MODEL)
            llm_span.set_attribute("gen_ai.operation.name", "chat")
            llm_span.set_attribute("gen_ai.output.type", "text")
            # Observation-level input — shown on the LLM observation row in Langfuse.
            llm_span.set_attribute("langfuse.observation.input", text)
            # Trace-level input — populates the "Input" column on the conversation
            # trace itself. Setting on any span in the trace works; we set it here
            # (last LLM call wins for multi-turn lessons).
            llm_span.set_attribute("langfuse.trace.input", text)
            llm_span.set_attribute("voice", self.context.agent_voice)
            llm_span.set_attribute("lesson_id", self.context.lesson_id)
            llm_span.set_attribute("exchange", self._exchange_count)

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
                        # appears. Detected in-stream (post-yield code is unreliable)
                        # but only MARKED as pending here — the actual stop_when_done()
                        # call happens in flush_bot_output() after BotStoppedSpeakingFrame
                        # so the final TTS span gets to record under the turn before
                        # EndFrame races through and closes the turn span. See
                        # LEARNINGS.md for the race condition that motivates this.
                        if (not self._end_pending
                                and (self._exchange_count >= self._max_exchanges
                                     or _contains_goodbye("".join(full_response)))):
                            reason = (
                                "max_exchanges" if self._exchange_count >= self._max_exchanges
                                else "goodbye"
                            )
                            logger.info(
                                f"[END] Pending pipeline close ({reason}, "
                                f"exchange {self._exchange_count}/{self._max_exchanges}) "
                                f"— will fire after bot finishes speaking"
                            )
                            self._end_pending = True

                    if getattr(token, "usage_metadata", None):
                        final_usage = token.usage_metadata
            finally:
                output_text = "".join(full_response)
                llm_span.set_attribute("langfuse.observation.output", output_text)
                llm_span.set_attribute("langfuse.trace.output", output_text)
                if final_usage:
                    if (n := final_usage.get("input_tokens")) is not None:
                        llm_span.set_attribute("gen_ai.usage.input_tokens", n)
                    if (n := final_usage.get("output_tokens")) is not None:
                        llm_span.set_attribute("gen_ai.usage.output_tokens", n)
                if ttft_ns is not None:
                    # Keep our existing TTFT metric as a custom attribute. Span
                    # duration itself now covers full LLM streaming (not just
                    # first-token), which matches what gen_ai.* conventions expect.
                    llm_span.set_attribute(
                        "metrics.ttft_ms",
                        (ttft_ns - span_start_ns) / 1_000_000,
                    )

                # Stash the full reply; the actual push to the client now happens
                # in `flush_bot_output`, invoked by TTSDurationTracker once TTS has
                # finished streaming audio (server-side end-of-speech). The framework
                # would otherwise dispatch duplicates (AggregatedTextFrame on the LLM
                # side AND TTSTextFrame on the TTS side); we still emit exactly one
                # message per turn, just timed against the audio.
                self._pending_bot_text = output_text.strip() or None

                # Capture the turn for the post-session evaluator. Runs in the
                # finally so even partial turns (interruptions, errors mid-stream)
                # land in the transcript with whatever the bot managed to say.
                self._transcript.append(("user", text))
                self._transcript.append(("bot", output_text))
                # Stamp the user text with the current VAD-stop seq so the
                # pronunciation evaluator can pair it with the matching audio
                # at disconnect (BUG-002). Empty/whitespace inputs are skipped
                # — `/say` injection or the (extremely rare) all-whitespace
                # transcript shouldn't reach Azure as a reference text anyway.
                if text.strip():
                    self._user_turn_text.append((self._current_vad_seq, text))

        # After first LLM call, capture system prompt for transcript
        if self.logger and not self.logger._system_prompt_written:
            prompt = get_last_system_prompt()
            if prompt:
                self.logger.write_system_prompt(prompt)

    def render_transcript(self) -> str:
        """Format the captured turns as a single string for the evaluator prompt."""
        return "\n\n".join(f"{role.capitalize()}: {body}" for role, body in self._transcript)

    def vad_stop(self) -> None:
        """Tick the VAD-stop sequence number. Called by
        ``PipelineLatencyObserver`` on every ``UserStoppedSpeakingFrame``.
        The counter is the shared identifier that pairs audio (stamped in
        ``append_user_turn_audio``) with text (stamped in ``astream``'s
        finally block) at disconnect — see LEARNINGS.md 2026-05-26 (BUG-002).
        """
        self._current_vad_seq += 1

    def append_user_turn_audio(self, audio: bytes, sample_rate: int) -> None:
        """Buffer one user turn's audio with the current VAD-stop seq.
        Called by the audiobuffer's ``on_user_turn_audio_data`` event handler
        in ``pipeline/factory.py``."""
        self._user_turn_audio.append((self._current_vad_seq, audio, sample_rate))

    def has_user_turn_audio(self) -> bool:
        return bool(self._user_turn_audio)

    def iter_user_turn_audio(self):
        """Yield ``(text, audio_bytes, sample_rate)`` for each user turn that
        has both a captured audio clip and a non-empty transcript.

        Pairs via the shared VAD-stop seq stamped on both sides during the
        session. Orphan audio (silent VAD trigger, breath, mic glitch, or an
        STT miss where Deepgram produced no transcript) is dropped silently
        and counted in ``_dropped_audio_count`` for the session log.
        """
        text_by_seq = {seq: t for seq, t in self._user_turn_text if t.strip()}
        for seq, audio, sr in self._user_turn_audio:
            if seq in text_by_seq:
                yield text_by_seq[seq], audio, sr
            else:
                self._dropped_audio_count += 1
        if self._dropped_audio_count:
            logger.info(
                f"Dropped {self._dropped_audio_count} audio chunk(s) with no "
                f"matching transcript (silent VAD / STT miss)"
            )

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

        # If end-of-call was detected mid-LLM-stream, NOW is the safe moment
        # to fire stop_when_done(): the bot has finished speaking, the final
        # TTS span is closed under the turn, and the EndFrame can race through
        # the observers without orphaning any in-flight span.
        if self._end_pending and self._end_task is None and self._pipeline_task:
            logger.info("[END] Bot finished speaking — closing pipeline")
            self._end_task = asyncio.create_task(self._end_pipeline())

    async def _end_pipeline(self):
        """Gracefully end the pipeline once in-flight TTS audio is done streaming.

        ``stop_when_done()`` queues an ``EndFrame`` at the pipeline source. The
        frame travels downstream behind whatever is already in transit (LLM
        text frames, TTS audio frames), so the bot's final reply finishes
        playing before the pipeline shuts down. The earlier ``queue_frame
        (CancelTaskFrame())`` approach didn't work — that path injects the
        frame *downstream* from the source, but ``CancelTaskFrame`` only
        triggers cancellation when it reaches ``PipelineTask._source_push_frame``
        from *upstream*, so it just slid through inert.
        """
        if self._pipeline_task:
            await self._pipeline_task.stop_when_done()
