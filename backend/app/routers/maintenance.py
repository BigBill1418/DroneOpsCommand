"""Aircraft maintenance tracking API."""

import asyncio
import logging
import os
import uuid as uuid_mod
from datetime import date, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Body, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import func, select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import get_current_user
from app.config import settings
from app.database import get_db
from app.models.aircraft import Aircraft
from app.models.flight import Flight
from app.models.maintenance import MaintenanceRecord, MaintenanceSchedule
from app.models.user import User
from app.schemas.flight import (
    MaintenanceRecordCreate, MaintenanceRecordUpdate, MaintenanceRecordResponse,
    MaintenanceScheduleCreate, MaintenanceScheduleResponse,
)


# ── DJI industry-standard maintenance defaults ───────────────────────
DJI_MAINTENANCE_DEFAULTS = [
    {"maintenance_type": "Propeller Replacement", "interval_hours": 200, "interval_days": 365,
     "description": "Replace all propellers per DJI recommendation — inspect for chips, cracks, warping after every flight"},
    {"maintenance_type": "Motor Inspection", "interval_hours": 200, "interval_days": 365,
     "description": "Inspect all motors for bearing wear, unusual noise, debris — clean and lubricate per manufacturer spec"},
    {"maintenance_type": "Gimbal Calibration", "interval_hours": 100, "interval_days": 180,
     "description": "Recalibrate gimbal IMU and mechanical alignment — check for drift, vibration, or lens obstruction"},
    {"maintenance_type": "IMU Calibration", "interval_hours": 50, "interval_days": 90,
     "description": "Calibrate Inertial Measurement Unit on level surface — required after firmware updates or compass anomalies"},
    {"maintenance_type": "Compass Calibration", "interval_hours": 50, "interval_days": None,
     "description": "Calibrate compass when operating in new geographic area or after magnetic interference detection"},
    {"maintenance_type": "Airframe Inspection", "interval_hours": 100, "interval_days": 365,
     "description": "Full visual and structural inspection — check arms, landing gear, body for cracks, loose fasteners, water damage"},
    {"maintenance_type": "Battery Health Check", "interval_hours": None, "interval_days": 30,
     "description": "Check all batteries: cycle count, cell voltage balance, swelling, storage voltage — retire batteries exceeding 200 cycles or showing >0.1V cell imbalance"},
    {"maintenance_type": "Firmware Review", "interval_hours": None, "interval_days": 30,
     "description": "Check for and apply DJI firmware updates — review release notes for safety-critical patches before field deployment"},
    {"maintenance_type": "Remote Controller Inspection", "interval_hours": None, "interval_days": 90,
     "description": "Inspect RC: stick tension, button response, antenna integrity, screen condition, firmware version match"},
    {"maintenance_type": "Sensor Cleaning", "interval_hours": 25, "interval_days": 30,
     "description": "Clean all vision sensors, obstacle avoidance sensors, and camera lens — use microfiber and sensor-safe cleaning solution"},
]


class ScheduleIntervalUpdate(BaseModel):
    """Schema for updating a maintenance schedule's interval."""
    interval_hours: float | None = None
    interval_days: int | None = None
    description: str | None = None

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


@router.put("/records/{record_id}", response_model=MaintenanceRecordResponse)
async def update_record(
    record_id: UUID,
    data: MaintenanceRecordUpdate,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    result = await db.execute(select(MaintenanceRecord).where(MaintenanceRecord.id == record_id))
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=404, detail="Record not found")

    if data.aircraft_id is not None:
        record.aircraft_id = data.aircraft_id
    if data.maintenance_type is not None:
        mt = data.maintenance_type.strip()
        if not mt:
            raise HTTPException(status_code=422, detail="At least one maintenance type is required")
        record.maintenance_type = mt
    if data.description is not None:
        record.description = data.description
    if data.performed_at is not None:
        try:
            record.performed_at = date.fromisoformat(data.performed_at)
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=422, detail=f"Invalid performed_at date: {exc}")
    if data.next_due_date is not None:
        try:
            record.next_due_date = date.fromisoformat(data.next_due_date) if data.next_due_date else None
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=422, detail=f"Invalid next_due_date: {exc}")
    if data.flight_hours_at is not None:
        record.flight_hours_at = data.flight_hours_at
    if data.next_due_hours is not None:
        record.next_due_hours = data.next_due_hours
    if data.cost is not None:
        record.cost = data.cost
    if data.notes is not None:
        record.notes = data.notes

    await db.flush()
    await db.refresh(record)
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


@router.get("/schedules/{aircraft_id}", response_model=list[MaintenanceScheduleResponse])
async def get_schedules_for_aircraft(
    aircraft_id: UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Get all maintenance schedules for a specific aircraft."""
    # Verify aircraft exists
    ac_result = await db.execute(select(Aircraft).where(Aircraft.id == aircraft_id))
    if not ac_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Aircraft not found")

    result = await db.execute(
        select(MaintenanceSchedule)
        .where(MaintenanceSchedule.aircraft_id == aircraft_id)
        .order_by(MaintenanceSchedule.maintenance_type)
    )
    return result.scalars().all()


@router.put("/schedules/{schedule_id}", response_model=MaintenanceScheduleResponse)
async def update_schedule(
    schedule_id: UUID,
    data: ScheduleIntervalUpdate,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Update a maintenance schedule's interval or description."""
    result = await db.execute(
        select(MaintenanceSchedule).where(MaintenanceSchedule.id == schedule_id)
    )
    schedule = result.scalar_one_or_none()
    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")

    if data.interval_hours is not None:
        schedule.interval_hours = data.interval_hours
    if data.interval_days is not None:
        schedule.interval_days = data.interval_days
    if data.description is not None:
        schedule.description = data.description

    await db.flush()
    await db.refresh(schedule)
    logger.info(
        "Updated schedule %s (%s) — hours=%s, days=%s",
        schedule_id, schedule.maintenance_type,
        schedule.interval_hours, schedule.interval_days,
    )
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




# ── Skip / Defer ─────────────────────────────────────────────────────

@router.post("/schedules/{schedule_id}/skip")
async def skip_schedule(
    schedule_id: UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Skip a single maintenance reminder — resets last_performed to today."""
    result = await db.execute(select(MaintenanceSchedule).where(MaintenanceSchedule.id == schedule_id))
    schedule = result.scalar_one_or_none()
    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")
    schedule.last_performed = date.today()
    await db.flush()
    next_due = date.today() + timedelta(days=schedule.interval_days) if schedule.interval_days else None
    logger.info("Schedule %s (%s) skipped — next due %s", schedule_id, schedule.maintenance_type, next_due)
    return {"message": "Skipped", "next_due": next_due.isoformat() if next_due else None}


@router.post("/defer-all-overdue")
async def defer_all_overdue(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Defer ALL overdue maintenance schedules by resetting last_performed to today."""
    result = await db.execute(select(MaintenanceSchedule))
    schedules = result.scalars().all()
    deferred = 0
    for sched in schedules:
        if not sched.interval_days:
            continue
        if not sched.last_performed:
            sched.last_performed = date.today()
            deferred += 1
            continue
        days_since = (date.today() - sched.last_performed).days
        if days_since >= sched.interval_days:
            sched.last_performed = date.today()
            deferred += 1
    await db.flush()
    logger.info("Deferred %d overdue maintenance schedules", deferred)
    return {"message": f"Deferred {deferred} overdue items", "deferred": deferred}


# ── Seed Defaults ─────────────────────────────────────────────────────

@router.post("/seed-defaults")
async def seed_defaults(
    aircraft_id: UUID = Body(..., embed=True),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Create industry-standard DJI maintenance schedules for an aircraft if none exist."""
    # Verify aircraft exists
    ac_result = await db.execute(select(Aircraft).where(Aircraft.id == aircraft_id))
    aircraft = ac_result.scalar_one_or_none()
    if not aircraft:
        raise HTTPException(status_code=404, detail="Aircraft not found")

    # Check if schedules already exist
    existing = await db.execute(
        select(func.count())
        .select_from(MaintenanceSchedule)
        .where(MaintenanceSchedule.aircraft_id == aircraft_id)
    )
    count = existing.scalar()
    if count and count > 0:
        logger.info(
            "Aircraft %s (%s) already has %d schedules — skipping seed",
            aircraft_id, aircraft.model_name, count,
        )
        return {
            "message": f"Aircraft already has {count} maintenance schedules — no defaults added",
            "created": 0,
            "existing": count,
        }

    created = []
    today = date.today()
    for default in DJI_MAINTENANCE_DEFAULTS:
        schedule = MaintenanceSchedule(
            aircraft_id=aircraft_id,
            maintenance_type=default["maintenance_type"],
            interval_hours=default["interval_hours"],
            interval_days=default["interval_days"],
            description=default["description"],
            # Assume the aircraft is currently airworthy — set last_performed
            # to today so schedules don't immediately show as overdue.
            last_performed=today,
        )
        db.add(schedule)
        created.append(default["maintenance_type"])

    await db.flush()
    logger.info(
        "Seeded %d default maintenance schedules for aircraft %s (%s)",
        len(created), aircraft_id, aircraft.model_name,
    )
    return {
        "message": f"Created {len(created)} default maintenance schedules",
        "created": len(created),
        "schedules": created,
    }


# ── Maintenance Status (flight-hours-based) ──────────────────────────

@router.get("/status")
async def maintenance_status(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Return maintenance status for ALL aircraft based on flight hours and schedule intervals.

    For each aircraft, calculates total flight hours from Flight.duration_secs,
    then compares against each MaintenanceSchedule to determine:
      - overdue: flight hours since last maintenance exceed the interval
      - due_soon: within 10% of the interval threshold
      - ok: not yet approaching the interval
    Also checks date-based intervals where applicable.
    """
    today = date.today()

    # ── Load all aircraft ────────────────────────────────────────────
    ac_result = await db.execute(select(Aircraft))
    all_aircraft = ac_result.scalars().all()

    # ── Compute total flight hours per aircraft_id ───────────────────
    hours_query = (
        select(
            Flight.aircraft_id,
            func.coalesce(func.sum(Flight.duration_secs), 0).label("total_secs"),
        )
        .where(Flight.aircraft_id.isnot(None))
        .group_by(Flight.aircraft_id)
    )
    hours_result = await db.execute(hours_query)
    flight_hours_map: dict[str, float] = {}
    for row in hours_result.all():
        flight_hours_map[str(row.aircraft_id)] = row.total_secs / 3600.0

    # ── Load all schedules ───────────────────────────────────────────
    sched_result = await db.execute(select(MaintenanceSchedule))
    all_schedules = sched_result.scalars().all()

    # Group schedules by aircraft_id
    schedules_by_aircraft: dict[str, list] = {}
    for sched in all_schedules:
        aid = str(sched.aircraft_id)
        schedules_by_aircraft.setdefault(aid, []).append(sched)

    # ── Load last maintenance record per (aircraft_id, maintenance_type) ─
    # Used to know flight_hours_at time of last maintenance
    last_records_query = (
        select(MaintenanceRecord)
        .order_by(MaintenanceRecord.performed_at.desc())
    )
    last_records_result = await db.execute(last_records_query)
    # Build lookup: (aircraft_id, type) -> most recent record
    last_record_map: dict[tuple[str, str], MaintenanceRecord] = {}
    for rec in last_records_result.scalars().all():
        key = (str(rec.aircraft_id), rec.maintenance_type)
        if key not in last_record_map:  # first = most recent due to desc order
            last_record_map[key] = rec

    # ── Build status for each aircraft ───────────────────────────────
    output = []
    for ac in all_aircraft:
        aid = str(ac.id)
        total_hours = flight_hours_map.get(aid, 0.0)
        schedules = schedules_by_aircraft.get(aid, [])

        schedule_statuses = []
        worst_status = "ok"

        for sched in schedules:
            item_status = "ok"
            hours_remaining = None
            days_remaining = None
            hours_since_maintenance = None
            next_due_at_hours = None

            # ── Hours-based check ────────────────────────────────
            if sched.interval_hours is not None:
                rec = last_record_map.get((aid, sched.maintenance_type))
                if rec and rec.flight_hours_at is not None:
                    # We have a concrete record of the flight hours when
                    # this maintenance was last done.
                    last_hours = rec.flight_hours_at
                elif sched.last_performed is not None:
                    # No record with flight_hours_at, but the schedule has
                    # a last_performed date (e.g. from seed-defaults or
                    # manual entry).  Treat it as if maintenance was done
                    # at the current total hours — the interval counter
                    # starts fresh from here.
                    last_hours = total_hours
                else:
                    # Never performed and no record at all — assume 0.
                    last_hours = 0.0

                hours_since_maintenance = total_hours - last_hours
                next_due_at_hours = last_hours + sched.interval_hours
                hours_remaining = sched.interval_hours - hours_since_maintenance

                threshold_10pct = sched.interval_hours * 0.10

                if hours_since_maintenance >= sched.interval_hours:
                    item_status = "overdue"
                elif hours_remaining <= threshold_10pct:
                    item_status = "due_soon"

            # ── Date-based check ─────────────────────────────────
            if sched.interval_days is not None:
                if sched.last_performed:
                    next_due_date = sched.last_performed + timedelta(days=sched.interval_days)
                    days_remaining = (next_due_date - today).days
                    threshold_days = max(1, int(sched.interval_days * 0.10))

                    if days_remaining < 0:
                        item_status = "overdue"
                    elif days_remaining <= threshold_days and item_status != "overdue":
                        item_status = "due_soon"
                else:
                    # Never performed — overdue
                    days_remaining = -1
                    item_status = "overdue"

            # Track worst status for the aircraft
            if item_status == "overdue":
                worst_status = "overdue"
            elif item_status == "due_soon" and worst_status != "overdue":
                worst_status = "due_soon"

            schedule_statuses.append({
                "schedule_id": str(sched.id),
                "maintenance_type": sched.maintenance_type,
                "description": sched.description,
                "interval_hours": sched.interval_hours,
                "interval_days": sched.interval_days,
                "status": item_status,
                "hours_since_maintenance": round(hours_since_maintenance, 2) if hours_since_maintenance is not None else None,
                "hours_remaining": round(hours_remaining, 2) if hours_remaining is not None else None,
                "next_due_at_hours": round(next_due_at_hours, 2) if next_due_at_hours is not None else None,
                "days_remaining": days_remaining,
                "last_performed": sched.last_performed.isoformat() if sched.last_performed else None,
            })

        output.append({
            "aircraft_id": aid,
            "aircraft_name": ac.model_name,
            "total_flight_hours": round(total_hours, 2),
            "overall_status": worst_status,
            "schedules": schedule_statuses,
        })

    logger.debug("Maintenance status computed for %d aircraft", len(output))
    return output


# ── Due / Overdue alerts ──────────────────────────────────────────────

@router.get("/due")
async def maintenance_due(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Get maintenance items that are due or overdue (includes aircraft name)."""
    today = date.today()
    alerts = []

    # Build aircraft name lookup
    aircraft_result = await db.execute(select(Aircraft))
    aircraft_map = {str(a.id): a.model_name for a in aircraft_result.scalars().all()}

    # Check schedules — date-based intervals AND hours-only with no last_performed
    schedules = await db.execute(select(MaintenanceSchedule))
    for sched in schedules.scalars().all():
        aid = str(sched.aircraft_id)

        # Date-based interval check
        if sched.interval_days:
            if sched.last_performed:
                next_due = sched.last_performed + timedelta(days=sched.interval_days)
                days_until = (next_due - today).days
                if days_until <= 7:  # Due within a week or overdue
                    alerts.append({
                        "schedule_id": str(sched.id),
                        "aircraft_id": aid,
                        "aircraft_name": aircraft_map.get(aid, "Unknown"),
                        "maintenance_type": sched.maintenance_type,
                        "description": sched.description,
                        "next_due_date": next_due.isoformat(),
                        "days_until": days_until,
                        "overdue": days_until < 0,
                    })
            elif not sched.last_performed:
                # Never performed and no last_performed date — overdue
                alerts.append({
                    "schedule_id": str(sched.id),
                    "aircraft_id": aid,
                    "aircraft_name": aircraft_map.get(aid, "Unknown"),
                    "maintenance_type": sched.maintenance_type,
                    "description": sched.description,
                    "next_due_date": None,
                    "days_until": -1,
                    "overdue": True,
                })
        elif not sched.interval_days and sched.last_performed is None:
            # Hours-only schedule that has never been performed — flag it
            alerts.append({
                "schedule_id": str(sched.id),
                "aircraft_id": aid,
                "aircraft_name": aircraft_map.get(aid, "Unknown"),
                "maintenance_type": sched.maintenance_type,
                "description": sched.description,
                "next_due_date": None,
                "days_until": -1,
                "overdue": True,
            })

    # Check record-based next_due_date
    records = await db.execute(
        select(MaintenanceRecord).where(MaintenanceRecord.next_due_date != None)
    )
    for rec in records.scalars().all():
        if rec.next_due_date:
            aid = str(rec.aircraft_id)
            days_until = (rec.next_due_date - today).days
            if days_until <= 7:
                alerts.append({
                    "record_id": str(rec.id),
                    "aircraft_id": aid,
                    "aircraft_name": aircraft_map.get(aid, "Unknown"),
                    "maintenance_type": rec.maintenance_type,
                    "description": rec.description,
                    "next_due_date": rec.next_due_date.isoformat(),
                    "days_until": days_until,
                    "overdue": days_until < 0,
                })

    return sorted(alerts, key=lambda a: a.get("days_until", 0))


@router.get("/next-due")
async def maintenance_next_due(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Get the single next upcoming maintenance item across all aircraft.

    Unlike /due which only shows items within 7 days, this looks at ALL
    future maintenance to always show what's coming next — even if it's
    months away. Used by the dashboard 'Next Service Due' widget.
    """
    today = date.today()
    candidates: list[dict] = []

    # Build aircraft name lookup
    aircraft_result = await db.execute(select(Aircraft))
    aircraft_map = {str(a.id): a.model_name for a in aircraft_result.scalars().all()}

    # From schedules with interval_days
    schedules = await db.execute(select(MaintenanceSchedule))
    for sched in schedules.scalars().all():
        if not sched.interval_days:
            continue
        aid = str(sched.aircraft_id)
        if sched.last_performed:
            next_due = sched.last_performed + timedelta(days=sched.interval_days)
        else:
            next_due = today  # Never performed — due now
        candidates.append({
            "aircraft_id": aid,
            "aircraft_name": aircraft_map.get(aid, "Unknown"),
            "maintenance_type": sched.maintenance_type,
            "description": sched.description,
            "next_due_date": next_due.isoformat(),
            "days_until": (next_due - today).days,
            "overdue": (next_due - today).days < 0,
        })

    # From records with next_due_date
    records = await db.execute(
        select(MaintenanceRecord).where(MaintenanceRecord.next_due_date != None)
    )
    for rec in records.scalars().all():
        if rec.next_due_date:
            aid = str(rec.aircraft_id)
            candidates.append({
                "aircraft_id": aid,
                "aircraft_name": aircraft_map.get(aid, "Unknown"),
                "maintenance_type": rec.maintenance_type,
                "description": rec.description,
                "next_due_date": rec.next_due_date.isoformat(),
                "days_until": (rec.next_due_date - today).days,
                "overdue": (rec.next_due_date - today).days < 0,
            })

    if not candidates:
        return None

    # Return the one with the smallest days_until (most urgent)
    return sorted(candidates, key=lambda c: c["days_until"])[0]
