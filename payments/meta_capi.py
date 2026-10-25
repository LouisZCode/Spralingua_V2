"""Server-side Meta Conversions API Purchase events (P3, 2026-10-25).

Fires the ``Purchase`` conversion from the Stripe webhook straight to
Meta's Graph API, alongside — deduplicated against — the browser pixel
event in ``PricingSuccess.tsx``. Both carry the Stripe checkout session id
as the event id (browser ``eventID`` option / server ``event_id`` field),
which is Meta's documented dedup key: the two events merge into ONE
Purchase, never a double count.

Why server-side at all: the browser event dies with the tab (closed before
the success page's poll confirms) or with the payment method — a SEPA
debit confirms days after the redirect, the success page times out at 20 s,
and the webhook's ``async_payment_succeeded`` lands when the learner is
long gone. The webhook always runs, so the advertiser's optimizer hears
about every real purchase.

Inert until configured: without ``META_CAPI_TOKEN`` nothing is sent — the
webhook calls :func:`track_purchase_capi` unconditionally and it no-ops,
the same optional-key pattern as ``SENTRY_DSN`` / ``AZURE_SPEECH_KEY``.
``META_PIXEL_ID`` overrides the pixel id (fallback = the production pixel,
mirroring ``MetaPixel.tsx``), and ``META_CAPI_TEST_EVENT_CODE`` routes
events to Meta's Test Events tool instead of live optimization for
verification runs.

Consent (LEGAL-003): fires ONLY when the checkout session's metadata
carries ``analytics_consent="1"`` — the browser stamped its consent flag
into the session at checkout start (``Pricing.tsx`` →
``lib/fbpixel.ts::metaTrackingContext``), so a learner who declined never
has a purchase reported server-side, exactly like the pixel's own gate.

Privacy: the only identity sent is a SHA-256 hash of the account email
(irreversible, industry standard for CAPI matching) plus Meta's own
``_fbp``/``_fbc`` first-party cookies when the browser had them. Never a
name, never a readable email, never anything from a learning record — the
privacy page's purchase paragraph discloses exactly this.

Fail-open by construction: fulfillment (tier change / coin credit) happens
BEFORE this runs, an exception here can never roll it back, and the HTTP
call itself is scheduled as a background task so the webhook's response to
Stripe is never delayed by Meta's round-trip.
"""

import asyncio
import hashlib
import logging
import os
import time
from typing import Any

import aiohttp
from sqlalchemy.ext.asyncio import AsyncSession

from database.orm import User

logger = logging.getLogger(__name__)

# Distinguish "disabled" from "broken": the no-token no-op logs ONE INFO
# line per process. A silent no-op cost a full diagnosis session on
# 2026-10-25 (webhook ran on a pre-token deployment and said nothing).
_disabled_logged = False

_GRAPH_URL = "https://graph.facebook.com/v21.0"
# Same fallback as frontend/src/components/MetaPixel.tsx — the production
# pixel, so a token alone is enough to go live.
_FALLBACK_PIXEL_ID = "1433402562081357"
_TIMEOUT_S = 6.0


def _capi_token() -> str | None:
    return os.environ.get("META_CAPI_TOKEN") or None


def _pixel_id() -> str:
    return os.environ.get("META_PIXEL_ID") or _FALLBACK_PIXEL_ID


async def track_purchase_capi(
    db: AsyncSession,
    *,
    user_id: str,
    checkout_session_id: str,
    value: float | None,
    currency: str | None,
    metadata: dict[str, Any] | None,
) -> None:
    """Report one Purchase server-side; no-op unless configured + consented.

    Called from both webhook branches next to the Amplitude event. All DB
    reads (the email hash) happen inline while the webhook's session is
    open; only the HTTP POST is a fire-and-forget task.
    """
    token = _capi_token()
    if not token:
        global _disabled_logged
        if not _disabled_logged:
            _disabled_logged = True
            logger.info(
                "Meta CAPI: META_CAPI_TOKEN not set — server Purchase reporting disabled"
            )
        return
    meta = metadata if isinstance(metadata, dict) else {}
    if meta.get("analytics_consent") != "1":
        logger.debug(
            f"Meta CAPI: skipping purchase {checkout_session_id} — no analytics consent"
        )
        return
    if value is None or not currency:
        logger.warning(
            f"Meta CAPI: purchase {checkout_session_id} has no amount/currency — skipping"
        )
        return

    # Identity for matching: hashed email (+ Meta's own browser cookies when
    # the browser carried them). Hashing here, never logged in clear.
    user = await db.get(User, user_id)
    user_data: dict[str, str] = {}
    if user and user.email:
        user_data["em"] = hashlib.sha256(user.email.strip().lower().encode()).hexdigest()
    for key in ("fbp", "fbc"):
        v = meta.get(key)
        if isinstance(v, str) and v:
            user_data[key] = v
    if not user_data:
        logger.debug(f"Meta CAPI: purchase {checkout_session_id} has no match keys — skipping")
        return

    payload: dict[str, Any] = {
        "data": [
            {
                "event_name": "Purchase",
                "event_time": int(time.time()),
                # THE dedup key — same id the browser pixel event carries.
                "event_id": checkout_session_id,
                "action_source": "website",
                "event_data": {"currency": currency, "value": value},
                "user_data": user_data,
            }
        ],
        "access_token": token,
    }
    test_code = os.environ.get("META_CAPI_TEST_EVENT_CODE")
    if test_code:
        payload["test_event_code"] = test_code

    # Fire-and-forget: the webhook must not wait on (or fail because of)
    # Meta's round-trip. The event loop keeps running tasks referenced until
    # they finish; every failure path inside is logged and swallowed.
    asyncio.create_task(_post_event(payload))


async def _post_event(payload: dict[str, Any]) -> None:
    url = f"{_GRAPH_URL}/{_pixel_id()}/events"
    try:
        timeout = aiohttp.ClientTimeout(total=_TIMEOUT_S)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=payload) as resp:
                body = await resp.json(content_type=None)
        if resp.status != 200 or not isinstance(body, dict) or body.get("events_received") != 1:
            logger.warning(
                f"Meta CAPI: unexpected response ({resp.status}): {str(body)[:300]}"
            )
        else:
            logger.info("Meta CAPI: Purchase event accepted")
    except Exception:
        # Never propagate — a failed conversion report must not disturb
        # anything downstream (and by now there IS nothing downstream).
        logger.warning("Meta CAPI: purchase event delivery failed", exc_info=True)
