"""HTTP routes for Stripe billing (PAY-001): Checkout, the billing portal,
and the webhook.

Every route requires Stripe to be configured (``STRIPE_SECRET_KEY`` set) or
503s "billing not configured" — fail-soft like ``azure_speech_key`` /
``google_client_id`` elsewhere in this repo, so the app boots fine with no
Stripe env at all.

See ``payments/webhook.py`` for the webhook's dispatch/fulfill logic and its
critical "never call .get() on a Stripe object" rule (Stripe SDK v15
``StripeObject`` no longer inherits ``dict`` — see that module's docstring).
"""

from typing import Literal

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request
from loguru import logger
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from auth.deps import get_current_user_id
from config.settings import (
    frontend_base_url,
    stripe_automatic_tax,
    stripe_basic_price_id,
    stripe_premium_price_id,
    stripe_webhook_secret,
)
from database.connection import get_db
from database.orm import User
from database.repository import get_subscription

from .stripe_sync import (
    construct_webhook_event,
    create_checkout_session,
    create_portal_session,
    find_or_create_customer,
    is_configured,
)
from .webhook import handle_stripe_event

router = APIRouter(prefix="/payments", tags=["payments"])

# Our own settings dict, not a Stripe object — plain .get() below is fine.
_TIER_PRICE_IDS: dict[str, str | None] = {
    "basic": stripe_basic_price_id,
    "premium": stripe_premium_price_id,
}


def _require_configured() -> None:
    if not is_configured():
        raise HTTPException(status_code=503, detail="billing not configured")


class CheckoutBody(BaseModel):
    tier: Literal["basic", "premium"]


@router.post("/checkout")
async def create_checkout(
    body: CheckoutBody,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Start a Checkout Session for the requested subscription tier.

    The user id rides in two places on the session — belt and suspenders —
    so a later ``customer.subscription.*`` event can resolve it without a DB
    lookup by customer: ``client_reference_id`` on the Session itself, and
    ``subscription_data.metadata`` so the same id lands directly on the
    Subscription object Stripe creates from it.
    """
    _require_configured()
    price_id = _TIER_PRICE_IDS.get(body.tier)
    if not price_id:
        raise HTTPException(
            status_code=503, detail=f"billing not configured for tier {body.tier!r}"
        )

    user = await db.get(User, user_id)
    # The "demo" sentinel row (anonymous front-page sessions) is never a
    # billable account, and a user with no email can't be matched to (or
    # created as) a Stripe customer.
    if user is None or user_id == "demo":
        raise HTTPException(status_code=400, detail="No billable account for this session.")
    if not user.email:
        raise HTTPException(
            status_code=400, detail="Your account has no email on file — sign in again."
        )

    try:
        customer_id = await find_or_create_customer(email=user.email, name=user.name)

        checkout_kwargs: dict = {
            "mode": "subscription",
            "customer": customer_id,
            "line_items": [{"price": price_id, "quantity": 1}],
            "client_reference_id": user_id,
            "subscription_data": {"metadata": {"user_id": user_id}},
            # {CHECKOUT_SESSION_ID} is a Stripe-side placeholder Stripe fills
            # in on redirect — the doubled braces keep it literal through the
            # f-string.
            "success_url": (
                f"{frontend_base_url}/pricing/success?session_id={{CHECKOUT_SESSION_ID}}"
            ),
            "cancel_url": f"{frontend_base_url}/pricing",
            "locale": "auto",
            # PAY-D4 (2026-08-24): Managed Payments (Stripe as merchant of
            # record) is ON by default for this account and would require
            # eligible tax codes on every product — and its automated-only
            # product rule conflicts with roadmap extras like the human-led
            # weekly call. Explicitly disabled per session so we stay on the
            # decided plan (Stripe Tax Basic + self-filed OSS, own branding).
            # Flip this to revisit merchant-of-record later.
            "managed_payments": {"enabled": False},
        }
        if stripe_automatic_tax:
            checkout_kwargs["automatic_tax"] = {"enabled": True}
        session = await create_checkout_session(**checkout_kwargs)
    except stripe.StripeError:
        logger.exception(
            f"Stripe checkout session creation failed (user {user_id}, tier {body.tier})"
        )
        raise HTTPException(
            status_code=502, detail="Could not start checkout — try again in a moment."
        )

    return {"url": session["url"]}


@router.get("/portal")
async def create_portal(
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Open the Stripe-hosted billing portal for the caller's own subscription."""
    _require_configured()
    subscription = await get_subscription(db, user_id=user_id)
    if subscription is None:
        raise HTTPException(status_code=404, detail="No subscription found.")

    try:
        session = await create_portal_session(
            customer=subscription.stripe_customer_id,
            return_url=f"{frontend_base_url}/practice",
        )
    except stripe.StripeError:
        logger.exception(f"Stripe portal session creation failed (user {user_id})")
        raise HTTPException(
            status_code=502,
            detail="Could not open the billing portal — try again in a moment.",
        )

    return {"url": session["url"]}


@router.post("/webhook")
async def stripe_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """Stripe calls this directly — there is no session JWT here, the
    signature IS the auth.

    Raw body + the ``Stripe-Signature`` header verify the event actually
    came from Stripe before any of it is trusted. See ``payments/webhook.py``
    for what happens once it's verified.
    """
    _require_configured()
    if not stripe_webhook_secret:
        raise HTTPException(status_code=503, detail="billing not configured")

    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")
    try:
        event = construct_webhook_event(payload, sig_header, stripe_webhook_secret)
    except (stripe.SignatureVerificationError, ValueError) as e:
        logger.warning(f"Stripe webhook signature verification failed: {e}")
        raise HTTPException(status_code=400, detail="invalid signature")

    # Deliberately not caught here: a processing failure must surface as a
    # 500 so Stripe retries the delivery — see webhook.py::handle_stripe_event
    # for the full idempotency-ordering rationale (check -> process -> record).
    await handle_stripe_event(db, event)
    return {"ok": True}
