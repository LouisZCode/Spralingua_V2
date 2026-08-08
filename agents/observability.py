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
import os
from contextlib import contextmanager

from loguru import logger
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.trace import Status, StatusCode
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
    # OBS-008: pipecat's setup_tracing (.venv/…/pipecat/utils/tracing/setup.py)
    # builds its Resource with an EXPLICIT dict —
    #   Resource.create({"service.name": ..., "deployment.environment":
    #       os.getenv("ENVIRONMENT", "development"), ...})
    # — and opentelemetry-sdk's Resource.create() is
    #   get_aggregated_resources(detectors, _DEFAULT_RESOURCE).merge(Resource(explicit_dict))
    # where Resource.merge(other) has `other` WIN on key collisions. The
    # explicit dict is `other` here, so it always overrides whatever the
    # OTELResourceDetector parsed from OTEL_RESOURCE_ATTRIBUTES — setting
    # that env var would be silently discarded. The var pipecat actually
    # reads is the bare `ENVIRONMENT` name. `setdefault` only fills it when
    # unset, so an operator-set ENVIRONMENT (Railway or otherwise) is never
    # clobbered by our langfuse_environment default.
    os.environ.setdefault("ENVIRONMENT", langfuse_environment)
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


def record_generation_output(
    span,
    output_text: str,
    usage: dict | None = None,
    response_metadata: dict | None = None,
) -> None:
    """Stamp a generation's output + token usage (LangChain ``usage_metadata``
    shape) onto its span — the same fields the pipeline LLM span records.

    ``response_metadata`` (OBS-008) is the raw AIMessage's
    ``response_metadata`` dict, as returned by
    :func:`unwrap_structured_output`. Empirical probe (see that function's
    docstring): stock langchain_openai only copies a fixed allowlist of
    fields off the raw ChatCompletion into ``response_metadata`` and
    silently drops OpenRouter's non-standard top-level ``provider`` key
    (the actually-served upstream, e.g. "Cerebras"), even though the openai
    SDK preserves it in ``ChatCompletion.model_extra`` — so every judge now
    constructs its LLM via ``agents.openrouter_llm.ProviderChatOpenAI``,
    which rescues that field into
    ``response_metadata["openrouter_provider"]``. NOTE:
    ``response_metadata["model_provider"]`` is NOT the served provider — it
    is a LangChain-internal constant equal to ``"openai"`` on every call
    regardless of which upstream served it, so it is deliberately never
    mapped here. ``model_name`` is the requested/routing model id OpenRouter
    echoes back; the bare ``provider`` fallback covers a future
    langchain_openai that starts propagating the key natively.
    """
    span.set_attribute("langfuse.observation.output", output_text)
    if usage:
        if (n := usage.get("input_tokens")) is not None:
            span.set_attribute("gen_ai.usage.input_tokens", n)
        if (n := usage.get("output_tokens")) is not None:
            span.set_attribute("gen_ai.usage.output_tokens", n)
    if response_metadata:
        if (m := response_metadata.get("model_name")) is not None:
            span.set_attribute("gen_ai.response.model", m)
        provider = response_metadata.get("openrouter_provider") or response_metadata.get("provider")
        if provider is not None:
            span.set_attribute("openrouter.provider", provider)


def mark_span_error(span, message: str) -> None:
    """Stamp a span as a Langfuse-visible ERROR (SATZ-021).

    Every other helper in this module records a happy-path Generation; this
    one exists for the opposite case — a call that ran to completion (no
    exception) but whose RESULT is a failure the rest of the system should
    still surface loudly, e.g. the Satzschmiede forge gate exhausting every
    verify/retry round and shipping an unverified card anyway rather than
    blocking the learner. Sets the OTel span status to ERROR (so it reads as
    a failed span on the trace itself) and stamps
    ``langfuse.observation.level``/``langfuse.observation.status_message``
    (Langfuse's own error-level attributes) so it also shows up as an error
    in the Langfuse UI, not just another green Generation. Same no-op-safe
    contract as the rest of this module: with LANGFUSE_* unconfigured the
    tracer is a no-op and every call here is swallowed at zero cost.
    """
    span.set_status(Status(StatusCode.ERROR, message))
    span.set_attribute("langfuse.observation.level", "ERROR")
    span.set_attribute("langfuse.observation.status_message", message)


def unwrap_structured_output(result) -> tuple:
    """Unpack a ``with_structured_output(..., include_raw=True)`` result into
    ``(parsed_model, usage_metadata, response_metadata)``, re-raising the
    parse error that a plain ``with_structured_output`` call would have
    raised. ``include_raw`` exists so the raw message's token usage AND
    response metadata can reach the span.

    OBS-008 empirical probe (one live ``ainvoke`` through this exact wiring,
    ``openai/gpt-oss-120b`` via OpenRouter pinned to Cerebras): the raw
    AIMessage's ``response_metadata`` carries ``token_usage``,
    ``model_provider`` (LangChain's own hardcoded ``"openai"`` label — NOT
    the upstream-served provider), ``model_name`` (``"openai/gpt-oss-120b"``,
    the requested routing id, echoed back unchanged regardless of which
    provider served it), ``system_fingerprint``, ``id``, ``finish_reason``,
    ``logprobs``. A parallel raw HTTP call to the same OpenRouter endpoint
    (bypassing LangChain) confirmed OpenRouter's response body DOES carry a
    top-level ``provider`` string (e.g. ``"OpenInference"``/``"Cerebras"``),
    and the openai SDK's ``ChatCompletion.model_extra`` even preserves it —
    but stock langchain_openai's chat-result parsing never copies that key
    into ``response_metadata`` or ``additional_kwargs``. The judges therefore
    build their LLMs via ``agents.openrouter_llm.ProviderChatOpenAI``, whose
    ``_create_chat_result`` override rescues it into
    ``response_metadata["openrouter_provider"]`` — verified live to survive
    langchain_core's post-hoc metadata merges. ``response_metadata`` is
    returned as-is (``{}`` if the raw message has none) so
    ``record_generation_output`` can pull ``model_name`` +
    ``openrouter_provider`` off it.
    """
    if result.get("parsing_error") is not None:
        raise result["parsing_error"]
    raw = result.get("raw")
    usage = getattr(raw, "usage_metadata", None)
    response_metadata = getattr(raw, "response_metadata", None) or {}
    return result["parsed"], usage, response_metadata


def flush_traces() -> None:
    """Drain queued spans before the process moves on.

    Called from ``pipeline/factory.py::run_pipeline`` in the disconnect
    ``finally`` so a short-lived WebSocket can't lose spans to the
    ``BatchSpanProcessor``'s background batching.
    """
    provider = trace.get_tracer_provider()
    if hasattr(provider, "force_flush"):
        # B1: force_flush() is a synchronous, thread-blocking OTel call. Bound
        # it so a slow Langfuse OTLP endpoint can't stall the caller for its
        # unbounded default — the factory.py call site also runs this off the
        # event loop via asyncio.to_thread for the same reason.
        provider.force_flush(timeout_millis=2000)
