"""Flight library — native flight management with upload, CRUD, and export."""

import hashlib
import json
import logging
import traceback
from datetime import datetime as _dt
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import get_current_user
from app.config import settings
from app.database import get_db, async_session
from app.models.battery import Battery, BatteryLog
from app.models.flight import Flight
from app.models.user import User
from app.schemas.flight import (
    FlightCreate, FlightDetailResponse, FlightResponse, FlightUpdate, FlightUploadResponse,
)

logger = logging.getLogger("doc.flights")

router = APIRouter(prefix="/api/flight-library", tags=["flight-library"])


_DATE_FORMATS = (
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%dT%H:%M:%S.%fZ",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%d",
    "%m/%d/%Y %H:%M:%S",
    "%m/%d/%Y",
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y",
)


def _parse_datetime(value: object) -> _dt | None:
    """Parse a datetime from string, numeric epoch, or datetime object."""
    if value is None or value == "":
        return None
    if isinstance(value, _dt):
        return value
    # Numeric epoch (seconds since 1970)
    if isinstance(value, (int, float)):
        try:
            # Epoch in milliseconds (Java/JS style) vs seconds
            if value > 1e12:
                return _dt.utcfromtimestamp(value / 1000)
            return _dt.utcfromtimestamp(value)
        except (OSError, OverflowError, ValueError):
            return None
    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned:
            return None
        # Try numeric string
        try:
            num = float(cleaned)
            if num > 1e12:
                return _dt.utcfromtimestamp(num / 1000)
            return _dt.utcfromtimestamp(num)
        except (ValueError, OSError, OverflowError):
            pass
        # Try date format strings
        for fmt in _DATE_FORMATS:
            try:
                return _dt.strptime(cleaned.rstrip("Z"), fmt.rstrip("Z").replace("%z", ""))
            except ValueError:
                continue
        logger.warning("Could not parse date: %r", value)
    return None

PARSER_URL = "http://flight-parser:8100"


# ── List flights ──────────────────────────────────────────────────────
@router.get("", response_model=list[FlightResponse])
async def list_flights(
    search: str = Query(None),
    drone: str = Query(None),
    source: str = Query(None),
    limit: int = Query(500, le=2000),
    offset: int = Query(0),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    query = select(Flight).order_by(desc(Flight.start_time), desc(Flight.created_at))

    if search:
        q = f"%{search}%"
        query = query.where(
            Flight.name.ilike(q) | Flight.drone_model.ilike(q) |
            Flight.drone_serial.ilike(q) | Flight.notes.ilike(q)
        )
    if drone:
        query = query.where(Flight.drone_model.ilike(f"%{drone}%"))
    if source:
        query = query.where(Flight.source == source)

    query = query.offset(offset).limit(limit)
    result = await db.execute(query)
    return result.scalars().all()


# ── Get flight detail ─────────────────────────────────────────────────
@router.get("/{flight_id}", response_model=FlightDetailResponse)
async def get_flight(
    flight_id: UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    result = await db.execute(select(Flight).where(Flight.id == flight_id))
    flight = result.scalar_one_or_none()
    if not flight:
        raise HTTPException(status_code=404, detail="Flight not found")
    return flight


# ── Get telemetry (downsampled) ───────────────────────────────────────
@router.get("/{flight_id}/telemetry")
async def get_telemetry(
    flight_id: UUID,
    max_points: int = Query(2000, le=10000),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    result = await db.execute(select(Flight).where(Flight.id == flight_id))
    flight = result.scalar_one_or_none()
    if not flight:
        raise HTTPException(status_code=404, detail="Flight not found")

    telemetry = flight.telemetry or {}
    if not telemetry:
        return {"message": "No telemetry data available", "data": {}}

    # Downsample if needed
    def downsample(arr, target):
        if not arr or len(arr) <= target:
            return arr
        step = len(arr) / target
        return [arr[int(i * step)] for i in range(target)]

    return {
        "timestamps": downsample(telemetry.get("timestamps", []), max_points),
        "altitude": downsample(telemetry.get("altitude", []), max_points),
        "speed": downsample(telemetry.get("speed", []), max_points),
        "battery_pct": downsample(telemetry.get("battery_pct", []), max_points),
        "battery_voltage": downsample(telemetry.get("battery_voltage", []), max_points),
        "battery_temp": downsample(telemetry.get("battery_temp", []), max_points),
        "satellites": downsample(telemetry.get("satellites", []), max_points),
        "signal_strength": downsample(telemetry.get("signal_strength", []), max_points),
        "distance_from_home": downsample(telemetry.get("distance_from_home", []), max_points),
    }


# ── Get GPS track ─────────────────────────────────────────────────────
@router.get("/{flight_id}/track")
async def get_track(
    flight_id: UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    result = await db.execute(select(Flight).where(Flight.id == flight_id))
    flight = result.scalar_one_or_none()
    if not flight:
        raise HTTPException(status_code=404, detail="Flight not found")
    return flight.gps_track or []


# ── Upload flight logs ────────────────────────────────────────────────
@router.post("/upload", response_model=FlightUploadResponse)
async def upload_flights(
    files: list[UploadFile] = File(...),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    imported = []
    skipped = 0
    errors = []

    # Send files to the parser service
    for upload in files:
        try:
            content = await upload.read()
            file_hash = hashlib.sha256(content).hexdigest()

            # Check for duplicate
            existing = await db.execute(
                select(Flight).where(Flight.source_file_hash == file_hash)
            )
            if existing.scalar_one_or_none():
                skipped += 1
                continue

            # Call parser service
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(
                    f"{PARSER_URL}/parse",
                    files={"file": (upload.filename or "upload.txt", content)},
                )
                if resp.status_code != 200:
                    errors.append(f"{upload.filename}: parser returned {resp.status_code}")
                    continue

                data = resp.json()
                if data.get("errors"):
                    errors.extend(data["errors"])

                for parsed in data.get("flights", []):
                    # Check hash dedup again (parser may return multiple flights)
                    ph = parsed.get("file_hash", file_hash)
                    dup = await db.execute(select(Flight).where(Flight.source_file_hash == ph))
                    if dup.scalar_one_or_none():
                        skipped += 1
                        continue

                    flight = Flight(
                        name=parsed.get("name", upload.filename or "Unknown"),
                        drone_model=parsed.get("drone_model"),
                        drone_serial=parsed.get("drone_serial"),
                        battery_serial=parsed.get("battery_serial"),
                        start_time=parsed.get("start_time"),
                        duration_secs=parsed.get("duration_secs", 0),
                        total_distance=parsed.get("total_distance", 0),
                        max_altitude=parsed.get("max_altitude", 0),
                        max_speed=parsed.get("max_speed", 0),
                        home_lat=parsed.get("home_lat"),
                        home_lon=parsed.get("home_lon"),
                        point_count=parsed.get("point_count", 0),
                        gps_track=parsed.get("gps_track"),
                        telemetry=parsed.get("telemetry"),
                        raw_metadata=parsed.get("raw_metadata"),
                        source=parsed.get("source", "dji_txt"),
                        source_file_hash=ph,
                        original_filename=parsed.get("original_filename", upload.filename),
                    )
                    db.add(flight)
                    await db.flush()
                    await db.refresh(flight)
                    imported.append(flight)

                    # Auto-track battery if data is available
                    battery_data = parsed.get("battery_data")
                    if battery_data and battery_data.get("serial"):
                        await _track_battery(db, flight, battery_data)

        except httpx.ConnectError:
            errors.append(f"{upload.filename}: flight-parser service unavailable")
        except Exception as e:
            errors.append(f"{upload.filename}: {str(e)}")

    return FlightUploadResponse(
        imported=len(imported),
        skipped=skipped,
        errors=errors,
        flights=imported,
    )


# ── Manual flight entry ───────────────────────────────────────────────
@router.post("/manual", response_model=FlightResponse, status_code=201)
async def create_manual_flight(
    data: FlightCreate,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    flight = Flight(
        name=data.name,
        drone_model=data.drone_model,
        drone_serial=data.drone_serial,
        battery_serial=data.battery_serial,
        start_time=data.start_time,
        duration_secs=data.duration_secs,
        total_distance=data.total_distance,
        max_altitude=data.max_altitude,
        max_speed=data.max_speed,
        home_lat=data.home_lat,
        home_lon=data.home_lon,
        point_count=len(data.gps_track) if data.gps_track else 0,
        gps_track=data.gps_track,
        notes=data.notes,
        tags=data.tags,
        source="manual",
        aircraft_id=data.aircraft_id,
    )
    db.add(flight)
    await db.flush()
    await db.refresh(flight)
    return flight


# ── Update flight ─────────────────────────────────────────────────────
@router.put("/{flight_id}", response_model=FlightResponse)
async def update_flight(
    flight_id: UUID,
    data: FlightUpdate,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    result = await db.execute(select(Flight).where(Flight.id == flight_id))
    flight = result.scalar_one_or_none()
    if not flight:
        raise HTTPException(status_code=404, detail="Flight not found")

    for key, val in data.model_dump(exclude_unset=True).items():
        setattr(flight, key, val)

    await db.flush()
    await db.refresh(flight)
    return flight


# ── Delete flight ─────────────────────────────────────────────────────
@router.delete("/{flight_id}", status_code=204)
async def delete_flight(
    flight_id: UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    result = await db.execute(select(Flight).where(Flight.id == flight_id))
    flight = result.scalar_one_or_none()
    if not flight:
        raise HTTPException(status_code=404, detail="Flight not found")
    await db.delete(flight)


# ── Purge all flights ────────────────────────────────────────────────
@router.delete("/purge/all", status_code=200)
async def purge_all_flights(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Delete ALL flights and associated battery logs. Requires confirmation."""
    from sqlalchemy import delete as sql_delete

    # Delete battery logs tied to flights first
    await db.execute(sql_delete(BatteryLog).where(BatteryLog.flight_id.isnot(None)))
    # Delete all flights
    result = await db.execute(sql_delete(Flight))
    count = result.rowcount
    await db.commit()
    return {"deleted": count}


# ── Export flight ─────────────────────────────────────────────────────
@router.get("/{flight_id}/export/{fmt}")
async def export_flight(
    flight_id: UUID,
    fmt: str,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    result = await db.execute(select(Flight).where(Flight.id == flight_id))
    flight = result.scalar_one_or_none()
    if not flight:
        raise HTTPException(status_code=404, detail="Flight not found")

    track = flight.gps_track or []

    if fmt == "csv":
        return _export_csv(flight, track)
    elif fmt == "gpx":
        return _export_gpx(flight, track)
    elif fmt == "kml":
        return _export_kml(flight, track)
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported format: {fmt}. Use csv, gpx, or kml.")


# ── Import from OpenDroneLog ──────────────────────────────────────────
@router.post("/import/opendronelog")
async def import_from_opendronelog(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Bulk import all flights from configured OpenDroneLog instance."""
    from app.services.opendronelog import opendronelog_client

    if not await opendronelog_client.is_configured(db):
        raise HTTPException(status_code=400, detail="OpenDroneLog URL not configured")

    imported = 0
    skipped = 0
    errors = []

    try:
        odl_flights = await opendronelog_client.list_flights(db)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to connect to OpenDroneLog: {e}")

    for odl in odl_flights:
        try:
            odl_id = str(odl.get("id", ""))

            # Use custom display name, then file name, then fallback
            name = odl.get("display_name") or odl.get("name") or f"ODL Flight {odl_id}"
            start_raw = odl.get("start_time")

            # Parse start_time into a datetime object (handles strings, epochs, datetimes)
            start_dt = _parse_datetime(start_raw)

            # If list endpoint didn't include start_time, try fetching detail
            if not start_dt:
                try:
                    detail = await opendronelog_client.get_flight(odl_id, db)
                    if detail:
                        detail_time = detail.get("start_time") or detail.get("startTime") or detail.get("timestamp") or detail.get("dateTime") or detail.get("date")
                        start_dt = _parse_datetime(detail_time)
                        # Also pick up richer name/battery data from detail
                        if not odl.get("display_name"):
                            name = detail.get("display_name") or detail.get("displayName") or detail.get("customName") or name
                        if not odl.get("battery_name"):
                            odl["battery_name"] = detail.get("battery_name") or detail.get("batteryName") or detail.get("batteryNickname") or ""
                except Exception:
                    pass

            # Dedup check
            existing = await db.execute(
                select(Flight).where(Flight.name == name, Flight.source == "opendronelog_import")
            )
            if existing.scalar_one_or_none():
                skipped += 1
                continue

            # Fetch GPS track
            track = []
            try:
                track = await opendronelog_client.get_flight_track(odl_id, db)
            except Exception:
                pass

            # Safely convert numeric fields (ODL may return strings or None)
            def _float(val: object, default: float = 0.0) -> float:
                try:
                    return float(val) if val is not None else default
                except (ValueError, TypeError):
                    return default

            def _int(val: object, default: int = 0) -> int:
                try:
                    return int(val) if val is not None else default
                except (ValueError, TypeError):
                    return default

            # Use custom drone name if available, otherwise fall back to drone model
            drone_display = odl.get("drone_name") or odl.get("drone_model") or None

            flight = Flight(
                name=name,
                drone_model=drone_display,
                drone_serial=odl.get("drone_serial") or None,
                battery_serial=odl.get("battery_serial") or None,
                start_time=start_dt,
                duration_secs=_float(odl.get("duration_secs")),
                total_distance=_float(odl.get("total_distance")),
                max_altitude=_float(odl.get("max_altitude")),
                max_speed=_float(odl.get("max_speed")),
                home_lat=_float(odl.get("home_lat"), None) if odl.get("home_lat") else None,
                home_lon=_float(odl.get("home_lon"), None) if odl.get("home_lon") else None,
                point_count=_int(odl.get("point_count")) or len(track),
                gps_track=track if track else None,
                notes=odl.get("notes") or None,
                tags=odl.get("tags") if odl.get("tags") else None,
                source="opendronelog_import",
                original_filename=odl.get("file_name") or None,
            )
            db.add(flight)
            await db.flush()
            imported += 1

            # Auto-create battery record from ODL data
            bat_serial = odl.get("battery_serial")
            if bat_serial:
                bat_name = odl.get("battery_name") or ""
                await _track_battery_from_odl(db, flight, bat_serial, bat_name, drone_display)

        except Exception as e:
            errors.append(f"Flight {odl.get('id', '?')}: {str(e)}")

    return {
        "imported": imported,
        "skipped": skipped,
        "errors": errors,
        "total_in_opendronelog": len(odl_flights),
    }


# ── Streaming import from OpenDroneLog (with progress) ────────────────
@router.post("/import/opendronelog/stream")
async def import_from_opendronelog_stream(
    _user: User = Depends(get_current_user),
):
    """Stream import progress as Server-Sent Events."""
    from app.services.opendronelog import opendronelog_client

    async def event_stream():
        def sse(data: dict) -> str:
            return f"data: {json.dumps(data)}\n\n"

        async with async_session() as db:
            try:
                if not await opendronelog_client.is_configured(db):
                    yield sse({"type": "error", "message": "OpenDroneLog URL not configured"})
                    return

                logger.info("ODL import: fetching flight list...")
                try:
                    odl_flights = await opendronelog_client.list_flights(db)
                except Exception as e:
                    logger.error("ODL import: failed to list flights: %s", e)
                    yield sse({"type": "error", "message": f"Failed to connect to OpenDroneLog: {e}"})
                    return

                total = len(odl_flights)
                logger.info("ODL import: found %d flights to process", total)

                if total == 0:
                    yield sse({"type": "complete", "total": 0, "imported": 0, "skipped": 0, "errors": 0, "error_details": []})
                    return

                imported = 0
                skipped = 0
                error_count = 0
                error_details = []

                for i, odl in enumerate(odl_flights):
                    odl_id = str(odl.get("id", ""))
                    name = odl.get("display_name") or odl.get("name") or f"ODL Flight {odl_id}"

                    try:
                        start_raw = odl.get("start_time")

                        # Parse start_time (handles strings, epochs, datetimes)
                        start_dt = _parse_datetime(start_raw)

                        # If list endpoint didn't include start_time, try fetching detail
                        if not start_dt:
                            try:
                                detail = await opendronelog_client.get_flight(odl_id, db)
                                if detail:
                                    detail_time = detail.get("start_time") or detail.get("startTime") or detail.get("timestamp") or detail.get("dateTime") or detail.get("date")
                                    start_dt = _parse_datetime(detail_time)
                                    if not odl.get("display_name"):
                                        name = detail.get("display_name") or detail.get("displayName") or detail.get("customName") or name
                                    if not odl.get("battery_name"):
                                        odl["battery_name"] = detail.get("battery_name") or detail.get("batteryName") or detail.get("batteryNickname") or ""
                            except Exception:
                                pass
                            if not start_dt:
                                logger.warning("ODL import: could not parse start_time '%s' for flight %s", start_raw, odl_id)

                        # Check duplicate
                        existing = await db.execute(
                            select(Flight).where(Flight.name == name, Flight.source == "opendronelog_import")
                        )
                        if existing.scalar_one_or_none():
                            skipped += 1
                            logger.debug("ODL import: skipped duplicate '%s'", name)
                            yield sse({"type": "progress", "current": i + 1, "total": total, "imported": imported, "skipped": skipped, "errors": error_count, "flight_name": name})
                            continue

                        # Fetch GPS track
                        track = []
                        try:
                            track = await opendronelog_client.get_flight_track(odl_id, db)
                        except Exception as te:
                            logger.warning("ODL import: track fetch failed for %s: %s", odl_id, te)

                        def _float(val, default=0.0):
                            try:
                                return float(val) if val is not None else default
                            except (ValueError, TypeError):
                                return default

                        def _int(val, default=0):
                            try:
                                return int(val) if val is not None else default
                            except (ValueError, TypeError):
                                return default

                        # Use custom drone name if available, otherwise fall back to drone model
                        drone_display = odl.get("drone_name") or odl.get("drone_model") or None

                        flight = Flight(
                            name=name,
                            drone_model=drone_display,
                            drone_serial=odl.get("drone_serial") or None,
                            battery_serial=odl.get("battery_serial") or None,
                            start_time=start_dt,
                            duration_secs=_float(odl.get("duration_secs")),
                            total_distance=_float(odl.get("total_distance")),
                            max_altitude=_float(odl.get("max_altitude")),
                            max_speed=_float(odl.get("max_speed")),
                            home_lat=_float(odl.get("home_lat"), None) if odl.get("home_lat") else None,
                            home_lon=_float(odl.get("home_lon"), None) if odl.get("home_lon") else None,
                            point_count=_int(odl.get("point_count")) or len(track),
                            gps_track=track if track else None,
                            notes=odl.get("notes") or None,
                            tags=odl.get("tags") if odl.get("tags") else None,
                            source="opendronelog_import",
                            original_filename=odl.get("file_name") or None,
                        )
                        db.add(flight)
                        await db.flush()
                        imported += 1
                        logger.info("ODL import: imported '%s' (%d/%d)", name, i + 1, total)

                        # Auto-create battery record from ODL data
                        bat_serial = odl.get("battery_serial")
                        if bat_serial:
                            bat_name = odl.get("battery_name") or ""
                            await _track_battery_from_odl(db, flight, bat_serial, bat_name, drone_display)

                    except Exception as e:
                        error_count += 1
                        err_msg = f"Flight {odl_id} ({name}): {type(e).__name__}: {e}"
                        error_details.append(err_msg)
                        logger.error("ODL import: error on flight %s: %s\n%s", odl_id, e, traceback.format_exc())

                    yield sse({"type": "progress", "current": i + 1, "total": total, "imported": imported, "skipped": skipped, "errors": error_count, "flight_name": name})

                # Commit all at the end
                await db.commit()
                logger.info("ODL import: complete — %d imported, %d skipped, %d errors out of %d", imported, skipped, error_count, total)

                yield sse({
                    "type": "complete",
                    "total": total,
                    "imported": imported,
                    "skipped": skipped,
                    "errors": error_count,
                    "error_details": error_details[:20],
                })

            except Exception as e:
                logger.error("ODL import: fatal error: %s\n%s", e, traceback.format_exc())
                await db.rollback()
                yield sse({"type": "error", "message": f"Import failed: {type(e).__name__}: {e}"})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ── Flight stats summary ─────────────────────────────────────────────
@router.get("/stats/summary")
async def flight_stats(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(
            func.count(Flight.id),
            func.sum(Flight.duration_secs),
            func.sum(Flight.total_distance),
            func.max(Flight.max_altitude),
            func.max(Flight.max_speed),
            func.sum(Flight.point_count),
        )
    )
    row = result.one()
    total = row[0] or 0

    # Longest flight by duration
    longest = None
    longest_result = await db.execute(
        select(Flight).where(Flight.duration_secs > 0).order_by(desc(Flight.duration_secs)).limit(1)
    )
    longest_flight = longest_result.scalar_one_or_none()
    if longest_flight:
        longest = {
            "name": longest_flight.name,
            "duration_secs": longest_flight.duration_secs,
            "drone_model": longest_flight.drone_model,
        }

    # Farthest from home (max total_distance in a single flight)
    farthest = None
    farthest_result = await db.execute(
        select(Flight).where(Flight.total_distance > 0).order_by(desc(Flight.total_distance)).limit(1)
    )
    farthest_flight = farthest_result.scalar_one_or_none()
    if farthest_flight:
        farthest = {
            "name": farthest_flight.name,
            "total_distance": farthest_flight.total_distance,
            "drone_model": farthest_flight.drone_model,
        }

    # Recent flights (last 5)
    recent_result = await db.execute(
        select(Flight).order_by(desc(Flight.start_time)).limit(5)
    )
    recent_flights = [
        {
            "id": str(f.id),
            "name": f.name,
            "start_time": f.start_time.isoformat() if f.start_time else None,
            "duration_secs": f.duration_secs,
            "total_distance": f.total_distance,
            "max_altitude": f.max_altitude,
            "drone_model": f.drone_model,
        }
        for f in recent_result.scalars().all()
    ]

    return {
        "total_flights": total,
        "total_duration": row[1] or 0,
        "total_distance": row[2] or 0,
        "max_altitude": row[3] or 0,
        "max_speed": row[4] or 0,
        "total_points": row[5] or 0,
        "avg_duration": (row[1] or 0) / total if total > 0 else 0,
        "avg_distance": (row[2] or 0) / total if total > 0 else 0,
        "longest_flight": longest,
        "farthest_flight": farthest,
        "recent_flights": recent_flights,
    }


# ── Parser health check ──────────────────────────────────────────────
@router.get("/parser/status")
async def parser_status(
    _user: User = Depends(get_current_user),
):
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{PARSER_URL}/health")
            return resp.json()
    except Exception as e:
        return {"status": "offline", "error": str(e)}


# ── Helpers ───────────────────────────────────────────────────────────

async def _track_battery(db: AsyncSession, flight: Flight, battery_data: dict):
    """Auto-create/update battery record from parsed flight data."""
    serial = battery_data.get("serial")
    if not serial:
        return

    result = await db.execute(select(Battery).where(Battery.serial == serial))
    battery = result.scalar_one_or_none()

    if not battery:
        battery = Battery(serial=serial, model=flight.drone_model, cycle_count=0)
        db.add(battery)
        await db.flush()

    battery.cycle_count += 1
    battery.last_voltage = battery_data.get("end_voltage")

    log = BatteryLog(
        battery_id=battery.id,
        flight_id=flight.id,
        start_voltage=battery_data.get("start_voltage"),
        end_voltage=battery_data.get("end_voltage"),
        min_voltage=battery_data.get("min_voltage"),
        max_temp=battery_data.get("max_temp"),
        discharge_mah=battery_data.get("discharge_mah"),
        cycles_at_time=battery.cycle_count,
    )
    db.add(log)


async def _track_battery_from_odl(
    db: AsyncSession, flight: Flight, serial: str, custom_name: str, drone_model: str | None
):
    """Auto-create/update battery record from ODL flight data."""
    if not serial:
        return

    # Use custom battery name as serial if available (preserves ODL display names)
    display_serial = custom_name if custom_name else serial

    result = await db.execute(select(Battery).where(Battery.serial == display_serial))
    battery = result.scalar_one_or_none()

    # Also check by raw serial in case it was previously imported without custom name
    if not battery and custom_name:
        result2 = await db.execute(select(Battery).where(Battery.serial == serial))
        battery = result2.scalar_one_or_none()
        if battery:
            # Update to use the custom name
            battery.serial = display_serial

    if not battery:
        battery = Battery(serial=display_serial, model=drone_model, cycle_count=0)
        db.add(battery)
        await db.flush()

    battery.cycle_count += 1

    log = BatteryLog(
        battery_id=battery.id,
        flight_id=flight.id,
        cycles_at_time=battery.cycle_count,
    )
    db.add(log)


def _export_csv(flight: Flight, track: list) -> StreamingResponse:
    import csv
    import io

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["latitude", "longitude", "altitude_m", "speed_ms", "timestamp"])
    for pt in track:
        if isinstance(pt, dict):
            writer.writerow([pt.get("lat"), pt.get("lng"), pt.get("alt", 0),
                           pt.get("speed", ""), pt.get("timestamp", "")])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{flight.name}.csv"'},
    )


def _export_gpx(flight: Flight, track: list) -> StreamingResponse:
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<gpx version="1.1" creator="DroneOpsCommand">',
        f'  <trk><name>{flight.name}</name><trkseg>',
    ]
    for pt in track:
        if isinstance(pt, dict):
            lat, lon, alt = pt.get("lat", 0), pt.get("lng", 0), pt.get("alt", 0)
            ts = pt.get("timestamp", "")
            time_tag = f"<time>{ts}</time>" if ts else ""
            lines.append(f'    <trkpt lat="{lat}" lon="{lon}"><ele>{alt}</ele>{time_tag}</trkpt>')
    lines.append("  </trkseg></trk>")
    lines.append("</gpx>")

    content = "\n".join(lines)
    return StreamingResponse(
        iter([content]),
        media_type="application/gpx+xml",
        headers={"Content-Disposition": f'attachment; filename="{flight.name}.gpx"'},
    )


def _export_kml(flight: Flight, track: list) -> StreamingResponse:
    coords = []
    for pt in track:
        if isinstance(pt, dict):
            coords.append(f"{pt.get('lng', 0)},{pt.get('lat', 0)},{pt.get('alt', 0)}")

    content = f"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>{flight.name}</name>
    <Placemark>
      <name>{flight.name}</name>
      <Style><LineStyle><color>ffff6600</color><width>3</width></LineStyle></Style>
      <LineString>
        <altitudeMode>relativeToGround</altitudeMode>
        <coordinates>{" ".join(coords)}</coordinates>
      </LineString>
    </Placemark>
  </Document>
</kml>"""

    return StreamingResponse(
        iter([content]),
        media_type="application/vnd.google-earth.kml+xml",
        headers={"Content-Disposition": f'attachment; filename="{flight.name}.kml"'},
    )
