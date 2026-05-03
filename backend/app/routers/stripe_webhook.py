"""Stripe webhook handler — processes payment events from Stripe.

This endpoint has NO JWT auth — it is called directly by Stripe and
verified via the webhook signature.

ADR-0009 — `checkout.session.completed` is now phase-aware. The
session's `metadata.payment_phase` selects the destination columns
(deposit_* vs the existing paid_in_full path) and the notification
shape (deposit-received email vs balance-paid email). Sessions without
the metadata key fall through to the legacy single-payment path so any
checkout session in flight at the v2.65.0 cutover continues to work.
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

# ADR-0009 — single ntfy topic for both deposit + balance events so
# they thread together in the operator's notification client. ADR-0036
# transport. Topic registration in service-registry.json is an
# orchestrator follow-up; the publisher fail-soft path logs and continues
# if the topic does not yet exist.
_NTFY_TOPIC_DEPOSITS = "droneops-deposits"

# Per ADR-0009 §3.5, BCC the operator on every customer receipt so they
# get a confirmation copy without needing to log into the portal.
_OPERATOR_RECEIPT_BCC = "me@barnardhq.com"


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


async def _resolve_payment_method(payment_intent_id: str | None, payment_method_types: list, db: AsyncSession) -> str:
    """Inspect the PaymentIntent to determine card vs ACH.

    Falls back to the session-level `payment_method_types` array if the
    PaymentIntent retrieval fails (e.g., transient Stripe outage).
    """
    payment_method = "stripe_card"
    if not payment_intent_id:
        return payment_method
    try:
        stripe_cfg = await get_stripe_settings(db)
        stripe.api_key = stripe_cfg["stripe_secret_key"]
        pi = stripe.PaymentIntent.retrieve(payment_intent_id)
        if pi.payment_method:
            pm = stripe.PaymentMethod.retrieve(pi.payment_method)
            if pm.type == "us_bank_account":
                return "stripe_ach"
            if pm.type == "card":
                return "stripe_card"
    except Exception as exc:
        logger.warning("[STRIPE-WEBHOOK] Could not retrieve payment method details: %s", exc)
        if "us_bank_account" in (payment_method_types or []):
            return "stripe_ach"
    return payment_method


async def _handle_checkout_completed(session: dict, db: AsyncSession):
    """Process a completed checkout session.

    Phase-aware routing per ADR-0009 §3.5:
      - metadata.payment_phase == "deposit" → deposit_* columns
      - metadata.payment_phase == "balance" → existing paid_in_full path
      - metadata.payment_phase absent       → legacy fall-through

    Idempotency: each branch checks its own *_paid flag and returns
    early so a duplicate webhook delivery never double-fires the
    notifications.
    """
    session_id = session.get("id")
    payment_intent_id = session.get("payment_intent")
    payment_method_types = session.get("payment_method_types", [])
    metadata = session.get("metadata") or {}
    payment_phase = (metadata.get("payment_phase") or "").lower() or None

    logger.info(
        "[STRIPE-WEBHOOK] checkout.session.completed: session_id=%s, payment_intent=%s, phase=%s, methods=%s",
        session_id, payment_intent_id, payment_phase or "<absent/legacy>", payment_method_types,
    )

    if payment_phase == "deposit":
        await _handle_deposit_completed(
            session_id=session_id,
            payment_intent_id=payment_intent_id,
            payment_method_types=payment_method_types,
            db=db,
        )
    else:
        # "balance" OR absent (legacy). Both write to the existing
        # paid_in_full / stripe_checkout_session_id columns.
        await _handle_balance_completed(
            session_id=session_id,
            payment_intent_id=payment_intent_id,
            payment_method_types=payment_method_types,
            phase_for_log=payment_phase or "legacy",
            db=db,
        )


async def _handle_deposit_completed(
    *,
    session_id: str,
    payment_intent_id: str | None,
    payment_method_types: list,
    db: AsyncSession,
):
    """Mark an invoice's deposit as paid + fire deposit notifications."""
    result = await db.execute(
        select(Invoice)
        .options(selectinload(Invoice.line_items))
        .where(Invoice.deposit_checkout_session_id == session_id)
    )
    invoice = result.scalar_one_or_none()

    if not invoice:
        logger.warning("[STRIPE-WEBHOOK] DEPOSIT — no invoice found for deposit session_id=%s", session_id)
        return

    # Idempotency — duplicate webhook delivery is a no-op.
    if invoice.deposit_paid:
        logger.info("[STRIPE-WEBHOOK] DEPOSIT RECEIVED — invoice=%s already marked deposit_paid; skipping", invoice.id)
        return

    payment_method = await _resolve_payment_method(payment_intent_id, payment_method_types, db)

    invoice.deposit_paid = True
    invoice.deposit_paid_at = datetime.utcnow()
    invoice.deposit_payment_method = payment_method
    invoice.deposit_payment_intent_id = payment_intent_id
    await db.commit()

    logger.info(
        "[STRIPE-WEBHOOK] DEPOSIT RECEIVED — invoice=%s, method=%s, payment_intent=%s, amount=%.2f",
        invoice.id, payment_method, payment_intent_id, float(invoice.deposit_amount),
    )

    await _send_deposit_notifications(invoice, db)


async def _handle_balance_completed(
    *,
    session_id: str,
    payment_intent_id: str | None,
    payment_method_types: list,
    phase_for_log: str,
    db: AsyncSession,
):
    """Mark an invoice paid in full + fire balance-paid notifications.

    Handles both `payment_phase=balance` and the legacy no-metadata
    path, which both target the existing `stripe_checkout_session_id`
    column. Existing `paid_in_full` flag provides idempotency.
    """
    result = await db.execute(
        select(Invoice)
        .options(selectinload(Invoice.line_items))
        .where(Invoice.stripe_checkout_session_id == session_id)
    )
    invoice = result.scalar_one_or_none()

    if not invoice:
        logger.warning("[STRIPE-WEBHOOK] BALANCE/%s — no invoice found for session_id=%s", phase_for_log, session_id)
        return

    if invoice.paid_in_full:
        logger.info("[STRIPE-WEBHOOK] BALANCE/%s — invoice=%s already paid_in_full; skipping", phase_for_log, invoice.id)
        return

    payment_method = await _resolve_payment_method(payment_intent_id, payment_method_types, db)

    invoice.paid_in_full = True
    invoice.paid_at = datetime.utcnow()
    invoice.payment_method = payment_method
    invoice.stripe_payment_intent_id = payment_intent_id
    await db.commit()

    logger.info(
        "[STRIPE-WEBHOOK] BALANCE PAID/%s — invoice=%s marked PAID, method=%s, payment_intent=%s, total=%.2f",
        phase_for_log, invoice.id, payment_method, payment_intent_id, float(invoice.total),
    )

    await _send_balance_notifications(invoice, db)


async def _load_mission_and_customer(invoice: Invoice, db: AsyncSession):
    from app.models.mission import Mission
    from app.models.customer import Customer

    mission_result = await db.execute(select(Mission).where(Mission.id == invoice.mission_id))
    mission = mission_result.scalar_one_or_none()
    if not mission or not mission.customer_id:
        return None, None
    cust_result = await db.execute(select(Customer).where(Customer.id == mission.customer_id))
    customer = cust_result.scalar_one_or_none()
    return mission, customer


async def _send_deposit_notifications(invoice: Invoice, db: AsyncSession):
    """ADR-0009 §3.5 deposit branch: ntfy + customer receipt + operator BCC."""
    mission, customer = await _load_mission_and_customer(invoice, db)
    if not mission or not customer or not customer.email:
        logger.warning(
            "[STRIPE-WEBHOOK] DEPOSIT — cannot send notifications, missing mission/customer/email for invoice=%s",
            invoice.id,
        )
        return

    deposit_amount = float(invoice.deposit_amount or 0)

    # ntfy — operator push.
    try:
        from app.services.ntfy import send_alert
        click_url = f"{_frontend_origin()}/missions/{invoice.mission_id}"
        await send_alert(
            title=f"[DroneOps Command] Deposit received — '{mission.title}' — ${deposit_amount:,.2f}",
            message=f"Customer paid the deposit for mission '{mission.title}'. Balance still owed: ${invoice.balance_amount:,.2f}.",
            priority=1,  # ntfy "high" — operator visibility, not crash-the-pager
            topic=_NTFY_TOPIC_DEPOSITS,
            click=click_url,
            tags=("dollar", "deposit"),
            dedup_key=f"deposit-paid:{invoice.id}",
        )
    except Exception as exc:
        # Fail-soft: ntfy module also fail-soft on missing token.
        logger.error("[STRIPE-WEBHOOK] DEPOSIT ntfy failed for invoice=%s: %s", invoice.id, exc)

    # Customer receipt + operator BCC.
    try:
        from app.services.email_service import send_deposit_received_email
        await send_deposit_received_email(
            to_email=customer.email,
            customer_name=customer.name,
            mission_title=mission.title,
            deposit_amount=deposit_amount,
            balance_amount=invoice.balance_amount,
            invoice_total=float(invoice.total),
            payment_method=invoice.deposit_payment_method or "stripe",
            paid_at=invoice.deposit_paid_at,
            bcc_email=_OPERATOR_RECEIPT_BCC,
            db=db,
        )
        logger.info(
            "[STRIPE-WEBHOOK] DEPOSIT email sent to=%s bcc=%s invoice=%s",
            customer.email, _OPERATOR_RECEIPT_BCC, invoice.id,
        )
    except Exception as exc:
        logger.error("[STRIPE-WEBHOOK] DEPOSIT email failed for invoice=%s: %s", invoice.id, exc)


async def _send_balance_notifications(invoice: Invoice, db: AsyncSession):
    """ADR-0009 §3.5 balance branch: ntfy on droneops-deposits + existing receipt email."""
    mission, customer = await _load_mission_and_customer(invoice, db)
    if not mission or not customer or not customer.email:
        logger.warning(
            "[STRIPE-WEBHOOK] BALANCE — cannot send notifications, missing mission/customer/email for invoice=%s",
            invoice.id,
        )
        return

    paid_amount = invoice.balance_amount if invoice.deposit_required else float(invoice.total)

    # ntfy — same topic as deposits so they thread.
    try:
        from app.services.ntfy import send_alert
        click_url = f"{_frontend_origin()}/missions/{invoice.mission_id}"
        await send_alert(
            title=f"[DroneOps Command] Balance paid — '{mission.title}' — ${paid_amount:,.2f}",
            message=f"Mission '{mission.title}' is paid in full (${float(invoice.total):,.2f}).",
            priority=1,
            topic=_NTFY_TOPIC_DEPOSITS,
            click=click_url,
            tags=("dollar", "balance"),
            dedup_key=f"balance-paid:{invoice.id}",
        )
    except Exception as exc:
        logger.error("[STRIPE-WEBHOOK] BALANCE ntfy failed for invoice=%s: %s", invoice.id, exc)

    try:
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
        logger.info(
            "[STRIPE-WEBHOOK] BALANCE email sent to=%s invoice=%s", customer.email, invoice.id,
        )
    except Exception as exc:
        logger.error("[STRIPE-WEBHOOK] BALANCE email failed for invoice=%s: %s", invoice.id, exc)


def _frontend_origin() -> str:
    """Read the operator-side frontend URL once. Defaults to production
    droneops origin so click URLs always point somewhere sane even if
    config drifts."""
    from app.config import settings as _settings
    return (_settings.frontend_url or "https://droneops.barnardhq.com").rstrip("/")
