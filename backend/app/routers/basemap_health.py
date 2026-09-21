"""Admin surface for the basemap tile-health probe (ADR-0046).

Three routes, all behind the same admin gate the rest of the admin surface
uses (``Depends(get_current_user)`` — this project has no role distinctions
yet; ADR-0003 §6 tracks that as the follow-up RBAC item):

- ``GET  /api/admin/basemap/tile-health``      — the last persisted result
- ``POST /api/admin/basemap/tile-health/run``  — run the probe now
- ``PUT  /api/admin/basemap/tile-health/ntfy`` — arm/disarm the ntfy publish

The weekly Celery beat task writes the same ``system_settings`` row, so the GET
reflects whichever ran last.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.auth.jwt import get_current_user
from app.database import get_db
from app.models.system_settings import SystemSetting
from app.models.user import User
from app.services.basemap_probe import (
    SETTING_LAST_RESULT,
    SETTING_NTFY_ENABLED,
    load_baseline,
    ntfy_enabled,
    run_probe,
)

logger = logging.getLogger("doc.basemap_health")

router = APIRouter(prefix="/api/admin/basemap", tags=["admin"])

#: Minimum gap between manual runs. Each run costs one tile request at every
#: provider, and the OSMF Tile Usage Policy is explicit that automated,
#: repeated fetching is what gets an app blocked. A button someone can hold
#: down is exactly that.
MANUAL_RUN_COOLDOWN_SECONDS = 60


class NtfyToggle(BaseModel):
    enabled: bool


async def _read_setting(db: AsyncSession, key: str) -> str | None:
    result = await db.execute(select(SystemSetting).where(SystemSetting.key == key))
    row = result.scalar_one_or_none()
    return row.value if row else None


async def _write_setting(db: AsyncSession, key: str, value: str) -> None:
    result = await db.execute(select(SystemSetting).where(SystemSetting.key == key))
    row = result.scalar_one_or_none()
    if row is None:
        db.add(SystemSetting(key=key, value=value))
    else:
        row.value = value
    await db.commit()


def _parse_result(raw: str | None) -> dict | None:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        logger.error("basemap_health.last_result_unparseable len=%d", len(raw))
        return None


@router.get("/tile-health")
async def get_tile_health(
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Last probe result, the baseline it was graded against, and the alert gate.

    ``last_result`` is ``null`` until the probe has run at least once — which
    is a different state from "the probe ran and everything is fine", and the
    caller can tell them apart.
    """
    last = _parse_result(await _read_setting(db, SETTING_LAST_RESULT))
    baseline = load_baseline()
    return {
        "last_result": last,
        "ntfy_enabled": ntfy_enabled(await _read_setting(db, SETTING_NTFY_ENABLED)),
        "baseline_captured_at": baseline.get("captured_at"),
        "baseline_layers": sorted(baseline.get("layers", {})),
    }


@router.post("/tile-health/run")
async def run_tile_health(
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Run the probe now and persist the result. Never publishes to ntfy.

    A hand-run is a diagnostic, so it deliberately does not take the alert
    path — arming the publish is the scheduled task's job, and a manual run
    should not be able to burn the 24h cooldown.
    """
    previous = _parse_result(await _read_setting(db, SETTING_LAST_RESULT))
    if previous:
        try:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(previous["checked_at"])).total_seconds()
        except (KeyError, TypeError, ValueError):
            age = MANUAL_RUN_COOLDOWN_SECONDS
        if age < MANUAL_RUN_COOLDOWN_SECONDS:
            raise HTTPException(
                status_code=429,
                detail=f"Probe ran {int(age)}s ago; wait {MANUAL_RUN_COOLDOWN_SECONDS}s between manual runs.",
            )

    logger.info("basemap_health.manual_run_start")
    # run_probe() does blocking HTTP — keep it off the event loop.
    result = await run_in_threadpool(run_probe)
    await _write_setting(db, SETTING_LAST_RESULT, json.dumps(result))
    logger.info(
        "basemap_health.manual_run_done",
        extra={
            "event": "basemap_health.manual_run_done",
            "ok": result["ok"],
            "layers_ok": result["layers_ok"],
            "failing": result["failing"],
        },
    )
    return result


@router.put("/tile-health/ntfy")
async def set_tile_health_ntfy(
    payload: NtfyToggle,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Arm or disarm the probe's ntfy publish.

    Ships disarmed. ROADMAP MP-2: arm it only after at least two weeks of
    observe-only data has shown what the providers' own refresh cadence does
    to the hash distances — earliest 2026-10-05.
    """
    await _write_setting(db, SETTING_NTFY_ENABLED, "true" if payload.enabled else "false")
    logger.info(
        "basemap_health.ntfy_gate_set",
        extra={"event": "basemap_health.ntfy_gate_set", "enabled": payload.enabled},
    )
    return {"ntfy_enabled": payload.enabled}
