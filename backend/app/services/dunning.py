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


def process_invoice(invoice, now: datetime, sender, *, now_fn=datetime.utcnow) -> str | None:
    """Send the due stage for `invoice` (via `sender`) and stamp it. Pure aside
    from the injected `sender`. Returns the stage sent, or None.

    `sender` must expose send_reminder(invoice, amount), send_final(invoice, amount),
    send_operator(invoice, amount). `now_fn` supplies the stamp value (injectable
    for tests)."""
    stage = due_stage(invoice, now)
    if stage is None:
        return None
    amt = amount_due(invoice)
    if stage == STAGE_REMINDER:
        sender.send_reminder(invoice, amt)
        invoice.reminder_sent_at = now_fn()
    elif stage == STAGE_FINAL:
        sender.send_final(invoice, amt)
        sender.send_operator(invoice, amt)
        invoice.final_notice_sent_at = now_fn()
    return stage
