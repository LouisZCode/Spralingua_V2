"""
Langfuse observability — single process-wide client.

Used directly by:
- ``pipeline/observers.py::TurnTraceObserver`` for STT and TTS Generation
  observations.
- ``agents/pipecat_wrapper.py::ClientWrapper.astream`` for the LLM Generation.

The LangChain ``CallbackHandler`` is intentionally NOT used here — we own
all three per-turn Generations manually so each measures its model's
time-to-first-output and exposes ``input``/``output``/``usage_details`` on
the LLM trace. See OBS-004 plan + ``pipeline/observers.py`` docstring.

Cost: deferred. ``usage_details`` is populated on the LLM Generation, so
Langfuse will calculate costs automatically once model pricing rules are
configured in the dashboard.
"""

from langfuse import Langfuse

from config import (
    langfuse_public_key,
    langfuse_secret_key,
    langfuse_base_url,
    langfuse_environment,
)

# Singleton client — auth happens once here, events are batched and sent in background.
langfuse_client = Langfuse(
    public_key=langfuse_public_key,
    secret_key=langfuse_secret_key,
    host=langfuse_base_url,
    environment=langfuse_environment,
)
