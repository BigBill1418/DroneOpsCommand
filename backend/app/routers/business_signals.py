"""Business-signals endpoint — lightweight aggregate for upstream Innovation Engine.

Consumed by Project J.A.R.V.I.S. (Innovation Engine signals collector). Returns
rolling 30-day and 90-day summary counts that are cheap to compute and stable
to cache. Intentionally does not expose customer-identifying detail.

Auth model: matches the existing router convention — the caller must present
a valid JWT from a Jarvis-tier service account (admin). This avoids a bespoke
service-token plumbing path. Keep this endpoint cheap (one SELECT per metric)
so Jarvis can pull it on its 15-minute innovation loop without cost concern.

Resilience note: every query is wrapped so one-failing-query does not drop
the whole envelope. Missing values surface as `null` (the innovation prompt
treats null as "unknown, not zero").
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import get_current_user
from app.database import get_db
from app.models.customer import Customer
from app.models.invoice import Invoice
from app.models.mission import Mission, MissionStatus
from app.models.user import User

logger = logging.getLogger("doc.business_signals")

router = APIRouter(prefix="/api/v1", tags=["business-signals"])


async def _safe_scalar(db: AsyncSession, stmt, label: str):
    try:
        res = await db.execute(stmt)
        return res.scalar() or 0
    except Exception:  # noqa: BLE001
        logger.exception("business-signals: %s query failed", label)
        return None


@router.get("/business-signals")
async def business_signals(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> dict:
    """Rolling aggregate for the last 30 and 90 days.

    Response shape (every value either int/float or null):

    {
      "window_30d": {
        "missions_completed": int|null,   # COMPLETED|DELIVERED|SENT in window
        "missions_scheduled": int|null,   # SCHEDULED right now (snapshot)
        "flights_flown": int|null,        # MissionFlight rows attached to missions in window
        "invoice_total_usd": float|null,  # sum(invoices.total) where created_at in window
        "invoice_paid_usd":  float|null,  # sum(invoices.total) where paid_at in window
        "new_customers": int|null        # customers.created_at in window
      },
      "window_90d": { same shape },
      "active_now": {
        "missions_in_progress": int|null,
        "missions_review":      int|null
      },
      "generated_at": ISO8601 UTC string
    }
    """
    from app.models.mission import MissionFlight  # lazy import — cheap

    now = datetime.now(timezone.utc)
    d30 = now - timedelta(days=30)
    d90 = now - timedelta(days=90)

    async def _window(start: datetime) -> dict:
        completed_stmt = (
            select(func.count(Mission.id))
            .where(Mission.status.in_((
                MissionStatus.COMPLETED, MissionStatus.DELIVERED, MissionStatus.SENT,
            )))
            .where(Mission.updated_at >= start)
        )
        scheduled_stmt = (
            select(func.count(Mission.id))
            .where(Mission.status == MissionStatus.SCHEDULED)
        )
        flights_stmt = (
            select(func.count(MissionFlight.id))
            .join(Mission, Mission.id == MissionFlight.mission_id)
            .where(Mission.mission_date.is_not(None))
            .where(Mission.mission_date >= start.date())
        )
        invoice_total_stmt = (
            select(func.coalesce(func.sum(Invoice.total), 0))
            .where(Invoice.created_at >= start)
        )
        invoice_paid_stmt = (
            select(func.coalesce(func.sum(Invoice.total), 0))
            .where(Invoice.paid_at.is_not(None))
            .where(Invoice.paid_at >= start)
        )
        new_customers_stmt = (
            select(func.count(Customer.id))
            .where(Customer.created_at >= start)
        )
        return {
            "missions_completed": await _safe_scalar(db, completed_stmt, "missions_completed"),
            "missions_scheduled": await _safe_scalar(db, scheduled_stmt, "missions_scheduled"),
            "flights_flown":      await _safe_scalar(db, flights_stmt, "flights_flown"),
            "invoice_total_usd":  float(await _safe_scalar(db, invoice_total_stmt, "invoice_total") or 0),
            "invoice_paid_usd":   float(await _safe_scalar(db, invoice_paid_stmt, "invoice_paid") or 0),
            "new_customers":      await _safe_scalar(db, new_customers_stmt, "new_customers"),
        }

    in_progress_stmt = (
        select(func.count(Mission.id)).where(Mission.status == MissionStatus.IN_PROGRESS)
    )
    review_stmt = (
        select(func.count(Mission.id)).where(Mission.status == MissionStatus.REVIEW)
    )

    return {
        "window_30d": await _window(d30),
        "window_90d": await _window(d90),
        "active_now": {
            "missions_in_progress": await _safe_scalar(db, in_progress_stmt, "in_progress"),
            "missions_review":      await _safe_scalar(db, review_stmt, "review"),
        },
        "generated_at": now.isoformat(),
    }
