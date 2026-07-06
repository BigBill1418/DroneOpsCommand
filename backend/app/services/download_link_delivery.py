"""ADR-0040 — automated download-link delivery on payment-in-full.

Companion to the ADR-0039 gate. ADR-0039 WITHHOLDS the mission-footage
download link (report PDF + report email + client portal) while the
invoice is unpaid; this module DELIVERS it automatically the moment the
invoice becomes paid in full — no operator steps:

- Stripe balance webhook marks the invoice paid  → follow-up email fires.
- Operator marks the invoice paid manually       → follow-up email fires.
- Link URL is set/changed on an already-paid
  mission (footage ready after payment)          → follow-up email fires.

`missions.download_link_email_sent_at` is the dedup stamp; the mission
update path resets it when `download_link_url` changes so a replacement
link re-arms delivery. The client portal shows the link whenever
`download_link_payment_blocked()` passes — same policy, same moment.

This module also owns the gate policy itself (`download_link_payment_
blocked`) so the report router, the portal, and the delivery email can
never drift apart. All entry points are fail-soft: a delivery failure is
logged loudly but never breaks the payment flow that triggered it.
"""

from __future__ import annotations

import logging
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

logger = logging.getLogger("doc.download_link_delivery")


# ── ADR-0039 gate policy (single source — reports router + portal import this) ──
def download_link_payment_blocked(mission, report) -> bool:
    """True when the unpaid-invoice gate must withhold the download link.

    `report` may be None (mission without a report row) — the override
    lives on the report, so no report means no override.
    """
    if report is not None and getattr(report, "download_link_payment_override", False):
        return False
    if not mission.is_billable:
        return False
    invoice = mission.invoice
    if invoice is None:
        # Billable but never invoiced — fail closed until an invoice exists
        # and is paid (or the operator overrides).
        return True
    if invoice.paid_in_full:
        return False
    if float(invoice.total or 0) <= 0:
        # Nothing to collect (mirrors the payment-links section logic).
        return False
    return True


def _delivery_skip_reason(mission, invoice) -> str | None:
    """Why the payment-triggered delivery email should NOT fire, or None.

    `invoice` is passed EXPLICITLY (not read via `mission.invoice`): the
    relationship is lazy="noload", so on a mission already in the session's
    identity map (every trigger path — the webhook and both PUT routes load
    the mission/invoice before delivery runs) a re-query with selectinload
    does NOT repopulate it and the gate would mis-read paid missions as
    never-invoiced. Caught by test_download_link_delivery_e2e.
    """
    if not mission.download_link_url:
        return "no-download-url"
    if mission.download_link_email_sent_at is not None:
        return "already-sent"
    if not mission.is_billable:
        # No payment event exists for non-billable missions; the link is
        # already un-gated for them (report + portal carry it).
        return "not-billable"
    if invoice is None or not invoice.paid_in_full:
        return "not-paid-in-full"
    if (
        mission.download_link_expires_at is not None
        and mission.download_link_expires_at < datetime.utcnow()
    ):
        return "link-expired"
    if not mission.customer or not mission.customer.email:
        return "no-customer-email"
    return None


async def deliver_download_link_if_due(
    db: AsyncSession,
    mission_id: UUID | str,
    *,
    trigger: str,
) -> str:
    """Send the download-link email if every condition is met; stamp dedup.

    Returns a status string for logging/tests: "sent" or "skipped:<reason>".
    Never raises past its own boundary — callers sit on payment paths that
    must not fail because an email did.
    """
    from app.models.invoice import Invoice
    from app.models.mission import Mission

    try:
        result = await db.execute(
            select(Mission)
            .where(Mission.id == mission_id)
            .options(selectinload(Mission.customer))
        )
        mission = result.scalar_one_or_none()
        if mission is None:
            logger.warning(
                "[DL-DELIVERY] trigger=%s mission=%s not found — nothing to deliver",
                trigger, mission_id,
            )
            return "skipped:mission-not-found"

        # Direct invoice query — NOT mission.invoice; see _delivery_skip_reason
        # docstring for the lazy="noload" identity-map trap this avoids.
        invoice = (
            await db.execute(select(Invoice).where(Invoice.mission_id == mission.id))
        ).scalar_one_or_none()

        reason = _delivery_skip_reason(mission, invoice)
        if reason is not None:
            level = logging.WARNING if reason in ("link-expired", "no-customer-email") else logging.INFO
            logger.log(
                level,
                "[DL-DELIVERY] trigger=%s mission=%s SKIPPED (%s)",
                trigger, mission_id, reason,
            )
            return f"skipped:{reason}"

        from app.services.email_service import send_download_link_email

        sent = await send_download_link_email(
            to_email=mission.customer.email,
            customer_name=mission.customer.name,
            mission_title=mission.title,
            download_url=mission.download_link_url,
            expires_at=mission.download_link_expires_at,
            db=db,
        )
        if not sent:
            # Graceful no-op path (SMTP unconfigured — demo stacks). Do NOT
            # stamp: the delivery stays armed and fires on the next trigger
            # once SMTP exists, instead of being silently lost forever.
            logger.warning(
                "[DL-DELIVERY] trigger=%s mission=%s SKIPPED (smtp-unconfigured) — "
                "not stamping sent_at; delivery stays armed",
                trigger, mission_id,
            )
            return "skipped:smtp-unconfigured"
        mission.download_link_email_sent_at = datetime.utcnow()
        await db.flush()
        logger.info(
            "[DL-DELIVERY] trigger=%s mission=%s download-link email SENT to %s",
            trigger, mission_id, mission.customer.email,
        )
        return "sent"
    except Exception as exc:  # noqa: BLE001 — never break the payment path
        logger.error(
            "[DL-DELIVERY] trigger=%s mission=%s delivery FAILED: %s "
            "(payment flow unaffected; link remains armed for retry — "
            "sent_at stamp not written)",
            trigger, mission_id, exc, exc_info=True,
        )
        return "skipped:error"
