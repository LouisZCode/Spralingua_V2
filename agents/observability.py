"""
OpenTelemetry tracing setup for the Pipecat pipeline.

Pipecat 0.0.98 ships native OTel tracing: when ``PipelineTask`` is
constructed with ``enable_tracing=True``, the framework auto-attaches a
``TurnTrackingObserver`` + ``TurnTraceObserver`` pair and the service
decorators ``@traced_stt`` / ``@traced_tts`` (already applied to
``DeepgramSTTService`` and ``MiniMaxHttpTTSService``) emit spans under
the active turn span. The LLM span is hand-rolled in
``agents/pipecat_wrapper.py::ClientWrapper.astream`` because
``LangchainProcessor`` is a ``FrameProcessor`` (not an ``LLMService``)
and therefore not covered by Pipecat's decorators.

This module wires the OTel exporter at the Langfuse OTLP endpoint. The
Langfuse Python SDK is NOT used — Langfuse ingests OTel spans directly
via its public OTLP collector. Auth is HTTP Basic with the Langfuse
public/secret key pair from the existing ``.env``.
"""

import base64
from contextlib import contextmanager

from loguru import logger
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from pipecat.utils.tracing.setup import setup_tracing

from config import (
    langfuse_public_key,
    langfuse_secret_key,
    langfuse_base_url,
    langfuse_environment,
)


def _build_otlp_exporter() -> OTLPSpanExporter:
    """Construct the OTLP HTTP exporter targeting Langfuse's public OTel endpoint.

    Langfuse expects HTTP Basic auth (``Authorization: Basic base64(PK:SK)``)
    and an ``x-langfuse-ingestion-version`` header per the integration docs.
    The collector path is ``/api/public/otel/v1/traces`` under the configured
    Langfuse host.
    """
    auth_b64 = base64.b64encode(
        f"{langfuse_public_key}:{langfuse_secret_key}".encode()
    ).decode()

    endpoint = f"{langfuse_base_url.rstrip('/')}/api/public/otel/v1/traces"

    return OTLPSpanExporter(
        endpoint=endpoint,
        headers={
            "Authorization": f"Basic {auth_b64}",
            "x-langfuse-ingestion-version": "4",
        },
    )


# Wire OTel once at import time. ``setup_tracing`` registers a
# ``TracerProvider`` globally and wraps the exporter in a
# ``BatchSpanProcessor`` for background flushing. Returns False if OTel
# is unavailable; the pipeline still runs in that case (Pipecat's
# ``enable_tracing=True`` short-circuits to a no-op).
# Langfuse is optional: with no host/keys (e.g. a deploy without the
# LANGFUSE_* vars) skip export entirely instead of crashing at import —
# ``_build_otlp_exporter`` would dereference a None host.
if langfuse_base_url and langfuse_public_key and langfuse_secret_key:
    _tracing_enabled = setup_tracing(
        service_name=f"spralingua-{langfuse_environment}",
        exporter=_build_otlp_exporter(),
    )
else:
    logger.info("Langfuse tracing disabled — LANGFUSE_* not fully configured.")
    _tracing_enabled = False

# Tracer for hand-rolled spans (the LLM span in ``pipecat_wrapper.astream``).
# Pipecat-owned spans use their own tracer names internally.
tracer = trace.get_tracer("spralingua.llm")


@contextmanager
def generation_span(
    name: str,
    *,
    model: str,
    input_text: str,
    system: str = "openrouter",
    operation: str = "chat",
    session_id: str | None = None,
    user_id: str | None = None,
):
    """One Generation span for the one-shot STT/LLM calls that live OUTSIDE
    the Pipecat pipeline (OBS-006): the Satzschmiede attempt path + word
    forge, the tandem debrief, and the drill grammar harvester.

    Mirrors the ``gen_ai.*`` / ``langfuse.*`` attribute conventions of the
    hand-rolled LLM span in ``agents/pipecat_wrapper.py::ClientWrapper.astream``
    so Langfuse ingests the same Generation shape (model, input/output,
    usage, duration). Opened as the *current* span, so nested calls parent
    automatically — the ``satz-attempt`` root adopts its ``stt`` and ``llm``
    children with no explicit context passing. ``session_id`` groups the
    trace into a Langfuse Session (the tandem debrief joins its
    conversation's session); without one the trace stands alone.

    Callers stamp the result via :func:`record_generation_output` once it is
    in hand. With LANGFUSE_* unconfigured the tracer is a no-op and every
    ``set_attribute`` is swallowed — zero cost.
    """
    with tracer.start_as_current_span(name) as span:
        span.set_attribute("gen_ai.system", system)
        span.set_attribute("gen_ai.request.model", model)
        span.set_attribute("gen_ai.operation.name", operation)
        span.set_attribute("langfuse.observation.input", input_text)
        if session_id:
            span.set_attribute("langfuse.session.id", session_id)
        if user_id:
            span.set_attribute("user.id", user_id)
        yield span


def record_generation_output(span, output_text: str, usage: dict | None = None) -> None:
    """Stamp a generation's output + token usage (LangChain ``usage_metadata``
    shape) onto its span — the same fields the pipeline LLM span records."""
    span.set_attribute("langfuse.observation.output", output_text)
    if usage:
        if (n := usage.get("input_tokens")) is not None:
            span.set_attribute("gen_ai.usage.input_tokens", n)
        if (n := usage.get("output_tokens")) is not None:
            span.set_attribute("gen_ai.usage.output_tokens", n)


def unwrap_structured_output(result) -> tuple:
    """Unpack a ``with_structured_output(..., include_raw=True)`` result into
    ``(parsed_model, usage_metadata)``, re-raising the parse error that a
    plain ``with_structured_output`` call would have raised. ``include_raw``
    exists purely so the raw message's token usage can reach the span."""
    if result.get("parsing_error") is not None:
        raise result["parsing_error"]
    return result["parsed"], getattr(result.get("raw"), "usage_metadata", None)


def flush_traces() -> None:
    """Drain queued spans before the process moves on.

    Called from ``pipeline/factory.py::run_pipeline`` in the disconnect
    ``finally`` so a short-lived WebSocket can't lose spans to the
    ``BatchSpanProcessor``'s background batching.
    """
    provider = trace.get_tracer_provider()
    if hasattr(provider, "force_flush"):
        provider.force_flush()
