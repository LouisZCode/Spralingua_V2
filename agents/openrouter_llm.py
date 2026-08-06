"""ChatOpenAI subclass that preserves OpenRouter's top-level ``provider`` field.

langchain_openai copies only an allowlist of response fields into
``response_metadata`` and silently drops OpenRouter's ``provider`` extension
(the actually-served upstream, e.g. "Cerebras") — but the openai SDK keeps
it in ``ChatCompletion.model_extra``. Rescuing it here lets every judge span
record which provider actually served the call — the whole point of the
Cerebras-pin diagnostics (OBS-008): a call that silently fell off Cerebras
goes from ~2s to 30-70s, and without this field the served provider was
recorded nowhere.

Installed langchain_openai's ``_create_chat_result`` signature (verified in
``.venv/.../langchain_openai/chat_models/base.py``)::

    def _create_chat_result(
        self,
        response: dict | openai.BaseModel,
        generation_info: dict | None = None,
    ) -> ChatResult

Both the plain ``ainvoke`` path and the structured-output beta-parse path
route through it. After it returns, langchain_core reassigns
``message.response_metadata = {**generation_info, **message.response_metadata}``
and then ``{**llm_output, **message.response_metadata}`` — the message's own
dict WINS both merges, so the key stamped here survives onto the final
AIMessage that ``unwrap_structured_output`` sees.

NOT used by ``agents/conversation_agent.py``: the live voice agent streams,
and streamed chunks are assembled by ``_astream``/``_convert_chunk_to_generation_chunk``
— a different code path ``_create_chat_result`` never touches, so this
subclass would add nothing there (see the Task 3 note in
``agents/pipecat_wrapper.py``).
"""

import asyncio

import httpx
from langchain_core.runnables import Runnable, RunnableLambda
from langchain_openai import ChatOpenAI
from loguru import logger

from config import cerebras_api_key, openrouter_api_key, openrouter_base_url


class ProviderChatOpenAI(ChatOpenAI):
    """ChatOpenAI that copies OpenRouter's served ``provider`` into
    ``response_metadata["openrouter_provider"]`` on every generation."""

    def _create_chat_result(self, response, generation_info=None):
        result = super()._create_chat_result(response, generation_info)
        provider = None
        extra = getattr(response, "model_extra", None)
        if extra:
            provider = extra.get("provider")
        elif isinstance(response, dict):
            provider = response.get("provider")
        if provider:
            for gen in result.generations:
                gen.message.response_metadata["openrouter_provider"] = provider
        return result


_judge_http_client: httpx.AsyncClient | None = None


def judge_http_client() -> httpx.AsyncClient:
    """Lazily-created singleton ``httpx.AsyncClient`` shared by every judge/
    evaluator LLM call, with keep-alive reuse disabled.

    langchain_openai's own default async client is ``lru_cache``d per
    ``(base_url, timeout)`` (``langchain_openai/chat_models/_client_utils.py``)
    — every judge shares that one pool. A connection Railway's NAT / the
    OpenRouter LB drops without an RST looks alive to the pool but hangs on
    first use, and the openai SDK's default ``max_retries=2`` silently retries
    the same way twice: 61-120s user-facing waits on what should be a 1-5s
    call (2026-07-16 prod traces). Passing this client via ``http_async_client=``
    bypasses that cache (``langchain_openai/chat_models/base.py:983-987``), and
    ``max_keepalive_connections=0`` means no connection is ever handed out a
    second time — every judge call pays a fresh TCP+TLS handshake
    (~100-300ms), which is fine for non-streaming request/response calls.
    """
    global _judge_http_client
    if _judge_http_client is None:
        _judge_http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(10.0),
            limits=httpx.Limits(max_keepalive_connections=0, max_connections=100),
            follow_redirects=True,
        )
    return _judge_http_client


class DeadlineChatOpenAI(ProviderChatOpenAI):
    """``ProviderChatOpenAI`` with a hard TOTAL-time deadline on the async
    non-streaming generation path (2026-07 Cerebras-direct cutover).

    Why a wall-clock deadline instead of relying on ``timeout=``: OpenRouter
    was observed to intermittently PARK a pinned, non-streaming request for
    ~60s before serving it — the TCP connection stays open and bytes keep
    trickling at the transport level, so httpx's per-read timeout never
    fires. ``httpx.Timeout`` (and the openai SDK's ``timeout=``/``max_retries=``
    on top of it) only bounds the gap between reads, not the call's total
    duration. ``asyncio.wait_for`` is the one mechanism that bounds the
    WHOLE await regardless of what the connection is doing underneath, so a
    parked call reliably converts into an ``asyncio.TimeoutError`` that
    ``RunnableWithFallbacks`` (default ``exceptions_to_handle=(Exception,)``;
    ``asyncio.TimeoutError`` is ``TimeoutError``, an ``Exception`` subclass)
    catches to trigger the fallback leg.

    Verified override point (installed
    ``.venv/lib/python3.12/site-packages/langchain_openai/chat_models/base.py``):
    ``BaseChatOpenAI._agenerate`` (line ~1577) is the single async hook both
    non-streaming code paths route through — the ``response_format``/
    ``.with_raw_response.parse(...)`` branch (structured output via
    ``method="json_schema"``) AND the plain ``.with_raw_response.create(...)``
    branch (structured output via ``method="function_calling"``, which binds
    the schema as a tool and parses the tool call afterward — no
    ``response_format`` in the payload, so it falls through to the same
    plain-create branch a non-structured call uses). Confirmed the dispatch
    chain that reaches it: ``Runnable.ainvoke`` -> ``BaseChatModel.ainvoke``
    -> ``agenerate_prompt`` -> ``agenerate`` -> ``_agenerate_with_cache``
    (``langchain_core/language_models/chat_models.py``), which — when
    streaming isn't implicitly requested (the judges never call
    ``astream``/``astream_events``) — does
    ``inspect.signature(self._agenerate).parameters.get("run_manager")`` and
    then ``await self._agenerate(messages, stop=stop, run_manager=run_manager,
    **kwargs)``. That inspects the BOUND method, i.e. this override, so
    keeping the ``run_manager`` parameter name here is required for the
    kwarg to be passed through. Both ``with_structured_output(method=...)``
    paths build a ``RunnableSequence`` of ``(possibly tool-bound) self |
    output_parser`` — the model half still goes through ``ainvoke`` ->
    ``_agenerate`` exactly as above, so wrapping ``_agenerate`` here covers
    every judge call site (all use ``.with_structured_output(..., include_raw=True)``
    today; a hypothetical plain-text ``.ainvoke()`` call is covered too, same hook).

    Worst-case math (per leg, ``deadline_s=12.0`` default):
    - Happy path: 0.5-2s (Cerebras direct, healthy).
    - Cerebras having a bad moment: 12s deadline trips -> fallback leg
      serves in ~1-3s (OpenRouter, ``allow_fallbacks=True`` so the router
      can serve off ANY upstream, not just retry the same parked path) =>
      ~13-15s total.
    - Absolute worst case (both legs dead/parked): ~12s + ~12s = ~24s, then
      the ``TimeoutError`` from the fallback leg propagates out of
      ``RunnableWithFallbacks`` and the caller's route sees an exception ->
      existing 503 handling.
    """

    deadline_s: float = 12.0

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        return await asyncio.wait_for(
            super()._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs),
            timeout=self.deadline_s,
        )


# --- Cerebras-primary / OpenRouter-fallback judge factory (2026-07) --------
#
# Production judges used to call OpenRouter pinned to Cerebras
# (`extra_body={"provider": {"order": ["cerebras"], "allow_fallbacks": False}}`).
# OpenRouter was observed to intermittently PARK that pinned, non-streaming
# request for ~60s before serving it. Fix: judges call Cerebras DIRECTLY
# (fast fail-fast semantics, no router hop), with OpenRouter kept as an
# automatic fallback leg (`allow_fallbacks=True` there — if OpenRouter itself
# is the one serving, let it route off Cerebras rather than park waiting for
# it), and a `DeadlineChatOpenAI` hard total deadline on both legs so a
# parked/stuck call converts into a failure that trips the fallback instead
# of hanging.

CEREBRAS_BASE_URL = "https://api.cerebras.ai/v1"
CEREBRAS_JUDGE_MODEL = "gpt-oss-120b"
OPENROUTER_JUDGE_MODEL = "openai/gpt-oss-120b"

_missing_cerebras_key_warned = False


def _log_fallback_engaged(x):
    """Loguru WARNING fired exactly once per invocation, the moment the
    fallback leg engages — piped in front of the fallback model so it only
    logs when the primary (Cerebras direct) leg actually failed. Production
    signal that Cerebras is having a bad moment."""
    logger.warning("Judge primary (Cerebras direct) failed — falling back to OpenRouter")
    return x


def _cerebras_leg(deadline_s: float, temperature: float | None = None) -> DeadlineChatOpenAI:
    return DeadlineChatOpenAI(
        model=CEREBRAS_JUDGE_MODEL,
        base_url=CEREBRAS_BASE_URL,
        api_key=cerebras_api_key,
        timeout=10,
        max_retries=1,
        http_async_client=judge_http_client(),
        deadline_s=deadline_s,
        **({} if temperature is None else {"temperature": temperature}),
    )


def _openrouter_leg(deadline_s: float, temperature: float | None = None) -> DeadlineChatOpenAI:
    return DeadlineChatOpenAI(
        model=OPENROUTER_JUDGE_MODEL,
        base_url=openrouter_base_url,
        api_key=openrouter_api_key,
        timeout=10,
        max_retries=1,
        http_async_client=judge_http_client(),
        deadline_s=deadline_s,
        **({} if temperature is None else {"temperature": temperature}),
        # allow_fallbacks=True here (unlike the old pinned wiring): this leg
        # only runs when Cerebras-direct already failed, so let OpenRouter
        # serve off any upstream rather than risk parking again waiting
        # specifically for Cerebras. OpenRouter's documented `order` semantics
        # still try Cerebras first.
        extra_body={"provider": {"order": ["cerebras"], "allow_fallbacks": True}},
    )


def _judge_legs(
    deadline_s: float, temperature: float | None = None
) -> tuple[DeadlineChatOpenAI, DeadlineChatOpenAI | None]:
    """Build the (primary, fallback) leg pair. ``fallback`` is ``None`` only
    in the no-Cerebras-key degraded mode, where ``primary`` IS the OpenRouter
    leg (deadline still applied) and there is nothing left to fall back to."""
    if not cerebras_api_key:
        global _missing_cerebras_key_warned
        if not _missing_cerebras_key_warned:
            logger.warning("CEREBRAS_API_KEY not set — judges running OpenRouter-only")
            _missing_cerebras_key_warned = True
        return _openrouter_leg(deadline_s, temperature), None
    return _cerebras_leg(deadline_s, temperature), _openrouter_leg(deadline_s, temperature)


def structured_judge_llm(
    schema,
    *,
    method: str = "json_schema",
    include_raw: bool = True,
    strict: bool | None = True,
    deadline_s: float = 12.0,
    temperature: float | None = None,
) -> Runnable:
    """Cerebras-direct primary + OpenRouter-fallback structured-output judge.

    TEMPERATURE (2026-08-07): defaults to ``None`` = don't send the field at
    all, which is what every judge did before this parameter existed, i.e.
    the provider default of 1.0. That is sampling, and it means a judge can
    return DIFFERENT verdicts for byte-identical input — measured on the
    Satzbau clause judge, which accepted a valid German word order twice and
    rejected it once out of three identical calls, the rejection asserting a
    grammar rule that was backwards. A learner resubmitting the same correct
    sentence being told they are wrong is corrosive in a way a consistently
    strict judge is not. Pass ``temperature=0`` for any judge whose job is a
    verdict rather than prose. Left opt-in rather than defaulted so this
    doesn't silently change the behaviour of the judges that predate it.

    Drop-in replacement for the old
    ``ProviderChatOpenAI(...).with_structured_output(schema, include_raw=True)``
    construction — returns a ``Runnable`` whose ``.ainvoke(prompt)`` yields
    the same ``{"raw", "parsed", "parsing_error"}`` dict shape
    ``agents.observability.unwrap_structured_output`` expects, whichever leg
    served it.

    ``method="json_schema"`` + ``strict=True`` is pinned on BOTH legs
    (2026-07-18): with the earlier ``method="function_calling"`` the model
    wrote the verdict JSON as free-form tool args, and Cerebras
    ``gpt-oss-120b`` intermittently omitted or ``null``-ed a required field
    (observed in prod: ``{"word_ok": true}`` with no ``grammar_ok`` → pydantic
    ``ValidationError`` → 502 "check hiccuped"; ~3 of 35 calls in one
    session). Strict ``response_format`` makes the server constrain decoding
    to the schema, so an answer missing a required field cannot be generated.
    Note ``json_schema`` binds NO tools, so the old "Cerebras won't take
    tools + response_format together" conflict doesn't apply. Same method on
    the fallback leg keeps the two legs' request shape identical so a
    fallback mid-flight isn't also a method switch.

    SCHEMA SIZE: Cerebras strict mode caps the generated JSON schema at
    5,000 characters (enforced from 2026-07-21) — class docstring and Field
    descriptions count. When adding or growing a judge schema, re-check all
    9 with ``uv run python speedtest/strict_lint.py`` (local-only). Largest
    today: szenario ``StructureVerdict`` at 4,202 — see the SIZE BUDGET
    comment above that class.

    Composition order matters: structured output is applied to each ChatOpenAI
    leg FIRST, then ``.with_fallbacks(...)`` — ``RunnableWithFallbacks`` has
    no ``.with_structured_output`` of its own.
    """
    primary, fallback = _judge_legs(deadline_s, temperature)
    primary_structured = primary.with_structured_output(
        schema, method=method, include_raw=include_raw, strict=strict
    )
    if fallback is None:
        return primary_structured
    fallback_structured = fallback.with_structured_output(
        schema, method=method, include_raw=include_raw, strict=strict
    )
    return primary_structured.with_fallbacks(
        [RunnableLambda(_log_fallback_engaged) | fallback_structured]
    )


def plain_judge_llm(*, deadline_s: float = 12.0) -> Runnable:
    """Cerebras-direct primary + OpenRouter-fallback judge for plain
    (non-structured-output) text completions. None of the current 9 judge/
    evaluator call sites need this today — every one of them uses
    ``.with_structured_output(...)`` — but it exists as the plain-text
    counterpart to :func:`structured_judge_llm` for a future non-structured
    judge, built from the exact same legs/deadline/fallback-logging wiring.
    """
    primary, fallback = _judge_legs(deadline_s)
    if fallback is None:
        return primary
    return primary.with_fallbacks([RunnableLambda(_log_fallback_engaged) | fallback])
