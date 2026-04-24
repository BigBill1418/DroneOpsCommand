"""Admin endpoint — zero-touch device API key rotation (ADR-0003).

Generates a new raw device key, marks the existing row with the SHA-256 of
that new key in ``rotated_to_key_hash`` plus a 24h grace window in
``rotation_grace_until``, and stashes the raw key in Redis under
``doc:rotation:hint:{device_id}`` (TTL = grace window) so the device-health
endpoint can return it ONCE to the OLD-key-authenticated request.

After the grace window, ``finalize_key_rotations_task`` (Celery beat,
every 15 minutes) promotes ``rotated_to_key_hash`` → ``key_hash`` and
clears the grace columns.

Auth: same admin gate the existing ``/api/settings/device-keys`` endpoints
use — ``Depends(get_current_user)``. The project does not have role
distinctions today; ADR-0003 §6 flags that as a follow-up RBAC item.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import get_current_user
from app.database import get_db
from app.models.device_api_key import DeviceApiKey
from app.models.user import User
from app.services.pushover import send_alert
from app.services.rotation_hint import (
    set_rotation_hint,
    RotationHintBackendUnavailable,
)

logger = logging.getLogger("doc.device_rotation")

router = APIRouter(prefix="/api/admin/devices", tags=["admin"])

# Grace window: 24h is "long enough for a cold device to cycle through one
# operational day, short enough to contain compromise". See ADR-0003 §3.4.
GRACE_HOURS = 24


class RotateKeyResponse(BaseModel):
    """Returned ONCE — `raw_key` is never returned again."""

    id: uuid.UUID
    label: str
    raw_key: str
    rotation_grace_until: datetime


@router.post(
    "/{device_id}/rotate-key",
    response_model=RotateKeyResponse,
    status_code=200,
)
async def rotate_device_key(
    device_id: uuid.UUID,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RotateKeyResponse:
    """Begin a zero-touch device API key rotation.

    Returns the new raw key exactly once. The OLD key continues to
    authenticate for ``GRACE_HOURS`` hours; during that window the
    device-health endpoint includes the new raw key in its response so
    the paired controller can self-update without operator interaction
    on the device.
    """
    logger.info(
        "rotate_key_start",
        extra={"event": "rotate_key_start", "device_id": str(device_id)},
    )

    result = await db.execute(
        select(DeviceApiKey).where(DeviceApiKey.id == device_id)
    )
    device = result.scalar_one_or_none()
    if device is None:
        logger.warning(
            "rotate_key_not_found",
            extra={"event": "rotate_key_not_found", "device_id": str(device_id)},
        )
        raise HTTPException(status_code=404, detail="Device key not found")

    now = datetime.utcnow()
    if (
        device.rotation_grace_until is not None
        and device.rotation_grace_until > now
    ):
        logger.warning(
            "rotate_key_already_in_flight",
            extra={
                "event": "rotate_key_already_in_flight",
                "device_id": str(device_id),
                "grace_until": device.rotation_grace_until.isoformat() + "Z",
            },
        )
        raise HTTPException(
            status_code=409,
            detail=(
                "Rotation already in flight for this device. Wait for the "
                "grace window to expire or revoke + recreate the key."
            ),
        )

    new_raw_key = secrets.token_urlsafe(32)
    new_hash = hashlib.sha256(new_raw_key.encode()).hexdigest()
    grace_until = now + timedelta(hours=GRACE_HOURS)

    # Stash the raw key in Redis FIRST. If that fails we never write the
    # DB row — keeping the system in a consistent "no rotation in flight"
    # state. ADR-0003 §3.4 explicitly fail-closes on Redis outage because
    # the device cannot pick up the new key without the hint.
    try:
        await set_rotation_hint(
            device_id=str(device_id),
            raw_key=new_raw_key,
            ttl_seconds=GRACE_HOURS * 3600,
        )
    except RotationHintBackendUnavailable as exc:
        logger.error(
            "rotate_key_redis_unavailable",
            extra={
                "event": "rotate_key_redis_unavailable",
                "device_id": str(device_id),
                "error": str(exc),
            },
        )
        raise HTTPException(
            status_code=503,
            detail=(
                "Rotation hint backend (Redis) unreachable; rotation aborted "
                "to avoid leaving the device unable to discover the new key."
            ),
        ) from exc

    device.rotated_to_key_hash = new_hash
    device.rotation_grace_until = grace_until
    await db.commit()

    logger.info(
        "rotate_key_success",
        extra={
            "event": "rotate_key_success",
            "device_id": str(device_id),
            "device_label": device.label,
            "new_key_prefix": new_hash[:8],
            "grace_until": grace_until.isoformat() + "Z",
        },
    )

    # Best-effort Pushover FYI. Never blocks the response; rotation is
    # already committed in the DB at this point.
    try:
        await send_alert(
            title="DroneOps key rotated",
            message=(
                f"Rotated device key for {device.label}. "
                f"Grace ends {grace_until.isoformat()}Z. "
                "Controllers will pick up the new key on next sync — "
                "no action needed."
            ),
            priority=0,
        )
    except Exception as exc:  # pragma: no cover — best-effort
        logger.warning(
            "rotate_key_pushover_failed",
            extra={
                "event": "rotate_key_pushover_failed",
                "device_id": str(device_id),
                "error": str(exc),
            },
        )

    return RotateKeyResponse(
        id=device.id,
        label=device.label,
        raw_key=new_raw_key,
        rotation_grace_until=grace_until,
    )
