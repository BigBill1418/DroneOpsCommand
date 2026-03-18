"""Device API key management — Settings → Device Access.

Allows admins to create, list, and revoke static API keys used by field
controllers (DroneOpsSync) to upload flight logs without a user login.
"""

import hashlib
import secrets
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import get_current_user
from app.database import get_db
from app.models.device_api_key import DeviceApiKey
from app.models.user import User

router = APIRouter(prefix="/api/settings/device-keys", tags=["settings"])


class DeviceKeyResponse(BaseModel):
    id: uuid.UUID
    label: str
    is_active: bool
    created_at: datetime
    last_used_at: datetime | None

    model_config = {"from_attributes": True}


class DeviceKeyCreateResponse(DeviceKeyResponse):
    """Returned ONCE at creation — includes the raw key which is never stored."""
    raw_key: str


class DeviceKeyCreate(BaseModel):
    label: str


@router.get("", response_model=list[DeviceKeyResponse])
async def list_device_keys(
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all device API keys (raw keys are never returned after creation)."""
    result = await db.execute(
        select(DeviceApiKey).order_by(DeviceApiKey.created_at.desc())
    )
    return result.scalars().all()


@router.post("", response_model=DeviceKeyCreateResponse, status_code=201)
async def create_device_key(
    payload: DeviceKeyCreate,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new device API key.

    The raw key is returned exactly ONCE in this response and is never stored.
    Copy it to your DroneOpsSync controller immediately.
    """
    raw_key = secrets.token_urlsafe(32)
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

    device_key = DeviceApiKey(
        label=payload.label.strip() or "Unnamed Device",
        key_hash=key_hash,
    )
    db.add(device_key)
    await db.flush()
    await db.refresh(device_key)
    await db.commit()

    return DeviceKeyCreateResponse(
        id=device_key.id,
        label=device_key.label,
        is_active=device_key.is_active,
        created_at=device_key.created_at,
        last_used_at=device_key.last_used_at,
        raw_key=raw_key,
    )


@router.delete("/{key_id}", status_code=204)
async def revoke_device_key(
    key_id: uuid.UUID,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Revoke (permanently delete) a device API key.

    Any controller using this key will immediately lose upload access.
    """
    result = await db.execute(
        select(DeviceApiKey).where(DeviceApiKey.id == key_id)
    )
    device_key = result.scalar_one_or_none()
    if not device_key:
        raise HTTPException(status_code=404, detail="Device key not found")

    await db.delete(device_key)
    await db.commit()
