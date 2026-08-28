from langchain.agents import create_agent
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver

from config import openrouter_api_key, openrouter_base_url
from .dynamic_prompts import Context
from .conversational_prompt import (
    conversational_prompt_middleware,        # noqa: F401 — V1 (standalone), kept for cheap revert / A-B
    conversational_prompt_v2_middleware,     # noqa: F401 — V2 (standalone, no personalization), kept for cheap revert
    layered_prompt_middleware,
)

CONVERSATIONAL_MODEL = "openai/gpt-oss-120b"


def _build_model(reasoning_effort: str | None = None) -> ChatOpenAI:
    extra_body = {
        # Pin to Cerebras for speed. Cost tracking via Langfuse is live: the
        # per-turn `llm` span (agents/pipecat_wrapper.py::ClientWrapper.astream,
        # since 2026-08-21) records usage + model, priced by a Langfuse model
        # definition matching the served model name.
        # allow_fallbacks stays True here ONLY — every one-shot judge
        # (satz/sprechen/bauteil/verbindungen/szenario judges, evaluator,
        # error_extractor, debrief) was flipped to False (OBS-008): a slow
        # turn beats a dead call mid-conversation for the live voice agent,
        # but a judge that falls off Cerebras goes 2s -> 30-70s silently, so
        # those fail fast onto the pinned provider instead. This asymmetry
        # is deliberate — do not "fix" it into consistency.
        "provider": {
            "order": ["cerebras"],
            "allow_fallbacks": True,
        },
    }
    if reasoning_effort is not None:
        # OpenRouter passes this straight through to Cerebras and it cuts
        # gpt-oss-120b's hidden reasoning tokens materially — confirmed with
        # speedtest/reasoning_probe.py (not committed; speedtest/ is
        # gitignored): reasoning_tokens dropped from ~100/reply at the
        # provider default ("medium") to ~9/reply at "low", 3 runs each.
        extra_body["reasoning_effort"] = reasoning_effort
    return ChatOpenAI(
        model=CONVERSATIONAL_MODEL,
        base_url=openrouter_base_url,
        api_key=openrouter_api_key,
        stream_usage=True,                   # final usage chunk on streamed OpenAI calls (token counts → Langfuse)
        timeout=30,
        extra_body=extra_body,
    )


_model = _build_model()


def agent_assembly(user_id: int, reasoning_effort: str | None = None):
    # `reasoning_effort` defaults to None, which reuses the shared `_model`
    # singleton — byte-identical behavior for every caller that doesn't pass
    # it. Passing a value (currently only ClientWrapper, teacher lessons
    # only — see pipecat_wrapper.py) builds a dedicated model instance
    # instead, so no other lesson type's model is affected.
    model = _model if reasoning_effort is None else _build_model(reasoning_effort)
    return create_agent(
        model=model,
        checkpointer=InMemorySaver(),
        # Active: layered prompt (V2 body from yaml + short-term + long-term).
        # Reads `Context.user_level` and `Context.profile` at each call.
        # To temporarily drop personalization and use the bare V2 prompt, swap to
        # `conversational_prompt_v2_middleware`. To A/B back to V1, swap to
        # `conversational_prompt_middleware`. All three are imported above.
        middleware=[layered_prompt_middleware],
        context_schema=Context,
    )
