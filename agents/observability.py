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
_tracing_enabled = setup_tracing(
    service_name=f"spralingua-{langfuse_environment}",
    exporter=_build_otlp_exporter(),
)

# Tracer for hand-rolled spans (the LLM span in ``pipecat_wrapper.astream``).
# Pipecat-owned spans use their own tracer names internally.
tracer = trace.get_tracer("spralingua.llm")


def flush_traces() -> None:
    """Drain queued spans before the process moves on.

    Called from ``pipeline/factory.py::run_pipeline`` in the disconnect
    ``finally`` so a short-lived WebSocket can't lose spans to the
    ``BatchSpanProcessor``'s background batching.
    """
    provider = trace.get_tracer_provider()
    if hasattr(provider, "force_flush"):
        provider.force_flush()
