"""Pilot management — CRUD, flight hour tracking, and FAA currency status."""

import logging
from collections import defaultdict
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import get_current_user
from app.database import get_db
from app.models.flight import Flight
from app.models.pilot import Pilot
from app.models.user import User
from app.schemas.pilot import PilotCreate, PilotResponse, PilotSummary, PilotUpdate

logger = logging.getLogger("doc.pilots")

router = APIRouter(prefix="/api/pilots", tags=["pilots"])


async def _compute_pilot_hours(db: AsyncSession, pilot_id: str) -> tuple[float, int]:
    """Return (total_flight_hours, total_flights) for a pilot."""
    result = await db.execute(
        select(
            func.coalesce(func.sum(Flight.duration_secs), 0),
            func.count(Flight.id),
        ).where(Flight.pilot_id == pilot_id)
    )
    row = result.one()
    total_secs = float(row[0])
    total_flights = int(row[1])
    return round(total_secs / 3600, 2), total_flights


@router.get("", response_model=list[PilotSummary])
async def list_pilots(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """List all pilots with computed flight hour totals."""
    logger.debug("Listing all pilots")
    result = await db.execute(select(Pilot).order_by(Pilot.name))
    pilots = result.scalars().all()

    summaries = []
    for pilot in pilots:
        hours, flights = await _compute_pilot_hours(db, pilot.id)
        summaries.append(
            PilotSummary(
                id=pilot.id,
                name=pilot.name,
                is_active=pilot.is_active,
                total_flight_hours=hours,
                total_flights=flights,
                faa_certificate_expiry=pilot.faa_certificate_expiry,
            )
        )

    logger.info("Listed %d pilots", len(summaries))
    return summaries


@router.post("", response_model=PilotResponse, status_code=status.HTTP_201_CREATED)
async def create_pilot(
    data: PilotCreate,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Create a new pilot."""
    pilot = Pilot(**data.model_dump())
    db.add(pilot)
    await db.flush()
    await db.refresh(pilot)
    logger.info("Created pilot %s (id=%s)", pilot.name, pilot.id)

    response = PilotResponse.model_validate(pilot)
    response.total_flight_hours = 0
    response.total_flights = 0
    return response


@router.get("/{pilot_id}", response_model=PilotResponse)
async def get_pilot(
    pilot_id: str,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Get pilot detail with flight history summary."""
    result = await db.execute(select(Pilot).where(Pilot.id == pilot_id))
    pilot = result.scalar_one_or_none()
    if not pilot:
        logger.warning("Pilot not found: %s", pilot_id)
        raise HTTPException(status_code=404, detail="Pilot not found")

    hours, flights = await _compute_pilot_hours(db, pilot_id)
    response = PilotResponse.model_validate(pilot)
    response.total_flight_hours = hours
    response.total_flights = flights
    return response


@router.put("/{pilot_id}", response_model=PilotResponse)
async def update_pilot(
    pilot_id: str,
    data: PilotUpdate,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Update pilot information."""
    result = await db.execute(select(Pilot).where(Pilot.id == pilot_id))
    pilot = result.scalar_one_or_none()
    if not pilot:
        logger.warning("Pilot not found for update: %s", pilot_id)
        raise HTTPException(status_code=404, detail="Pilot not found")

    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(pilot, key, value)

    await db.flush()
    await db.refresh(pilot)
    logger.info("Updated pilot %s (id=%s)", pilot.name, pilot.id)

    hours, flights = await _compute_pilot_hours(db, pilot_id)
    response = PilotResponse.model_validate(pilot)
    response.total_flight_hours = hours
    response.total_flights = flights
    return response


@router.delete("/{pilot_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_pilot(
    pilot_id: str,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Soft-delete a pilot by setting is_active=False."""
    result = await db.execute(select(Pilot).where(Pilot.id == pilot_id))
    pilot = result.scalar_one_or_none()
    if not pilot:
        logger.warning("Pilot not found for delete: %s", pilot_id)
        raise HTTPException(status_code=404, detail="Pilot not found")

    pilot.is_active = False
    await db.flush()
    logger.info("Soft-deleted pilot %s (id=%s)", pilot.name, pilot.id)


@router.get("/{pilot_id}/flights")
async def list_pilot_flights(
    pilot_id: str,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """List all flights for a specific pilot."""
    # Verify pilot exists
    result = await db.execute(select(Pilot).where(Pilot.id == pilot_id))
    pilot = result.scalar_one_or_none()
    if not pilot:
        logger.warning("Pilot not found for flight listing: %s", pilot_id)
        raise HTTPException(status_code=404, detail="Pilot not found")

    result = await db.execute(
        select(Flight)
        .where(Flight.pilot_id == pilot_id)
        .order_by(Flight.start_time.desc())
    )
    flights = result.scalars().all()
    logger.info("Listed %d flights for pilot %s", len(flights), pilot_id)

    return [
        {
            "id": str(f.id),
            "name": f.name,
            "drone_model": f.drone_model,
            "start_time": f.start_time.isoformat() if f.start_time else None,
            "duration_secs": f.duration_secs,
            "duration_hours": round(f.duration_secs / 3600, 2) if f.duration_secs else 0,
            "max_altitude": f.max_altitude,
            "total_distance": f.total_distance,
            "aircraft_id": str(f.aircraft_id) if f.aircraft_id else None,
        }
        for f in flights
    ]


@router.get("/{pilot_id}/hours-summary")
async def pilot_hours_summary(
    pilot_id: str,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Breakdown of flight hours by aircraft, month, and FAA Part 107 currency status."""
    # Verify pilot exists
    result = await db.execute(select(Pilot).where(Pilot.id == pilot_id))
    pilot = result.scalar_one_or_none()
    if not pilot:
        logger.warning("Pilot not found for hours summary: %s", pilot_id)
        raise HTTPException(status_code=404, detail="Pilot not found")

    # Fetch all flights for this pilot
    result = await db.execute(
        select(Flight).where(Flight.pilot_id == pilot_id)
    )
    flights = result.scalars().all()

    # By aircraft
    by_aircraft: dict[str, dict] = defaultdict(lambda: {"hours": 0.0, "flights": 0})
    # By month
    by_month: dict[str, dict] = defaultdict(lambda: {"hours": 0.0, "flights": 0})

    latest_flight_date: datetime | None = None

    for f in flights:
        aircraft_label = f.drone_model or "Unknown"
        hours = round(f.duration_secs / 3600, 2) if f.duration_secs else 0

        by_aircraft[aircraft_label]["hours"] += hours
        by_aircraft[aircraft_label]["flights"] += 1

        if f.start_time:
            month_key = f.start_time.strftime("%Y-%m")
            by_month[month_key]["hours"] += hours
            by_month[month_key]["flights"] += 1

            if latest_flight_date is None or f.start_time > latest_flight_date:
                latest_flight_date = f.start_time

    # FAA Part 107 currency check
    # Current if: flown within last 24 months AND certificate hasn't expired
    now = datetime.utcnow()
    twenty_four_months_ago = now - timedelta(days=730)

    has_recent_flight = latest_flight_date is not None and latest_flight_date >= twenty_four_months_ago
    cert_valid = pilot.faa_certificate_expiry is None or pilot.faa_certificate_expiry > now
    is_current = has_recent_flight and cert_valid

    currency_status = {
        "is_current": is_current,
        "last_flight_date": latest_flight_date.isoformat() if latest_flight_date else None,
        "certificate_expiry": pilot.faa_certificate_expiry.isoformat() if pilot.faa_certificate_expiry else None,
        "certificate_expired": not cert_valid,
        "has_recent_flight": has_recent_flight,
    }

    # Round hours in aggregates
    for v in by_aircraft.values():
        v["hours"] = round(v["hours"], 2)
    for v in by_month.values():
        v["hours"] = round(v["hours"], 2)

    # Sort months descending
    sorted_months = dict(sorted(by_month.items(), reverse=True))

    total_hours, total_flights = await _compute_pilot_hours(db, pilot_id)

    logger.info("Hours summary for pilot %s: %.2f hrs, %d flights", pilot_id, total_hours, total_flights)

    return {
        "pilot_id": pilot_id,
        "pilot_name": pilot.name,
        "total_flight_hours": total_hours,
        "total_flights": total_flights,
        "by_aircraft": dict(by_aircraft),
        "by_month": sorted_months,
        "currency": currency_status,
    }
