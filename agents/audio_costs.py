"""Deepgram STT / MiniMax TTS pricing for Langfuse audio cost stamping.

Researched 2026-08-21:

- Deepgram nova-3 monolingual STREAMING: $0.0048/min PAYG, billed per
  second (deepgram.com/pricing, current rate card).
- Deepgram nova-3 monolingual PRE-RECORDED: $0.0043/min PAYG (same page).
- Deepgram nova-2 PRE-RECORDED: Deepgram no longer publishes a nova-2
  prerecorded rate on its public pricing page. The nova-3 prerecorded rate
  is used here as a FLAGGED PROXY — confirm the real nova-2 rate in the
  Deepgram console/billing dashboard before trusting this number for an
  actual invoice reconciliation.
- MiniMax speech-2.8-turbo TTS: $60 per 1,000,000 characters =
  $0.00006/char. Corroborated across reseller price lists and
  OpenRouter's direct-serve listing; MiniMax's own pricing page is
  JS-rendered and could not be scraped directly — FLAGGED, confirm in
  the MiniMax console before trusting this number for an actual invoice
  reconciliation.

Both `langfuse.observation.usage_details` and
`langfuse.observation.cost_details` are JSON-ENCODED STRINGS in Langfuse
v4's OTLP ingestion contract (see `agents/observability.py`'s module
docstring for the rest of that pipeline) — `stamp_audio_cost` below is the
one place that encodes them, so every call site stays a plain dict in, no
JSON literal duplicated per call site.
"""

import json

# USD per minute, Deepgram pay-as-you-go rate card (2026-08-21).
DEEPGRAM_NOVA3_STREAMING_PER_MIN = 0.0048
DEEPGRAM_NOVA3_PRERECORDED_PER_MIN = 0.0043
# FLAGGED (unpublished): proxied off the nova-3 prerecorded rate. Confirm
# in the Deepgram console before trusting this for real cost reconciliation.
DEEPGRAM_NOVA2_PRERECORDED_PER_MIN = DEEPGRAM_NOVA3_PRERECORDED_PER_MIN

# USD per character, MiniMax speech-2.8-turbo. FLAGGED: official pricing
# page is JS-rendered — corroborated via resellers + OpenRouter only.
MINIMAX_TTS_PER_CHAR = 0.00006


def stt_cost_usd(seconds: float, per_min_rate: float) -> float:
    """USD cost for `seconds` of audio at a per-minute rate."""
    return (seconds / 60.0) * per_min_rate


def tts_cost_usd(characters: int, per_char_rate: float = MINIMAX_TTS_PER_CHAR) -> float:
    """USD cost for `characters` of TTS text at a per-character rate."""
    return characters * per_char_rate


def stamp_audio_cost(span, *, usage: dict, cost: dict) -> None:
    """JSON-encode and set Langfuse's per-span usage/cost attributes.

    `usage` holds unit counts keyed by usage type (e.g. "audio_seconds",
    "tts_characters"); `cost` holds USD per usage type plus a "total" key
    — the Langfuse v4 convention both attributes follow. `span` is guarded
    against None so every call site can call this unconditionally (e.g. a
    caller that built its span inside a context manager that may not have
    run) without an extra None check of its own.
    """
    if span is None:
        return
    span.set_attribute("langfuse.observation.usage_details", json.dumps(usage))
    span.set_attribute("langfuse.observation.cost_details", json.dumps(cost))
