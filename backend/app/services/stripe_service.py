"""Stripe Checkout integration — creates hosted checkout sessions for invoice payment."""

import logging

import stripe
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.system_settings import SystemSetting

logger = logging.getLogger("doc.stripe")

STRIPE_KEYS = [
    "stripe_secret_key",
    "stripe_webhook_secret",
    "stripe_publishable_key",
]


async def get_stripe_settings(db: AsyncSession) -> dict:
    """Load Stripe settings from DB, falling back to env-based config."""
    result = await db.execute(
        select(SystemSetting).where(SystemSetting.key.in_(STRIPE_KEYS))
    )
    db_settings = {r.key: r.value for r in result.scalars().all()}

    return {
        "stripe_secret_key": db_settings.get("stripe_secret_key") or settings.stripe_secret_key,
        "stripe_webhook_secret": db_settings.get("stripe_webhook_secret") or settings.stripe_webhook_secret,
        "stripe_publishable_key": db_settings.get("stripe_publishable_key") or settings.stripe_publishable_key,
    }


async def create_checkout_session(
    invoice,
    customer,
    success_url: str,
    cancel_url: str,
    db: AsyncSession,
    *,
    payment_phase: str = "balance",
    amount_override: float | None = None,
    description_override: str | None = None,
) -> str:
    """Create a Stripe Checkout session for the given invoice.

    ADR-0009 — `payment_phase` ∈ {"deposit", "balance", "legacy"}.
    Stamped onto session.metadata so the webhook handler can route the
    completion event to the right invoice column. "legacy" is the
    pre-deposit-feature shape and behaves exactly as before.

    When `amount_override` is provided the session bills a single
    consolidated line item for that amount (typical: deposit = 50% of
    total; balance = total - deposit). When None we fall back to
    itemized line-by-line billing for the entire invoice (legacy path).

    Returns the checkout session URL that the client should redirect to.
    """
    stripe_cfg = await get_stripe_settings(db)
    secret_key = stripe_cfg["stripe_secret_key"]

    if not secret_key:
        logger.error("[STRIPE] No Stripe secret key configured — cannot create checkout session")
        raise ValueError("Stripe is not configured. Set the Stripe Secret Key in Settings.")

    stripe.api_key = secret_key

    if amount_override is not None:
        # Single consolidated line item for the deposit or balance.
        # Stripe Checkout doesn't accept partial-payment of an itemized
        # session, so the cleanest path is one synthetic line. The
        # invoice's full breakdown lives in our DB; the customer sees
        # a meaningful description on the Stripe page.
        amount_cents = int(round(float(amount_override) * 100))
        if amount_cents <= 0:
            raise ValueError(f"amount_override must be > 0 (got {amount_override})")
        default_desc = {
            "deposit": f"Deposit — Invoice {invoice.id}",
            "balance": f"Balance — Invoice {invoice.id}",
        }.get(payment_phase, f"Invoice {invoice.id}")
        checkout_line_items = [{
            "price_data": {
                "currency": "usd",
                "product_data": {
                    "name": (description_override or default_desc)[:200],
                },
                "unit_amount": amount_cents,
            },
            "quantity": 1,
        }]
        billed_total = float(amount_override)
    else:
        # Legacy itemized path — back-compat for any caller that still
        # passes the whole invoice.
        checkout_line_items = []
        for item in invoice.line_items:
            unit_amount = int(round(float(item.unit_price) * 100))  # Stripe uses cents
            quantity = max(1, int(item.quantity)) if float(item.quantity) == int(item.quantity) else 1
            # If quantity is fractional, fold into unit_amount
            if float(item.quantity) != int(item.quantity):
                unit_amount = int(round(float(item.total) * 100))
                quantity = 1

            checkout_line_items.append({
                "price_data": {
                    "currency": "usd",
                    "product_data": {
                        "name": item.description[:200],
                    },
                    "unit_amount": unit_amount,
                },
                "quantity": quantity,
            })

        # Add tax as a separate line item if present
        tax_amount = float(invoice.tax_amount) if invoice.tax_amount else 0
        if tax_amount > 0:
            checkout_line_items.append({
                "price_data": {
                    "currency": "usd",
                    "product_data": {"name": f"Tax ({float(invoice.tax_rate) * 100:.2f}%)"},
                    "unit_amount": int(round(tax_amount * 100)),
                },
                "quantity": 1,
            })
        billed_total = float(invoice.total)

    customer_email = customer.email if hasattr(customer, "email") and customer.email else None

    logger.info(
        "[STRIPE] Creating checkout session for invoice=%s, phase=%s, billed=%.2f, items=%d, customer_email=%s",
        invoice.id, payment_phase, billed_total, len(checkout_line_items), customer_email,
    )

    try:
        session = stripe.checkout.Session.create(
            payment_method_types=["card", "us_bank_account"],
            line_items=checkout_line_items,
            mode="payment",
            success_url=success_url,
            cancel_url=cancel_url,
            customer_email=customer_email,
            metadata={
                "invoice_id": str(invoice.id),
                "mission_id": str(invoice.mission_id),
                # ADR-0009 — webhook handler keys off this. Absent =
                # legacy path (existing checkout sessions in flight at
                # cutover continue to work).
                "payment_phase": payment_phase,
            },
        )
    except stripe.StripeError as exc:
        logger.error("[STRIPE] Failed to create checkout session for invoice=%s: %s", invoice.id, exc)
        raise

    # Store the session ID on the invoice — deposit gets its own column
    # so the two phases never overwrite each other.
    if payment_phase == "deposit":
        invoice.deposit_checkout_session_id = session.id
    else:
        invoice.stripe_checkout_session_id = session.id
    logger.info(
        "[STRIPE] Checkout session created: session_id=%s, phase=%s, url=%s",
        session.id, payment_phase, session.url,
    )

    return session.url
