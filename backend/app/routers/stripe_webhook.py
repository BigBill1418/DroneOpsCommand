"""Stripe webhook handler — processes payment events from Stripe.

This endpoint has NO JWT auth — it is called directly by Stripe and
verified via the webhook signature.
"""

import logging
from datetime import datetime

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.invoice import Invoice
from app.services.stripe_service import get_stripe_settings

logger = logging.getLogger("doc.stripe")

router = APIRouter(tags=["stripe_webhook"])


@router.post("/api/webhooks/stripe")
async def stripe_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Handle Stripe webhook events. No auth — verified by Stripe signature."""
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    stripe_cfg = await get_stripe_settings(db)
    webhook_secret = stripe_cfg["stripe_webhook_secret"]

    if not webhook_secret:
        logger.error("[STRIPE-WEBHOOK] No webhook secret configured — cannot verify signature")
        raise HTTPException(status_code=500, detail="Stripe webhook not configured")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
    except ValueError:
        logger.warning("[STRIPE-WEBHOOK] Invalid payload received")
        raise HTTPException(status_code=400, detail="Invalid payload")
    except stripe.SignatureVerificationError:
        logger.warning("[STRIPE-WEBHOOK] Signature verification failed — sig=%s", sig_header[:20] if sig_header else "none")
        raise HTTPException(status_code=400, detail="Invalid signature")

    event_type = event["type"]
    logger.info("[STRIPE-WEBHOOK] Received event type=%s, id=%s", event_type, event["id"])

    if event_type == "checkout.session.completed":
        await _handle_checkout_completed(event["data"]["object"], db)
    else:
        logger.info("[STRIPE-WEBHOOK] Ignoring event type=%s", event_type)

    return {"status": "ok"}


async def _handle_checkout_completed(session: dict, db: AsyncSession):
    """Process a completed checkout session — mark invoice as paid."""
    session_id = session.get("id")
    payment_intent_id = session.get("payment_intent")
    payment_method_types = session.get("payment_method_types", [])

    logger.info(
        "[STRIPE-WEBHOOK] checkout.session.completed: session_id=%s, payment_intent=%s, methods=%s",
        session_id, payment_intent_id, payment_method_types,
    )

    # Look up invoice by checkout session ID
    result = await db.execute(
        select(Invoice)
        .options(selectinload(Invoice.line_items))
        .where(Invoice.stripe_checkout_session_id == session_id)
    )
    invoice = result.scalar_one_or_none()

    if not invoice:
        logger.warning("[STRIPE-WEBHOOK] No invoice found for session_id=%s", session_id)
        return

    if invoice.paid_in_full:
        logger.info("[STRIPE-WEBHOOK] Invoice %s already marked paid — skipping", invoice.id)
        return

    # Determine payment method — try to inspect the actual PaymentIntent for precision
    payment_method = "stripe_card"
    if payment_intent_id:
        try:
            stripe_cfg = await get_stripe_settings(db)
            stripe.api_key = stripe_cfg["stripe_secret_key"]
            pi = stripe.PaymentIntent.retrieve(payment_intent_id)
            if pi.payment_method:
                pm = stripe.PaymentMethod.retrieve(pi.payment_method)
                if pm.type == "us_bank_account":
                    payment_method = "stripe_ach"
                elif pm.type == "card":
                    payment_method = "stripe_card"
        except Exception as exc:
            logger.warning("[STRIPE-WEBHOOK] Could not retrieve payment method details: %s", exc)
            # Fall back to checking session-level payment_method_types
            if "us_bank_account" in payment_method_types:
                payment_method = "stripe_ach"

    invoice.paid_in_full = True
    invoice.paid_at = datetime.utcnow()
    invoice.payment_method = payment_method
    invoice.stripe_payment_intent_id = payment_intent_id

    await db.commit()

    logger.info(
        "[STRIPE-WEBHOOK] Invoice %s marked PAID — method=%s, payment_intent=%s, total=%.2f",
        invoice.id, payment_method, payment_intent_id, float(invoice.total),
    )

    # Send payment confirmation email
    await _send_payment_notification(invoice, db)


async def _send_payment_notification(invoice: Invoice, db: AsyncSession):
    """Send payment confirmation email to the customer and log for the operator."""
    try:
        from app.models.mission import Mission
        from app.models.customer import Customer

        result = await db.execute(select(Mission).where(Mission.id == invoice.mission_id))
        mission = result.scalar_one_or_none()
        if not mission or not mission.customer_id:
            logger.warning("[STRIPE-WEBHOOK] Cannot send payment email — no mission/customer for invoice %s", invoice.id)
            return

        cust_result = await db.execute(select(Customer).where(Customer.id == mission.customer_id))
        customer = cust_result.scalar_one_or_none()
        if not customer or not customer.email:
            logger.warning("[STRIPE-WEBHOOK] Cannot send payment email — no customer email for invoice %s", invoice.id)
            return

        from app.services.email_service import send_payment_received_email
        await send_payment_received_email(
            to_email=customer.email,
            customer_name=customer.name,
            mission_title=mission.title,
            invoice_total=float(invoice.total),
            payment_method=invoice.payment_method or "stripe",
            paid_at=invoice.paid_at,
            db=db,
        )
        logger.info("[STRIPE-WEBHOOK] Payment confirmation email sent to %s for invoice %s", customer.email, invoice.id)
    except Exception as exc:
        # Email failure should not break the webhook — payment is already recorded
        logger.error("[STRIPE-WEBHOOK] Failed to send payment confirmation email for invoice %s: %s", invoice.id, exc)
