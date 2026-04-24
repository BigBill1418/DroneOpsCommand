"""Device API key authentication dependency for field controllers."""

import hashlib
import logging
from datetime import datetime

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.device_api_key import DeviceApiKey
from app.services.pushover import send_alert

logger = logging.getLogger("doc.device_auth")


async def validate_device_api_key(
    request: Request,
    x_device_api_key: str = Header(..., description="Device API key from DroneOpsCommand Settings → Device Access"),
    db: AsyncSession = Depends(get_db),
) -> DeviceApiKey:
    """Dependency that validates X-Device-Api-Key header against stored key hashes.

    The raw key is never stored — only its SHA-256 digest.  A 401 is returned for
    any key that is missing, unknown, or has been revoked.

    Logs WARN-level structured events on auth failure so a stale-APK incident
    can be diagnosed from logs alone (see ADR-0002). The raw key is never
    logged — only the first 8 chars of the SHA-256 digest (`key_prefix`).

    ADR-0002 §5 layer 4 — on auth failure we fire a Pushover alert,
    deduped to the first occurrence of a given (key_prefix, ip) pair
    per hour. This catches the "key rotated but device still trying"
    class of drift that the server CAN see (the device did reach the
    backend but with a stale credential).
    """
    key_hash = hashlib.sha256(x_device_api_key.encode()).hexdigest()
    key_prefix = key_hash[:8]

    result = await db.execute(
        select(DeviceApiKey).where(
            DeviceApiKey.key_hash == key_hash,
            DeviceApiKey.is_active.is_(True),
        )
    )
    device_key = result.scalar_one_or_none()

    if not device_key:
        client_ip = request.client.host if request.client else "unknown"
        user_agent = request.headers.get("user-agent", "")[:200]
        logger.warning(
            "device_auth_failed",
            extra={
                "event": "device_auth_failed",
                "key_prefix": key_prefix,
                "ip": client_ip,
                "user_agent": user_agent,
                "path": request.url.path,
            },
        )

        # Fire Pushover alert — first occurrence per (key_prefix, ip) per
        # hour. Best-effort; never blocks the 401 response. We send on
        # DEVICE-key paths only (device-health + device-upload) — other
        # routes that happen to share this dependency should not alert.
        path = request.url.path
        if "/device-" in path:
            await send_alert(
                title=f"DroneOps — stale device key attempt from {client_ip}",
                message=(
                    f"401 on {path} — key prefix {key_prefix} does not "
                    f"match any active device_api_keys row. Likely a "
                    f"controller with a revoked or post-rotation key. "
                    f"UA: {user_agent[:120]}"
                ),
                dedup_key=f"device_auth_failed:{key_prefix}:{client_ip}",
                dedup_ttl_seconds=3600,
            )

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or revoked device API key",
        )

    # Record last-used timestamp (best effort — do not fail the request if this write fails)
    try:
        device_key.last_used_at = datetime.utcnow()
    except Exception:
        pass

    return device_key
