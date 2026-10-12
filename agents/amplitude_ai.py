"""Amplitude Agent Analytics bootstrap + shared emit helpers.

Instrumentation entry point for the server-side LLM layer (Agent Analytics
Phase 3). Four module-level agents (singletons — never per-request, or every
turn gets a different ``[Agent] Agent ID`` and session grouping breaks):

- ``conversation-partner`` — live voice turns (``ClientWrapper.astream``).
- ``drill-judge`` — one-shot verdicts on a learner's answer.
- ``content-forge`` — background content generation (cards, drafts, letters,
  glosses, explanations, nudges — including their verify passes).
- ``post-session`` — disconnect-side harvesters (debrief, error extractor,
  goal evaluator).

PRIVACY: ``content_mode="metadata_only"`` — token counts, latency, model and
cost flow to Amplitude; prompt/response TEXT does not. The emit helpers below
still pass the real texts (the SDK drops them under this mode), so flipping
to ``"full"`` later needs no call-site change. Deliberate: Langfuse already
owns content-level traces under legitimate interest; Amplitude answers
"how is it used", not "what did they say".

RESILIENCE: every public helper here is fail-open — tracking must never break
a drill, a voice turn, or boot. Missing ``AMPLITUDE_AI_API_KEY`` degrades to
a no-op after one warning (same contract as the Sentry DSN).
"""

import os
from contextlib import contextmanager

from loguru import logger

# Langfuse-facing session ids carry a surface prefix (tandem-/teacher-/
# lesson-/demo-); the DB/activity id — and every Amplitude session — is the
# bare hex. Strip the closed prefix set so HTTP handlers holding only the
# prefixed form (Clara's exercise routes via get_trace_session) still join
# the same Amplitude session as the WS turns.
_TRACE_PREFIXES = ("tandem-", "teacher-", "lesson-", "demo-")


def normalize_session_id(session_id: str | None) -> str | None:
    if not session_id:
        return None
    for prefix in _TRACE_PREFIXES:
        if session_id.startswith(prefix):
            return session_id[len(prefix):] or None
    return session_id

_CONVERSATION_PARTNER = ("conversation-partner", "Live voice partner turns (tandem/teacher/respond)")
_DRILL_JUDGE = ("drill-judge", "One-shot verdicts on learner answers")
_CONTENT_FORGE = ("content-forge", "Background content generation + verify passes")
_POST_SESSION = ("post-session", "Disconnect-side harvesters (debrief, error harvest, goal eval)")

CONVERSATION_PARTNER_ID, CONVERSATION_PARTNER_DESC = _CONVERSATION_PARTNER
DRILL_JUDGE_ID, DRILL_JUDGE_DESC = _DRILL_JUDGE
CONTENT_FORGE_ID, CONTENT_FORGE_DESC = _CONTENT_FORGE
POST_SESSION_ID, POST_SESSION_DESC = _POST_SESSION

# Canonical model id for cost attribution. Both legs serve Cerebras
# ``gpt-oss-120b`` (direct, or via OpenRouter's cerebras-first routing) —
# never the gateway's product label (e.g. ``openai/gpt-oss-120b``), which
# genai-prices cannot price and would silently omit ``[Agent] Cost USD``.
CANONICAL_MODEL = "gpt-oss-120b"

_ai = None
_agents: dict = {}
_no_key_warned = False


def get_ai():
    """Module-level ``AmplitudeAI`` singleton, or ``None`` when disabled."""
    global _ai
    if _ai is not None:
        return _ai
    global _no_key_warned
    if not os.environ.get("AMPLITUDE_AI_API_KEY"):
        if not _no_key_warned:
            logger.warning("AMPLITUDE_AI_API_KEY not set — Agent Analytics disabled")
            _no_key_warned = True
        return None
    from amplitude import Amplitude
    from amplitude_ai import AIConfig, AmplitudeAI

    _ai = AmplitudeAI(
        amplitude=Amplitude(os.environ["AMPLITUDE_AI_API_KEY"]),
        config=AIConfig(content_mode="metadata_only"),
    )
    return _ai


def get_agent(agent_id: str, description: str):
    """Module-level agent singleton, or ``None`` when tracking is disabled."""
    ai = get_ai()
    if ai is None:
        return None
    if agent_id not in _agents:
        _agents[agent_id] = ai.agent(agent_id, description=description)
    return _agents[agent_id]


def flush_ai() -> None:
    """Flush queued events. Sync by SDK design (returns futures) — safe to
    call from sync and async contexts; never raises."""
    try:
        ai = get_ai()
        if ai is not None:
            ai.flush()
    except Exception as e:
        logger.warning(f"Agent Analytics flush failed (non-fatal): {e}")


@contextmanager
def ai_session(agent_id: str, description: str, user_id: str, session_id: str | None):
    """Open an agent session for one request/sitting, or no-op.

    Sync context manager so it composes into the existing sync ``with``
    Langfuse-span lines (``with propagate..., tracer...span, ai_session(...)``)
    — one-line diffs, no re-indentation. Safe in async handlers: the SDK's
    ``Session`` supports sync enter, and contextvars propagate across awaits
    in the same task.

    Skips (yields ``None``) when tracking is disabled OR the caller has no
    stable session id — a random id per request would fragment every sitting
    into unjoinable one-turn sessions, worse than no session at all. The id
    is prefix-normalized (see ``normalize_session_id``) so handlers holding
    only the Langfuse-prefixed form still join the bare-hex session.
    Flushes on exit so a long-lived uvicorn worker never holds events past
    the request (the SDK auto-flushes on exit in serverless only).
    """
    session_id = normalize_session_id(session_id)
    agent = get_agent(agent_id, description)
    if agent is None or not session_id:
        yield None
        return
    try:
        with agent.session(user_id=user_id, session_id=session_id):
            yield session_id
    except Exception as e:
        logger.warning(f"Agent Analytics session failed (non-fatal): {e}")
        yield None
    finally:
        flush_ai()


def emit_conversation_turn(
    *,
    user_id: str,
    session_id: str,
    trace_id: str,
    user_text: str,
    ai_text: str,
    model: str,
    provider: str,
    latency_ms: float,
    ttfb_ms: float | None,
    input_tokens: int | None,
    output_tokens: int | None,
    context: dict | None = None,
) -> None:
    """Emit a voice-turn user+AI pair with EXPLICIT binding (no session
    context required — the Pipecat pipeline task may not inherit the
    ``agent.session()`` contextvars from the connection task). The factory's
    session frame (Start on connect, End on disconnect) carries the same
    ``session_id``, so turns and framing join server-side. Never raises."""
    try:
        ai = get_ai()
        if ai is None:
            return
        base = dict(
            user_id=user_id,
            session_id=session_id,
            trace_id=trace_id,
            agent_id=CONVERSATION_PARTNER_ID,
            context=context,
        )
        ai.track_user_message(content=user_text or "[voice turn]", **base)
        ai.track_ai_message(
            content=ai_text or "[voice reply]",
            model=model,
            provider=provider,
            latency_ms=latency_ms,
            ttfb_ms=ttfb_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            **base,
        )
    except Exception as e:
        logger.warning(f"Agent Analytics conversation emit failed (non-fatal): {e}")


def emit_one_shot_turn(
    *,
    user_text: str,
    ai_text: str,
    model: str,
    provider: str,
    latency_ms: float,
    input_tokens: int | None,
    output_tokens: int | None,
    context: dict | None = None,
    is_error: bool = False,
    error_message: str | None = None,
) -> None:
    """Emit a user+AI pair bound to the ACTIVE session context (read via
    ``get_active_context``). No active session → drop silently: an unbound
    event joins nothing and pollutes the project. Never raises."""
    try:
        from amplitude_ai.context import get_active_context

        ctx = get_active_context()
        if ctx is None or not getattr(ctx, "session_id", None):
            return
        ai = get_ai()
        if ai is None:
            return
        base = dict(
            user_id=ctx.user_id,
            session_id=ctx.session_id,
            trace_id=ctx.trace_id,
            agent_id=ctx.agent_id,
            context=context,
        )
        ai.track_user_message(content=user_text or "[request]", **base)
        ai.track_ai_message(
            content=ai_text or "[response]",
            model=model,
            provider=provider,
            latency_ms=latency_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            is_error=is_error,
            error_message=error_message,
            **base,
        )
    except Exception as e:
        logger.warning(f"Agent Analytics emit failed (non-fatal): {e}")
