"""Automated payment reminders (dunning).

Two customer touches per unpaid invoice:
  - +48h  -> gentle reminder email
  - +7d   -> firmer final-notice email + operator overdue email to bill@

Core decision logic (`due_stage`, `process_invoice`) is pure and hermetically
tested. `run_dunning_sweep` is the async orchestration run by the daily Celery
beat task. Idempotent: each stage stamps a timestamp and never re-sends.
Design spec: docs/plans/2026-05-24-payment-reminders-dunning.md
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

logger = logging.getLogger("doc.dunning")

REMINDER_AFTER = timedelta(hours=48)
FINAL_AFTER = timedelta(days=7)
OPERATOR_ALERT_EMAIL = "bill@barnardhq.com"

STAGE_REMINDER = "reminder"
STAGE_FINAL = "final"


def due_stage(invoice, now: datetime) -> str | None:
    """Which dunning stage (if any) is due for `invoice` at `now`.

    Returns STAGE_FINAL, STAGE_REMINDER, or None. Pure -- no I/O.
    """
    if invoice.billed_at is None or invoice.paid_in_full:
        return None
    age = now - invoice.billed_at
    # 7-day final notice takes precedence (covers the "reached day 7 without a
    # prior reminder" case -- skip the gentle one and go straight to final).
    if age >= FINAL_AFTER:
        return None if invoice.final_notice_sent_at else STAGE_FINAL
    if age >= REMINDER_AFTER:
        return None if invoice.reminder_sent_at else STAGE_REMINDER
    return None


def amount_due(invoice) -> float:
    """The next amount the portal will charge -- matches the Stripe charge."""
    if getattr(invoice, "deposit_required", False) and not getattr(invoice, "deposit_paid", False):
        return float(invoice.deposit_amount or 0)
    return float(invoice.balance_amount or 0)


async def process_invoice(invoice, now: datetime, sender, *, now_fn=datetime.utcnow) -> str | None:
    """Send the due stage for `invoice` (via `sender`) and stamp it. Pure aside
    from the injected `sender`. Returns the stage sent, or None.

    `sender` must expose async send_reminder(invoice, amount), send_final(invoice, amount),
    send_operator(invoice, amount). `now_fn` supplies the stamp value (injectable
    for tests)."""
    stage = due_stage(invoice, now)
    if stage is None:
        return None
    amt = amount_due(invoice)
    if stage == STAGE_REMINDER:
        await sender.send_reminder(invoice, amt)
        invoice.reminder_sent_at = now_fn()
    elif stage == STAGE_FINAL:
        await sender.send_final(invoice, amt)
        await sender.send_operator(invoice, amt)
        invoice.final_notice_sent_at = now_fn()
    return stage


async def _build_pay_url(db, mission_id) -> str:
    """Customer magic pay-link (reuses the client-link helper). Falls back to the
    plain portal URL if no token can be minted (e.g. no customer)."""
    from app.config import settings
    from app.routers.client_portal import get_or_mint_active_client_link
    minted = await get_or_mint_active_client_link(db, mission_id)
    if minted:
        client_jwt = minted[0]
        return f"{settings.frontend_url.rstrip('/')}/client/{client_jwt}"
    return f"{settings.frontend_url.rstrip('/')}/client/mission/{mission_id}"


class DunningSender:
    """Real email sender. Each method awaits its async email coroutine.
    Constructed with the loaded mission/customer context for one invoice."""
    def __init__(self, db, *, mission, customer, pay_url, mission_url):
        self._db = db
        self.mission, self.customer, self.pay_url, self.mission_url = mission, customer, pay_url, mission_url

    async def send_reminder(self, invoice, amount):
        from app.services.email_service import send_payment_reminder_email
        await send_payment_reminder_email(
            to_email=self.customer.email, customer_name=self.customer.name,
            mission_title=self.mission.title, invoice_number=invoice.invoice_number or "(no number)",
            amount_due=amount, pay_url=self.pay_url, db=self._db)

    async def send_final(self, invoice, amount):
        from app.services.email_service import send_payment_final_notice_email
        await send_payment_final_notice_email(
            to_email=self.customer.email, customer_name=self.customer.name,
            mission_title=self.mission.title, invoice_number=invoice.invoice_number or "(no number)",
            amount_due=amount, pay_url=self.pay_url, db=self._db)

    async def send_operator(self, invoice, amount):
        from app.services.email_service import send_operator_overdue_email
        await send_operator_overdue_email(
            invoice_number=invoice.invoice_number or "(no number)", amount_due=amount,
            customer_name=self.customer.name, customer_email=self.customer.email,
            mission_url=self.mission_url, db=self._db)


async def run_dunning_sweep(db, *, now=None) -> dict:
    """Find billed, unpaid invoices and process the due dunning stage for each.
    Email sends are awaited directly (the caller drives the event loop).
    Per-invoice errors are caught so one failure can't abort the sweep."""
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload
    from app.config import settings
    from app.models.invoice import Invoice
    from app.models.mission import Mission
    from app.models.customer import Customer

    now = now or datetime.utcnow()
    summary = {"reminders": 0, "finals": 0, "skipped_no_email": 0, "errors": 0}

    result = await db.execute(
        select(Invoice).where(Invoice.billed_at.is_not(None), Invoice.paid_in_full.is_(False))
        .options(selectinload(Invoice.line_items))
    )
    for invoice in result.scalars().all():
        try:
            stage = due_stage(invoice, now)
            if stage is None:
                continue
            mission = (await db.execute(select(Mission).where(Mission.id == invoice.mission_id))).scalar_one_or_none()
            if mission is None or getattr(mission.status, "value", mission.status) == "cancelled":
                continue
            customer = None
            if mission.customer_id:
                customer = (await db.execute(select(Customer).where(Customer.id == mission.customer_id))).scalar_one_or_none()
            if customer is None or not customer.email:
                # Can't email the customer; still alert the operator at the final stage.
                summary["skipped_no_email"] += 1
                if stage == STAGE_FINAL:
                    from app.services.email_service import send_operator_overdue_email
                    await send_operator_overdue_email(
                        invoice_number=invoice.invoice_number or "(no number)",
                        amount_due=amount_due(invoice),
                        customer_name=(customer.name if customer else "(unknown)"),
                        customer_email=(customer.email if customer else None),
                        mission_url=f"{settings.frontend_url.rstrip('/')}/missions/{invoice.mission_id}", db=db)
                    invoice.final_notice_sent_at = now
                    await db.commit()
                continue
            pay_url = await _build_pay_url(db, invoice.mission_id)
            mission_url = f"{settings.frontend_url.rstrip('/')}/missions/{invoice.mission_id}"
            sender = DunningSender(db, mission=mission, customer=customer, pay_url=pay_url, mission_url=mission_url)
            sent = await process_invoice(invoice, now, sender, now_fn=lambda: now)
            await db.commit()
            if sent == STAGE_REMINDER:
                summary["reminders"] += 1
            elif sent == STAGE_FINAL:
                summary["finals"] += 1
        except Exception as exc:  # one bad invoice must not abort the sweep
            await db.rollback()
            summary["errors"] += 1
            logger.error("[DUNNING] invoice=%s failed: %s", getattr(invoice, "id", "?"), exc, exc_info=True)
    logger.info("[DUNNING] sweep complete: %s", summary)
    return summary
