"""Stripe billing (PAY-001) — Checkout, the billing portal, and the webhook
that keeps ``users.tier`` and the local ``subscriptions`` mirror in sync with
Stripe.

Every route here is fail-soft (``payments/stripe_sync.py::is_configured``):
absent ``STRIPE_SECRET_KEY`` means the app boots fine but every endpoint
503s "billing not configured", same contract as ``azure_speech_key`` /
``google_client_id`` elsewhere in this repo.

See ``payments/webhook.py`` for the webhook's dispatch/fulfill logic and its
critical "never call .get() on a Stripe object" rule (Stripe SDK v15
StripeObject no longer inherits dict).
"""

from .routes import router

__all__ = ["router"]
