"""Aircraft maintenance tracking API."""

import asyncio
import logging
import os
import uuid as uuid_mod
from datetime import date, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import get_current_user
from app.config import settings
from app.database import get_db
from app.models.maintenance import MaintenanceRecord, MaintenanceSchedule
from app.models.user import User
from app.schemas.flight import (
    MaintenanceRecordCreate, MaintenanceRecordResponse,
    MaintenanceScheduleCreate, MaintenanceScheduleResponse,
)

logger = logging.getLogger("doc.maintenance")

router = APIRouter(prefix="/api/maintenance", tags=["maintenance"])


# ── Maintenance Records ───────────────────────────────────────────────

@router.get("/records", response_model=list[MaintenanceRecordResponse])
async def list_records(
    aircraft_id: UUID | None = None,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    query = select(MaintenanceRecord).order_by(desc(MaintenanceRecord.performed_at))
    if aircraft_id:
        query = query.where(MaintenanceRecord.aircraft_id == aircraft_id)
    result = await db.execute(query)
    return result.scalars().all()


@router.post("/records", response_model=MaintenanceRecordResponse, status_code=201)
async def create_record(
    data: MaintenanceRecordCreate,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    try:
        performed = date.fromisoformat(data.performed_at)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=f"Invalid performed_at date: {exc}")

    try:
        next_due = date.fromisoformat(data.next_due_date) if data.next_due_date else None
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=f"Invalid next_due_date: {exc}")

    # maintenance_type may be comma-separated (multi-category)
    maintenance_type = data.maintenance_type.strip() if data.maintenance_type else ""
    if not maintenance_type:
        raise HTTPException(status_code=422, detail="At least one maintenance type is required")

    try:
        record = MaintenanceRecord(
            aircraft_id=data.aircraft_id,
            maintenance_type=maintenance_type,
            description=data.description,
            performed_at=performed,
            flight_hours_at=data.flight_hours_at,
            next_due_hours=data.next_due_hours,
            next_due_date=next_due,
            cost=data.cost,
            notes=data.notes,
        )
        db.add(record)
        await db.flush()
        await db.refresh(record)
    except Exception as exc:
        logger.error("Failed to create maintenance record: %s", exc)
        raise HTTPException(status_code=500, detail=f"Database error: {exc}")

    # Update schedule's last_performed if matching any of the types
    type_list = [t.strip() for t in maintenance_type.split(",")]
    for mtype in type_list:
        schedules = await db.execute(
            select(MaintenanceSchedule).where(
                MaintenanceSchedule.aircraft_id == data.aircraft_id,
                MaintenanceSchedule.maintenance_type == mtype,
            )
        )
        for sched in schedules.scalars().all():
            sched.last_performed = record.performed_at

    return record


@router.delete("/records/{record_id}", status_code=204)
async def delete_record(
    record_id: UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    result = await db.execute(select(MaintenanceRecord).where(MaintenanceRecord.id == record_id))
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=404, detail="Record not found")
    # Clean up any images on disk
    for img_path in (record.images or []):
        try:
            os.remove(os.path.join(settings.upload_dir, img_path))
        except OSError:
            pass
    await db.delete(record)


# ── Maintenance Record Images ─────────────────────────────────────────

ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_IMAGE_SIZE = 10_000_000  # 10 MB


def _write_file(path: str, content: bytes):
    with open(path, "wb") as f:
        f.write(content)


@router.post("/records/{record_id}/images", response_model=MaintenanceRecordResponse)
async def upload_maintenance_image(
    record_id: UUID,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    result = await db.execute(select(MaintenanceRecord).where(MaintenanceRecord.id == record_id))
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=404, detail="Record not found")

    content = await file.read()
    if len(content) > MAX_IMAGE_SIZE:
        raise HTTPException(status_code=413, detail="Image too large (10MB max)")
    if file.content_type and file.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(status_code=400, detail="Only JPEG, PNG, and WebP images are allowed")

    upload_dir = os.path.join(settings.upload_dir, "maintenance", str(record_id))
    os.makedirs(upload_dir, exist_ok=True)

    ext = os.path.splitext(file.filename)[1] if file.filename else ".jpg"
    filename = f"{uuid_mod.uuid4()}{ext}"
    file_path = os.path.join(upload_dir, filename)

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _write_file, file_path, content)

    relative_path = os.path.join("maintenance", str(record_id), filename)
    images = list(record.images or [])
    images.append(relative_path)
    record.images = images

    await db.flush()
    await db.refresh(record)
    return record


@router.delete("/records/{record_id}/images/{image_idx}", response_model=MaintenanceRecordResponse)
async def delete_maintenance_image(
    record_id: UUID,
    image_idx: int,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    result = await db.execute(select(MaintenanceRecord).where(MaintenanceRecord.id == record_id))
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=404, detail="Record not found")

    images = list(record.images or [])
    if image_idx < 0 or image_idx >= len(images):
        raise HTTPException(status_code=404, detail="Image not found")

    removed = images.pop(image_idx)
    try:
        os.remove(os.path.join(settings.upload_dir, removed))
    except OSError:
        pass

    record.images = images
    await db.flush()
    await db.refresh(record)
    return record


# ── Maintenance Schedules ─────────────────────────────────────────────

@router.get("/schedules", response_model=list[MaintenanceScheduleResponse])
async def list_schedules(
    aircraft_id: UUID | None = None,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    query = select(MaintenanceSchedule)
    if aircraft_id:
        query = query.where(MaintenanceSchedule.aircraft_id == aircraft_id)
    result = await db.execute(query)
    return result.scalars().all()


@router.post("/schedules", response_model=MaintenanceScheduleResponse, status_code=201)
async def create_schedule(
    data: MaintenanceScheduleCreate,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    schedule = MaintenanceSchedule(**data.model_dump())
    db.add(schedule)
    await db.flush()
    await db.refresh(schedule)
    return schedule


@router.delete("/schedules/{schedule_id}", status_code=204)
async def delete_schedule(
    schedule_id: UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    result = await db.execute(select(MaintenanceSchedule).where(MaintenanceSchedule.id == schedule_id))
    schedule = result.scalar_one_or_none()
    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")
    await db.delete(schedule)


# ── Due / Overdue alerts ──────────────────────────────────────────────

@router.get("/due")
async def maintenance_due(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Get maintenance items that are due or overdue."""
    today = date.today()
    alerts = []

    # Check date-based schedules
    schedules = await db.execute(select(MaintenanceSchedule))
    for sched in schedules.scalars().all():
        if not sched.interval_days:
            continue

        if sched.last_performed:
            from datetime import timedelta
            next_due = sched.last_performed + timedelta(days=sched.interval_days)
            days_until = (next_due - today).days
            if days_until <= 7:  # Due within a week or overdue
                alerts.append({
                    "schedule_id": str(sched.id),
                    "aircraft_id": str(sched.aircraft_id),
                    "maintenance_type": sched.maintenance_type,
                    "description": sched.description,
                    "next_due_date": next_due.isoformat(),
                    "days_until": days_until,
                    "overdue": days_until < 0,
                })
        else:
            # Never performed — always due
            alerts.append({
                "schedule_id": str(sched.id),
                "aircraft_id": str(sched.aircraft_id),
                "maintenance_type": sched.maintenance_type,
                "description": sched.description,
                "next_due_date": None,
                "days_until": -999,
                "overdue": True,
            })

    # Check record-based next_due_date
    records = await db.execute(
        select(MaintenanceRecord).where(MaintenanceRecord.next_due_date != None)
    )
    for rec in records.scalars().all():
        if rec.next_due_date:
            days_until = (rec.next_due_date - today).days
            if days_until <= 7:
                alerts.append({
                    "record_id": str(rec.id),
                    "aircraft_id": str(rec.aircraft_id),
                    "maintenance_type": rec.maintenance_type,
                    "description": rec.description,
                    "next_due_date": rec.next_due_date.isoformat(),
                    "days_until": days_until,
                    "overdue": days_until < 0,
                })

    return sorted(alerts, key=lambda a: a.get("days_until", 0))
