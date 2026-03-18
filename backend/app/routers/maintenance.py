"""Aircraft maintenance tracking API."""

import logging
from datetime import date, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import get_current_user
from app.database import get_db
from app.models.maintenance import MaintenanceRecord, MaintenanceSchedule
from app.models.user import User
from app.schemas.flight import (
    MaintenanceRecordCreate, MaintenanceRecordResponse,
    MaintenanceScheduleCreate, MaintenanceScheduleResponse,
)

logger = logging.getLogger("droneops.maintenance")

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
    record = MaintenanceRecord(
        aircraft_id=data.aircraft_id,
        maintenance_type=data.maintenance_type,
        description=data.description,
        performed_at=date.fromisoformat(data.performed_at),
        flight_hours_at=data.flight_hours_at,
        next_due_hours=data.next_due_hours,
        next_due_date=date.fromisoformat(data.next_due_date) if data.next_due_date else None,
        cost=data.cost,
        notes=data.notes,
    )
    db.add(record)
    await db.flush()
    await db.refresh(record)

    # Update schedule's last_performed if matching
    schedules = await db.execute(
        select(MaintenanceSchedule).where(
            MaintenanceSchedule.aircraft_id == data.aircraft_id,
            MaintenanceSchedule.maintenance_type == data.maintenance_type,
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
    await db.delete(record)


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
