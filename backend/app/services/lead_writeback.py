"""Mark a website lead `won` after a mission is created from it (ADR-0050).

Runs AFTER the mission's transaction commits (FastAPI BackgroundTasks, or the
manual retry route). Never raises: a failure leaves missions.lead_writeback_at
NULL, which the mission Hub shows as "Lead not marked won — Retry".
"""
from __future__ import annotations

import logging
from datetime import datetime
from uuid import UUID

from sqlalchemy import update

from app.database import async_session
from app.models.mission import Mission
from app.services.lead_source import (
    LEAD_KEY_RE, LeadSourceClient, get_client, is_enabled,
)

logger = logging.getLogger("doc.leads")


def should_write_back(source_ref: str | None) -> bool:
    return bool(source_ref) and bool(LEAD_KEY_RE.match(source_ref)) and is_enabled()


async def run_lead_writeback(
    mission_id: UUID,
    lead_key: str,
    *,
    client: LeadSourceClient | None = None,
    session_factory=async_session,
) -> bool:
    if client is None:
        if not is_enabled():
            return False
        client = get_client()
    try:
        await client.mark_won(lead_key, str(mission_id))
    except Exception as exc:  # never raise (LD-2 M-4) — includes LeadSourceUnavailable/LeadNotFound
        logger.warning("[LEAD-WRITEBACK] mission=%s lead=%s failed: %s", mission_id, lead_key, type(exc).__name__)
        return False
    try:
        async with session_factory() as session:
            result = await session.execute(
                update(Mission).where(Mission.id == mission_id).values(lead_writeback_at=datetime.utcnow())
            )
            await session.commit()
    except Exception as exc:
        logger.warning("[LEAD-WRITEBACK] mission=%s lead=%s marked won but stamp failed: %s",
                       mission_id, lead_key, type(exc).__name__)
        return False
    if not getattr(result, "rowcount", 1):
        logger.warning("[LEAD-WRITEBACK] mission=%s lead=%s marked won but no mission row", mission_id, lead_key)
        return False
    logger.info("[LEAD-WRITEBACK] mission=%s lead=%s marked won", mission_id, lead_key)
    return True
