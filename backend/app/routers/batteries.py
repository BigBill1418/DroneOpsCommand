"""Battery health tracking API."""

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import get_current_user
from app.database import get_db
from app.models.battery import Battery, BatteryLog
from app.models.user import User
from app.schemas.flight import BatteryCreate, BatteryLogResponse, BatteryResponse, BatteryUpdate

logger = logging.getLogger("doc.batteries")

router = APIRouter(prefix="/api/batteries", tags=["batteries"])


@router.get("", response_model=list[BatteryResponse])
async def list_batteries(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    result = await db.execute(select(Battery).order_by(desc(Battery.updated_at)))
    return result.scalars().all()


@router.get("/{battery_id}", response_model=BatteryResponse)
async def get_battery(
    battery_id: UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    result = await db.execute(select(Battery).where(Battery.id == battery_id))
    battery = result.scalar_one_or_none()
    if not battery:
        raise HTTPException(status_code=404, detail="Battery not found")
    return battery


@router.get("/{battery_id}/logs", response_model=list[BatteryLogResponse])
async def get_battery_logs(
    battery_id: UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(BatteryLog)
        .where(BatteryLog.battery_id == battery_id)
        .order_by(desc(BatteryLog.timestamp))
    )
    return result.scalars().all()


@router.post("", response_model=BatteryResponse, status_code=201)
async def create_battery(
    data: BatteryCreate,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    # Check uniqueness
    existing = await db.execute(select(Battery).where(Battery.serial == data.serial))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"Battery with serial '{data.serial}' already exists")

    battery = Battery(**data.model_dump())
    db.add(battery)
    await db.flush()
    await db.refresh(battery)
    return battery


@router.put("/{battery_id}", response_model=BatteryResponse)
async def update_battery(
    battery_id: UUID,
    data: BatteryUpdate,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    result = await db.execute(select(Battery).where(Battery.id == battery_id))
    battery = result.scalar_one_or_none()
    if not battery:
        raise HTTPException(status_code=404, detail="Battery not found")

    for key, val in data.model_dump(exclude_unset=True).items():
        setattr(battery, key, val)

    await db.flush()
    await db.refresh(battery)
    return battery


class BatchModelUpdate(BaseModel):
    battery_ids: list[UUID]
    model: str


@router.put("/batch/model", response_model=list[BatteryResponse])
async def batch_update_model(
    data: BatchModelUpdate,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Batch-reassign multiple batteries to a new drone model."""
    updated = []
    for bid in data.battery_ids:
        result = await db.execute(select(Battery).where(Battery.id == bid))
        battery = result.scalar_one_or_none()
        if battery:
            battery.model = data.model
            updated.append(battery)
    await db.flush()
    for b in updated:
        await db.refresh(b)
    return updated


@router.delete("/{battery_id}", status_code=204)
async def delete_battery(
    battery_id: UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    result = await db.execute(select(Battery).where(Battery.id == battery_id))
    battery = result.scalar_one_or_none()
    if not battery:
        raise HTTPException(status_code=404, detail="Battery not found")
    await db.delete(battery)
