"""Website-lead prefill for missions/customers (ADR-0050).

Proxies the marketing API's DOC routes. Off (404) unless LEADS_API_BASE and
LEADS_API_TOKEN are set — never configured on the demo instance. Logs keys and
outcomes only, never contact details.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import get_current_user
from app.database import get_db
from app.models.customer import Customer
from app.models.user import User
from app.services.lead_source import (
    LEAD_KEY_RE, LeadNotFound, LeadSourceUnavailable, get_client, is_enabled,
)
from app.services.lead_writeback import run_lead_writeback

router = APIRouter(prefix="/api/leads", tags=["leads"])


def _require_enabled() -> None:
    if not is_enabled():
        raise HTTPException(status_code=404, detail="Not found")


def _check_key(key: str) -> None:
    if not LEAD_KEY_RE.match(key):
        raise HTTPException(status_code=404, detail="Lead not found")


@router.get("/status")
async def leads_status(_user: User = Depends(get_current_user)):
    return {"enabled": is_enabled()}


@router.get("", dependencies=[Depends(_require_enabled)])
async def list_leads(
    q: str | None = None,
    include_closed: bool = False,
    _user: User = Depends(get_current_user),
):
    try:
        leads = await get_client().list_leads(q=q, include_closed=include_closed, limit=25)
    except LeadSourceUnavailable:
        raise HTTPException(status_code=503, detail="Leads unavailable")
    return {"leads": leads}


@router.get("/{key}", dependencies=[Depends(_require_enabled)])
async def get_lead(
    key: str,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    _check_key(key)
    try:
        lead = await get_client().get_lead(key)
    except LeadNotFound:
        raise HTTPException(status_code=404, detail="Lead not found")
    except LeadSourceUnavailable:
        raise HTTPException(status_code=503, detail="Leads unavailable")
    email = (lead.get("email") or "").strip().lower()
    match = None
    if email:
        result = await db.execute(
            select(Customer)
            .where(func.lower(Customer.email) == email)
            .order_by(Customer.created_at)
            .limit(1)
        )
        cust = result.scalars().first()
        if cust is not None:
            match = {"id": str(cust.id), "name": cust.name}
    return {"lead": lead, "matching_customer": match}


@router.post("/{key}/mark-won", dependencies=[Depends(_require_enabled)])
async def mark_won(
    key: str,
    mission_id: UUID,
    _user: User = Depends(get_current_user),
):
    _check_key(key)
    if not await run_lead_writeback(mission_id, key):
        raise HTTPException(status_code=502, detail="Lead not marked won")
    return {"ok": True}
