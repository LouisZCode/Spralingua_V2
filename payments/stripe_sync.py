"""Sync-to-thread wrappers around every Stripe SDK call the payments routes
need (PAY-001). Every Stripe SDK call is a blocking HTTP request under the
hood (there is no async client), so each one is wrapped in ``asyncio.to_thread``
here — kept in one place instead of sprinkled through ``routes.py`` /
``webhook.py``.

``stripe.api_key`` is set once at import time from ``config.settings``. The
app boots fine with it unset — ``is_configured()`` gates every route in
``routes.py`` before a call would ever be attempted, so an unconfigured
deploy never reaches the SDK at all.
"""

import asyncio

import stripe

from config.settings import stripe_secret_key

stripe.api_key = stripe_secret_key


def is_configured() -> bool:
    return bool(stripe_secret_key)


async def find_or_create_customer(*, email: str, name: str | None) -> str:
    """Find a Stripe customer by email, or create one. Returns the customer id."""
    existing = await asyncio.to_thread(stripe.Customer.list, email=email, limit=1)
    if existing["data"]:
        return existing["data"][0]["id"]
    created = await asyncio.to_thread(stripe.Customer.create, email=email, name=name)
    return created["id"]


async def create_checkout_session(**kwargs) -> stripe.checkout.Session:
    return await asyncio.to_thread(stripe.checkout.Session.create, **kwargs)


async def retrieve_checkout_session(session_id: str, **kwargs) -> stripe.checkout.Session:
    return await asyncio.to_thread(stripe.checkout.Session.retrieve, session_id, **kwargs)


async def create_portal_session(**kwargs) -> stripe.billing_portal.Session:
    return await asyncio.to_thread(stripe.billing_portal.Session.create, **kwargs)


def construct_webhook_event(payload: bytes, sig_header: str, secret: str) -> stripe.Event:
    """Signature verification is CPU-bound HMAC work, not network I/O — no
    thread hop needed here, unlike every call above."""
    return stripe.Webhook.construct_event(payload, sig_header, secret)
