"""Stripe webhook dispatch/fulfill logic (PAY-001).

CRITICAL — Stripe SDK v15 objects no longer inherit from ``dict``: a bare
``.get()`` call on ANY Stripe object (``Event``, ``Session``,
``Subscription``, ``Invoice``, a line item, a metadata dict pulled off one
of them, ...) raises ``AttributeError`` and crashes this handler. Stripe
retries a failing webhook up to 4 times, then gives up silently — a paying
customer never gets their tier, and nothing else surfaces the failure. This
took down a sister project in production. The rule below is absolute: every
attribute read on a Stripe object uses bracket access (``obj["x"]``) and
``in`` membership checks (``"x" in obj``), NEVER ``.get()``.

``handle_stripe_event`` is the module's one entry point, called from
``routes.py`` after signature verification with either a real
``stripe.Event`` or — in the throwaway verification script — a plain fake
dict of the same shape. Bracket access and ``in`` behave identically on
both, which is what makes this function testable without a real Stripe
signature.
"""

from datetime import datetime, timezone

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import stripe_basic_price_id, stripe_premium_price_id
from database.repository import (
    get_subscription,
    get_subscription_by_stripe_id,
    record_stripe_event,
    seen_stripe_event,
    set_user_tier,
    upsert_subscription,
)

from .stripe_sync import retrieve_checkout_session

# Same content-as-data choice as database.repository._VALID_TIERS: the price
# id -> tier mapping lives in settings, not a schema constraint.
_TIER_PRICE_IDS = {"basic": stripe_basic_price_id, "premium": stripe_premium_price_id}


def _tier_for_price_id(price_id: str | None) -> str | None:
    """Unknown or unconfigured price id -> None. Callers must never guess a
    tier for a price they don't recognize — log a warning and skip instead."""
    if not price_id:
        return None
    for tier, pid in _TIER_PRICE_IDS.items():
        if pid and pid == price_id:
            return tier
    return None


def _ts_to_naive_utc(ts: int) -> datetime:
    # subscriptions.current_period_end is a naive TIMESTAMP column, matching
    # this repo's other naive-UTC columns (e.g. activity_session.started_at).
    return datetime.fromtimestamp(ts, tz=timezone.utc).replace(tzinfo=None)


def _price_id_from_subscription(sub_obj) -> str | None:
    items = sub_obj["items"]["data"] if "items" in sub_obj and sub_obj["items"] else []
    if items and "price" in items[0] and items[0]["price"]:
        return items[0]["price"]["id"]
    return None


def _current_period_end(sub_obj) -> datetime | None:
    # Defensive per the newer API shape: current_period_end moved from the
    # top-level Subscription object onto each subscription item. Try the new
    # shape first, fall back to the older top-level field.
    items = sub_obj["items"]["data"] if "items" in sub_obj and sub_obj["items"] else []
    ts = None
    if items and "current_period_end" in items[0]:
        ts = items[0]["current_period_end"]
    elif "current_period_end" in sub_obj:
        ts = sub_obj["current_period_end"]
    return _ts_to_naive_utc(ts) if ts else None


async def _resolve_subscription_user_id(db: AsyncSession, sub_obj) -> str | None:
    metadata = sub_obj["metadata"] if "metadata" in sub_obj and sub_obj["metadata"] else {}
    user_id = metadata["user_id"] if "user_id" in metadata else None
    if user_id:
        return user_id
    # Fallback: a subscription that never carried our metadata stamp (e.g.
    # created by hand in the Dashboard) — look it up by its own Stripe id
    # against whatever local row we already have for it, if any.
    row = await get_subscription_by_stripe_id(db, stripe_subscription_id=sub_obj["id"])
    return row.user_id if row else None


async def _fulfill_checkout(db: AsyncSession, checkout_session_id: str) -> None:
    """``checkout.session.completed`` / ``.async_payment_succeeded`` (PAY-001 + PAY-002).

    Re-retrieves the session (the webhook payload alone doesn't carry line
    items) and only fulfills once payment has actually landed. A delayed
    method like SEPA fires ``.completed`` with ``payment_status="unpaid"``
    first, then ``.async_payment_succeeded`` later once the debit clears —
    we no-op on the former and let the latter do the work.

    Branches on ``session["mode"]``:
    - ``subscription`` → existing PAY-001 path (upsert_subscription + set_user_tier).
    - ``payment`` → PAY-002 top-up: ``coins.credit(amount=TOPUP_COINS, kind='topup',
      ref=session_id)``.

    Idempotent by construction: ``upsert_subscription`` + ``set_user_tier`` are
    both safe to call twice with the same values; for top-ups the UNIQUE
    ``(user_id, kind, ref)`` + ``ON CONFLICT DO NOTHING`` in ``coins.credit``
    makes a redelivery's credit a no-op without a separate already-seen check.
    """
    session = await retrieve_checkout_session(checkout_session_id, expand=["line_items"])
    if session["payment_status"] != "paid":
        logger.debug(
            f"Stripe checkout {checkout_session_id}: payment_status="
            f"{session['payment_status']!r}, not fulfilling yet"
        )
        return

    # PAY-002: one-time top-up (mode == "payment") vs subscription (mode == "subscription").
    # Must branch on mode BEFORE looking at price_id — the top-up price is not
    # in the subscription price table and would otherwise warn as "unknown price".
    # Stripe object rule: bracket access + ``in`` checks only, never .get() — see
    # this module's top-level docstring. A crash here = Stripe retries 4× then
    # gives up silently while a paying customer never gets coins.
    mode = session["mode"] if "mode" in session else None
    if mode == "payment":
        # Top-up: resolve the user from client_reference_id (belt) or metadata (suspenders),
        # then credit purchased_coins. The UNIQUE (user_id, kind, ref) index
        # is the idempotency key — the existing check→process→record ordering
        # for the webhook means a crash AFTER crediting but BEFORE
        # record_stripe_event would get a redelivery; the ON CONFLICT DO
        # NOTHING inside credit() is what makes the re-credit a no-op (comment
        # this at the credit site — done inside coins/engine.py::credit).
        # Unresolvable user → log error, don't crash (a 500 here retries a
        # permanently failing event forever).
        user_id = session["client_reference_id"] if "client_reference_id" in session else None
        if not user_id:
            metadata = session["metadata"] if "metadata" in session and session["metadata"] else {}
            user_id = metadata["user_id"] if "user_id" in metadata else None
        if not user_id:
            logger.error(
                f"Stripe topup checkout {checkout_session_id}: no client_reference_id or "
                "metadata user_id — cannot credit coins"
            )
            return
        # The credit MUST be idempotent BY ITSELF: the webhook's
        # check->process->record ordering means a crash after credit() but
        # before record_stripe_event() gets a Stripe redelivery — the UNIQUE
        # (user_id, kind, ref) + ON CONFLICT DO NOTHING inside credit() is
        # what makes the re-credit a no-op (see coins/engine.py::credit).
        try:
            from coins.engine import credit as _credit  # local import — coins not available at module load in older deploys

            from coins.prices import TOPUP_COINS as _TOPUP

            credited = await _credit(db, user_id=user_id, amount=_TOPUP, kind="topup", ref=checkout_session_id)
            if credited:
                logger.info(f"Stripe topup credited: user={user_id} session={checkout_session_id} amount={_TOPUP}")
                await db.commit()
            else:
                logger.debug(f"Stripe topup duplicate (already credited): user={user_id} session={checkout_session_id}")
        except Exception as e:
            # A DB error here propagates so Stripe retries — same fail-retry
            # contract as the subscription path (never silently swallow).
            logger.exception(f"Stripe topup credit failed for session {checkout_session_id}: {e}")
            raise
        return

    user_id = session["client_reference_id"]
    if not user_id:
        metadata = session["metadata"] if "metadata" in session and session["metadata"] else {}
        user_id = metadata["user_id"] if "user_id" in metadata else None
    if not user_id:
        logger.warning(
            f"Stripe checkout {checkout_session_id}: no client_reference_id or "
            "metadata user_id — skipping"
        )
        return

    line_items = (
        session["line_items"]["data"]
        if "line_items" in session and session["line_items"]
        else []
    )
    if not line_items:
        logger.warning(f"Stripe checkout {checkout_session_id}: no line items — skipping")
        return
    price_id = line_items[0]["price"]["id"]
    tier = _tier_for_price_id(price_id)
    if tier is None:
        logger.warning(
            f"Stripe checkout {checkout_session_id}: unknown price id {price_id!r} — skipping"
        )
        return

    await upsert_subscription(
        db,
        user_id=user_id,
        stripe_customer_id=session["customer"],
        stripe_subscription_id=session["subscription"] if "subscription" in session else None,
        stripe_price_id=price_id,
        tier=tier,
        status="active",
    )
    await set_user_tier(db, user_id=user_id, tier=tier)


async def _handle_subscription_updated(db: AsyncSession, sub_obj) -> None:
    subscription_id = sub_obj["id"]
    user_id = await _resolve_subscription_user_id(db, sub_obj)
    if user_id is None:
        logger.warning(
            f"Stripe subscription.updated {subscription_id}: could not resolve "
            "user_id — skipping"
        )
        return

    status = sub_obj["status"]
    price_id = _price_id_from_subscription(sub_obj)
    price_tier = _tier_for_price_id(price_id)
    current_period_end = _current_period_end(sub_obj)

    set_tier = True
    if status in ("active", "trialing"):
        # cancel_at_period_end=true also arrives here with status still
        # "active" — the learner keeps access until the later `.deleted`
        # event, which is exactly what taking this branch does (no special
        # case needed: we still grant price_tier).
        if price_tier is None:
            logger.warning(
                f"Stripe subscription {subscription_id}: status={status} but "
                f"unknown price id {price_id!r} — skipping tier change"
            )
            return
        new_tier = price_tier
    elif status == "past_due":
        # Stripe Smart Retries are still attempting to charge the card.
        # Deliberately NOT revoking access on the first failed attempt —
        # only `customer.subscription.deleted` (retries exhausted, or an
        # explicit cancel) drops the tier to free. Leave users.tier alone;
        # still mirror the row's status/price so anything reading the local
        # subscriptions row directly sees the truth.
        existing = await get_subscription(db, user_id=user_id)
        new_tier = existing.tier if existing else (price_tier or "free")
        set_tier = False
    else:  # canceled | unpaid | incomplete_expired | any other terminal status
        new_tier = "free"

    await upsert_subscription(
        db,
        user_id=user_id,
        stripe_customer_id=sub_obj["customer"],
        stripe_subscription_id=subscription_id,
        stripe_price_id=price_id,
        tier=new_tier,
        status=status,
        current_period_end=current_period_end,
    )
    if set_tier:
        await set_user_tier(db, user_id=user_id, tier=new_tier)


async def _handle_subscription_deleted(db: AsyncSession, sub_obj) -> None:
    subscription_id = sub_obj["id"]
    user_id = await _resolve_subscription_user_id(db, sub_obj)
    if user_id is None:
        logger.warning(
            f"Stripe subscription.deleted {subscription_id}: could not resolve "
            "user_id — skipping"
        )
        return

    await upsert_subscription(
        db,
        user_id=user_id,
        stripe_customer_id=sub_obj["customer"],
        stripe_subscription_id=subscription_id,
        stripe_price_id=_price_id_from_subscription(sub_obj),
        tier="free",
        status="canceled",
    )
    await set_user_tier(db, user_id=user_id, tier="free")


async def _handle_invoice_paid(db: AsyncSession, invoice_obj) -> None:
    # On API version 2026-07-29.dahlia the invoice's top-level `subscription`
    # field is gone; the id now lives at
    # parent.subscription_details.subscription. `parent` is null for one-off
    # (non-subscription) invoices, so guard each level with `in` + truthiness
    # before falling back to the legacy top-level field.
    subscription_id = None
    parent = invoice_obj["parent"] if "parent" in invoice_obj else None
    if parent:
        subscription_details = (
            parent["subscription_details"] if "subscription_details" in parent else None
        )
        if subscription_details and "subscription" in subscription_details:
            subscription_id = subscription_details["subscription"]
    if not subscription_id:
        subscription_id = invoice_obj["subscription"] if "subscription" in invoice_obj else None
    if not subscription_id:
        logger.debug(f"Stripe invoice.paid {invoice_obj['id']}: no subscription id — skipping")
        return
    row = await get_subscription_by_stripe_id(db, stripe_subscription_id=subscription_id)
    if row is None:
        logger.warning(
            f"Stripe invoice.paid {invoice_obj['id']}: no local row for subscription "
            f"{subscription_id!r} — skipping"
        )
        return

    lines = invoice_obj["lines"]["data"] if "lines" in invoice_obj and invoice_obj["lines"] else []
    period_end = None
    if lines and "period" in lines[0] and lines[0]["period"] and "end" in lines[0]["period"]:
        period_end = _ts_to_naive_utc(lines[0]["period"]["end"])

    await upsert_subscription(
        db,
        user_id=row.user_id,
        stripe_customer_id=row.stripe_customer_id,
        stripe_subscription_id=subscription_id,
        stripe_price_id=row.stripe_price_id,
        tier=row.tier,
        status="active",
        # Never clobber a good value with None when this invoice's shape
        # doesn't carry a period end.
        current_period_end=period_end if period_end else row.current_period_end,
    )


def _handle_invoice_payment_failed(invoice_obj) -> None:
    customer_id = invoice_obj["customer"] if "customer" in invoice_obj else None
    invoice_id = invoice_obj["id"] if "id" in invoice_obj else None
    # No user-facing behavior yet (PAY-001 scope) — just get it into the logs.
    logger.warning(f"Stripe invoice.payment_failed: customer={customer_id} invoice={invoice_id}")


async def _dispatch(db: AsyncSession, event_type: str, obj) -> None:
    if event_type in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
        await _fulfill_checkout(db, obj["id"])
    elif event_type in ("customer.subscription.updated", "customer.subscription.created"):
        # `.created` covers subscriptions born outside Checkout (Dashboard-
        # created, or API-created), which otherwise get no local row until
        # their first update; harmless duplicate work for the Checkout path
        # since every write here is an idempotent upsert.
        await _handle_subscription_updated(db, obj)
    elif event_type == "customer.subscription.deleted":
        await _handle_subscription_deleted(db, obj)
    elif event_type == "invoice.paid":
        await _handle_invoice_paid(db, obj)
    elif event_type == "invoice.payment_failed":
        _handle_invoice_payment_failed(obj)
    else:
        logger.debug(f"Stripe webhook: unhandled event type {event_type!r}")


async def handle_stripe_event(db: AsyncSession, event) -> None:
    """The webhook's dispatch entry point, called from ``routes.py`` AFTER
    signature verification.

    Idempotency ordering is deliberate — check, then process, then record —
    NOT record-then-process:

    1. SELECT-check whether this event id is already in ``stripe_events``
       (``seen_stripe_event``). If so, this is a Stripe redelivery of an
       event we already fully applied — return immediately, no-op.
    2. Process the event (``_dispatch``). Every write it makes is an
       idempotent upsert (``upsert_subscription``, ``set_user_tier``), so if
       processing raises partway through, we deliberately let the exception
       propagate — ``routes.py`` doesn't catch it, FastAPI turns it into a
       500, and Stripe retries the whole delivery. The retry safely
       reapplies from scratch because every write is idempotent.
    3. Only once processing has fully succeeded do we record the event id.

    Recording first (before processing) would be wrong: a crash between
    "mark seen" and "actually apply the tier change" would permanently lose
    that event — the next redelivery would see it as already-seen and skip
    it, and the customer would keep paying without ever getting their tier.
    Checking first and recording last means a crash anywhere in step 2 is
    always safely retryable, at the cost of (harmless) reprocessing.
    """
    event_id = event["id"]
    event_type = event["type"]

    if await seen_stripe_event(db, event_id=event_id):
        logger.debug(f"Stripe webhook: event {event_id} already processed, skipping")
        return

    await _dispatch(db, event_type, event["data"]["object"])

    # A concurrent duplicate delivery landing here at the same moment just
    # returns False (ON CONFLICT DO NOTHING) — ignore it, not an error: the
    # tier change it raced with was applied by the other delivery either way.
    await record_stripe_event(db, event_id=event_id, event_type=event_type)
