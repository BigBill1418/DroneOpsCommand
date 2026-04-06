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
) -> str:
    """Create a Stripe Checkout session for the given invoice.

    Returns the checkout session URL that the client should redirect to.
    """
    stripe_cfg = await get_stripe_settings(db)
    secret_key = stripe_cfg["stripe_secret_key"]

    if not secret_key:
        logger.error("[STRIPE] No Stripe secret key configured — cannot create checkout session")
        raise ValueError("Stripe is not configured. Set the Stripe Secret Key in Settings.")

    stripe.api_key = secret_key

    # Build line items from invoice line_items
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

    customer_email = customer.email if hasattr(customer, "email") and customer.email else None

    logger.info(
        "[STRIPE] Creating checkout session for invoice=%s, total=%.2f, items=%d, customer_email=%s",
        invoice.id, float(invoice.total), len(checkout_line_items), customer_email,
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
            },
        )
    except stripe.StripeError as exc:
        logger.error("[STRIPE] Failed to create checkout session for invoice=%s: %s", invoice.id, exc)
        raise

    # Store the session ID on the invoice
    invoice.stripe_checkout_session_id = session.id
    logger.info(
        "[STRIPE] Checkout session created: session_id=%s, url=%s",
        session.id, session.url,
    )

    return session.url
