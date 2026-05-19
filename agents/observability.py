"""
Langfuse observability — single client, per-turn callback handlers.

The Langfuse client is a process-wide singleton (it's a thread-safe event sender,
not stateful per-conversation). The CallbackHandler, however, is created fresh
per turn so each turn gets its own trace_id and stays isolated — same per-client
isolation rule as the rest of the pipeline (see CLAUDE.md).

Scope: Langfuse here captures **token counts, latency, prompt/response, metadata,
and traces grouped by session**. Per-call USD cost is deferred (see LEARNINGS.md
2026-05-19 entry — OpenRouter doesn't ship cost on streamed responses).
"""

from langfuse import Langfuse
from langfuse.langchain import CallbackHandler

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


def turn_callback_handler() -> CallbackHandler:
    """Fresh handler per turn — keeps trace_id isolated to one exchange."""
    return CallbackHandler()
