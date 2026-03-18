"""Device API key authentication dependency for field controllers."""

import hashlib
from datetime import datetime

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.device_api_key import DeviceApiKey


async def validate_device_api_key(
    x_device_api_key: str = Header(..., description="Device API key from DroneOpsCommand Settings → Device Access"),
    db: AsyncSession = Depends(get_db),
) -> DeviceApiKey:
    """Dependency that validates X-Device-Api-Key header against stored key hashes.

    The raw key is never stored — only its SHA-256 digest.  A 401 is returned for
    any key that is missing, unknown, or has been revoked.
    """
    key_hash = hashlib.sha256(x_device_api_key.encode()).hexdigest()

    result = await db.execute(
        select(DeviceApiKey).where(
            DeviceApiKey.key_hash == key_hash,
            DeviceApiKey.is_active.is_(True),
        )
    )
    device_key = result.scalar_one_or_none()

    if not device_key:
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
