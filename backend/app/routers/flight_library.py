"""Flight library — native flight management with upload, CRUD, and export."""

import hashlib
import json
import logging
import os
import traceback
from collections import Counter, defaultdict
from datetime import datetime as _dt
from pathlib import Path
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.device import validate_device_api_key
from app.auth.jwt import get_current_user
from app.config import settings
from app.database import get_db, async_session
from app.models.aircraft import Aircraft
from app.models.battery import Battery, BatteryLog
from app.models.device_api_key import DeviceApiKey
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
    """Parse a datetime from string, numeric epoch, or datetime object.

    Handles ISO 8601 (OpenDroneLog's format), epoch timestamps, and common
    date strings.  Returns a naive UTC datetime suitable for DB storage.
    """
    if value is None or value == "":
        return None
    if isinstance(value, _dt):
        return value
    # Numeric epoch (seconds since 1970)
    if isinstance(value, (int, float)):
        try:
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
        # Primary: use fromisoformat — handles all ISO 8601 variants that ODL produces
        # e.g. "2024-01-15T14:30:00", "2024-01-15T14:30:00Z", "2024-01-15T14:30:00+00:00"
        try:
            # Python 3.11+ fromisoformat handles trailing Z natively; for 3.10 compat
            # we also replace trailing Z with +00:00
            iso_str = cleaned
            if iso_str.endswith("Z"):
                iso_str = iso_str[:-1] + "+00:00"
            dt = _dt.fromisoformat(iso_str)
            # Strip timezone info to store as naive UTC
            if dt.tzinfo is not None:
                from datetime import timezone
                dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
            return dt
        except (ValueError, TypeError):
            pass
        # Fallback: try explicit format strings for non-ISO date formats
        for fmt in _DATE_FORMATS:
            try:
                return _dt.strptime(cleaned, fmt)
            except ValueError:
                continue
        logger.warning("Could not parse date: %r", value)
    return None

from app.models.system_settings import SystemSetting

PARSER_URL = "http://flight-parser:8100"

# ── Original file storage ─────────────────────────────────────────────
_FLIGHT_LOGS_DIR = Path(settings.upload_dir) / "flight_logs"


def _save_original_file(file_hash: str, content: bytes, filename: str) -> None:
    """Persist the original uploaded flight log to disk for future re-processing."""
    try:
        _FLIGHT_LOGS_DIR.mkdir(parents=True, exist_ok=True)
        ext = Path(filename).suffix.lower() if filename else ".bin"
        dest = _FLIGHT_LOGS_DIR / f"{file_hash}{ext}"
        if not dest.exists():
            dest.write_bytes(content)
            logger.debug("Saved original flight log: %s (%d bytes)", dest.name, len(content))
    except Exception as e:
        logger.warning("Failed to save original flight log %s: %s", file_hash, e)


def _get_stored_file_path(file_hash: str) -> Path | None:
    """Find a stored original file by its hash (any extension)."""
    if not _FLIGHT_LOGS_DIR.exists():
        return None
    for f in _FLIGHT_LOGS_DIR.iterdir():
        if f.stem == file_hash:
            return f
    return None


async def _get_dji_api_key(db: AsyncSession) -> str | None:
    """Read the DJI API key from system settings (set in Settings > Flight Data)."""
    try:
        result = await db.execute(
            select(SystemSetting).where(SystemSetting.key == "dji_api_key")
        )
        row = result.scalar_one_or_none()
        if row and row.value and row.value.strip():
            return row.value.strip()
    except Exception as e:
        logger.warning("Could not read DJI API key from settings: %s", e)
    return None


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


# ── Flight stats summary ─────────────────────────────────────────────
# NOTE: must be registered before /{flight_id} to avoid route shadowing
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


# ── Geo helpers for telemetry-stats ──────────────────────────────────

# Approximate bounding boxes: (state, min_lat, max_lat, min_lon, max_lon)
_US_STATE_BOXES: list[tuple[str, float, float, float, float]] = [
    ("Alabama", 30.22, 35.01, -88.47, -84.89),
    ("Alaska", 51.21, 71.39, -179.15, -129.98),
    ("Arizona", 31.33, 37.00, -114.81, -109.04),
    ("Arkansas", 33.00, 36.50, -94.62, -89.64),
    ("California", 32.53, 42.01, -124.41, -114.13),
    ("Colorado", 36.99, 41.00, -109.06, -102.04),
    ("Connecticut", 40.95, 42.05, -73.73, -71.79),
    ("Delaware", 38.45, 39.84, -75.79, -75.05),
    ("Florida", 24.40, 31.00, -87.63, -80.03),
    ("Georgia", 30.36, 35.00, -85.61, -80.84),
    ("Hawaii", 18.91, 22.24, -160.25, -154.81),
    ("Idaho", 41.99, 49.00, -117.24, -111.04),
    ("Illinois", 36.97, 42.51, -91.51, -87.02),
    ("Indiana", 37.77, 41.76, -88.10, -84.78),
    ("Iowa", 40.37, 43.50, -96.64, -90.14),
    ("Kansas", 36.99, 40.00, -102.05, -94.59),
    ("Kentucky", 36.50, 39.15, -89.57, -81.96),
    ("Louisiana", 28.93, 33.02, -94.04, -88.82),
    ("Maine", 43.06, 47.46, -71.08, -66.95),
    ("Maryland", 37.91, 39.72, -79.49, -75.05),
    ("Massachusetts", 41.24, 42.89, -73.51, -69.93),
    ("Michigan", 41.70, 48.26, -90.42, -82.12),
    ("Minnesota", 43.50, 49.38, -97.24, -89.49),
    ("Mississippi", 30.17, 35.00, -91.66, -88.10),
    ("Missouri", 35.99, 40.61, -95.77, -89.10),
    ("Montana", 44.36, 49.00, -116.05, -104.04),
    ("Nebraska", 40.00, 43.00, -104.05, -95.31),
    ("Nevada", 35.00, 42.00, -120.01, -114.04),
    ("New Hampshire", 42.70, 45.31, -72.56, -70.70),
    ("New Jersey", 38.93, 41.36, -75.56, -73.89),
    ("New Mexico", 31.33, 37.00, -109.05, -103.00),
    ("New York", 40.50, 45.01, -79.76, -71.86),
    ("North Carolina", 33.84, 36.59, -84.32, -75.46),
    ("North Dakota", 45.94, 49.00, -104.05, -96.55),
    ("Ohio", 38.40, 41.98, -84.82, -80.52),
    ("Oklahoma", 33.62, 37.00, -103.00, -94.43),
    ("Oregon", 41.99, 46.29, -124.57, -116.46),
    ("Pennsylvania", 39.72, 42.27, -80.52, -74.69),
    ("Rhode Island", 41.15, 42.02, -71.86, -71.12),
    ("South Carolina", 32.03, 35.22, -83.35, -78.54),
    ("South Dakota", 42.48, 45.95, -104.06, -96.44),
    ("Tennessee", 34.98, 36.68, -90.31, -81.65),
    ("Texas", 25.84, 36.50, -106.65, -93.51),
    ("Utah", 36.99, 42.00, -114.05, -109.04),
    ("Vermont", 42.73, 45.02, -73.44, -71.46),
    ("Virginia", 36.54, 39.47, -83.68, -75.24),
    ("Washington", 45.54, 49.00, -124.85, -116.92),
    ("West Virginia", 37.20, 40.64, -82.64, -77.72),
    ("Wisconsin", 42.49, 47.08, -92.89, -86.25),
    ("Wyoming", 40.99, 45.01, -111.06, -104.05),
]


def _lat_lon_to_state(lat: float, lon: float) -> str | None:
    """Return a US state name for the given lat/lon using bounding boxes."""
    for name, min_lat, max_lat, min_lon, max_lon in _US_STATE_BOXES:
        if min_lat <= lat <= max_lat and min_lon <= lon <= max_lon:
            return name
    return None


def _lon_to_timezone(lat: float, lon: float) -> str | None:
    """Return a US timezone name from lat/lon using simple longitude ranges."""
    if lat < 25 and lon < -150:
        return "Hawaii"
    if lat > 50 and lon < -130:
        return "Alaska"
    if lon <= -115:
        return "Pacific"
    if lon <= -105:
        return "Mountain"
    if lon <= -85:
        return "Central"
    return "Eastern"


# ── Telemetry stats (aggregate) ──────────────────────────────────────
# NOTE: must be registered before /{flight_id} to avoid route shadowing
@router.get("/telemetry-stats")
async def telemetry_stats(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Return aggregate statistics computed from all flights in the database."""
    logger.info("Computing telemetry-stats for user %s", _user.username)

    # Single query — load only the columns we need
    result = await db.execute(
        select(
            Flight.duration_secs,
            Flight.total_distance,
            Flight.max_altitude,
            Flight.max_speed,
            Flight.drone_serial,
            Flight.drone_model,
            Flight.drone_name,
            Flight.home_lat,
            Flight.home_lon,
            Flight.start_time,
            Flight.battery_serial,
            Flight.source,
            Flight.point_count,
            Flight.name,
            Aircraft.model_name.label("aircraft_model_name"),
        ).outerjoin(Aircraft, Flight.aircraft_id == Aircraft.id)
    )
    rows = result.all()

    total_flights = len(rows)
    total_duration_secs = 0.0
    total_distance_m = 0.0
    overall_max_alt_m = 0.0
    overall_max_speed_ms = 0.0
    longest_flight_secs = 0.0
    farthest_flight_m = 0.0
    drone_serials: set[str] = set()
    flight_locations: list[dict] = []
    states: set[str] = set()
    time_zones: set[str] = set()
    earliest: _dt | None = None
    latest: _dt | None = None
    month_counter: Counter[str] = Counter()
    battery_cycles = 0
    drone_stats: dict[str, dict] = defaultdict(lambda: {"flights": 0, "duration": 0.0})
    source_counter: Counter[str] = Counter()
    flights_needing_reprocess = 0

    for row in rows:
        dur = row.duration_secs or 0
        dist = row.total_distance or 0
        alt = row.max_altitude or 0
        spd = row.max_speed or 0

        total_duration_secs += dur
        total_distance_m += dist
        if alt > overall_max_alt_m:
            overall_max_alt_m = alt
        if spd > overall_max_speed_ms:
            overall_max_speed_ms = spd
        if dur > longest_flight_secs:
            longest_flight_secs = dur
        if dist > farthest_flight_m:
            farthest_flight_m = dist

        if row.drone_serial:
            drone_serials.add(row.drone_serial)

        # Best display name: drone_name (nickname) > aircraft model > flight drone_model > serial > Unknown
        drone_display = (
            row.drone_name
            or row.aircraft_model_name
            or row.drone_model
            or row.drone_serial
            or "Unknown"
        )
        drone_stats[drone_display]["flights"] += 1
        drone_stats[drone_display]["duration"] += dur

        if row.home_lat is not None and row.home_lon is not None:
            flight_locations.append({
                "lat": row.home_lat,
                "lon": row.home_lon,
                "name": row.name or "",
                "date": row.start_time.isoformat() if row.start_time else "",
                "drone": drone_display,
            })
            st = _lat_lon_to_state(row.home_lat, row.home_lon)
            if st:
                states.add(st)
            tz = _lon_to_timezone(row.home_lat, row.home_lon)
            if tz:
                time_zones.add(tz)

        if row.start_time:
            if earliest is None or row.start_time < earliest:
                earliest = row.start_time
            if latest is None or row.start_time > latest:
                latest = row.start_time
            month_key = row.start_time.strftime("%Y-%m")
            month_counter[month_key] += 1

        if row.battery_serial:
            battery_cycles += 1

        src = row.source or "unknown"
        source_counter[src] += 1

        if src == "dji_txt" and (row.point_count or 0) == 0:
            flights_needing_reprocess += 1

    # Derived values
    busiest_month = month_counter.most_common(1)[0][0] if month_counter else None
    avg_flight_mins = (total_duration_secs / total_flights / 60) if total_flights > 0 else 0.0

    # Build sorted flights_by_month
    flights_by_month = [
        {"month": m, "count": c}
        for m, c in sorted(month_counter.items())
    ]

    # Build drone_breakdown
    drone_breakdown = sorted(
        [
            {
                "drone": drone,
                "flights": info["flights"],
                "hours": round(info["duration"] / 3600, 2),
            }
            for drone, info in drone_stats.items()
        ],
        key=lambda x: x["flights"],
        reverse=True,
    )

    METERS_TO_MILES = 0.000621371
    METERS_TO_FEET = 3.28084
    MS_TO_MPH = 2.23694

    response = {
        "total_flights": total_flights,
        "total_flight_hours": round(total_duration_secs / 3600, 2),
        "total_distance_miles": round(total_distance_m * METERS_TO_MILES, 2),
        "max_altitude_ft": round(overall_max_alt_m * METERS_TO_FEET, 2),
        "max_speed_mph": round(overall_max_speed_ms * MS_TO_MPH, 2),
        "unique_drones": len(drone_serials),
        "flight_locations": flight_locations,
        "states_flown": sorted(states),
        "time_zones_flown": sorted(time_zones),
        "earliest_flight": earliest.isoformat() if earliest else None,
        "latest_flight": latest.isoformat() if latest else None,
        "busiest_month": busiest_month,
        "avg_flight_duration_mins": round(avg_flight_mins, 2),
        "total_battery_cycles": battery_cycles,
        "longest_flight_secs": longest_flight_secs,
        "farthest_flight_miles": round(farthest_flight_m * METERS_TO_MILES, 2),
        "drone_breakdown": drone_breakdown,
        "source_breakdown": dict(source_counter),
        "flights_by_month": flights_by_month,
        "flights_needing_reprocess": flights_needing_reprocess,
    }

    logger.info("telemetry-stats computed: %d flights, %.1f hours", total_flights, total_duration_secs / 3600)
    return response


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


# ── Device health check (test API key + connectivity) ────────────────
@router.get("/device-health")
async def device_health(
    device: DeviceApiKey = Depends(validate_device_api_key),
    db: AsyncSession = Depends(get_db),
):
    """Lightweight connectivity test for DroneOpsSync.

    Returns 200 with device info if the API key is valid and the server is
    reachable.  DroneOpsSync can hit this endpoint on startup to verify the
    connection before attempting file uploads.
    """
    parser_ok = False
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{PARSER_URL}/health")
            parser_ok = resp.status_code == 200
    except Exception:
        pass

    return {
        "status": "connected",
        "device_label": device.label,
        "parser_available": parser_ok,
        "upload_endpoint": "/api/flight-library/device-upload",
    }


# ── Device upload (field controllers via X-Device-Api-Key) ───────────
@router.post("/device-upload", response_model=FlightUploadResponse)
async def device_upload_flights(
    files: list[UploadFile] = File(...),
    db: AsyncSession = Depends(get_db),
    _device: DeviceApiKey = Depends(validate_device_api_key),
):
    """Upload flight logs from a field controller using a static device API key.

    Identical processing to /upload but authenticates via X-Device-Api-Key header
    instead of a user JWT, allowing automated sync from DroneOpsSync without
    requiring a human login session on the controller.
    """
    imported = []
    skipped = 0
    errors = []

    # Fetch DJI API key from settings to pass to the parser
    dji_key = await _get_dji_api_key(db)
    parser_headers = {}
    if dji_key:
        parser_headers["X-DJI-Api-Key"] = dji_key

    for upload in files:
        try:
            content = await upload.read()
            file_hash = hashlib.sha256(content).hexdigest()

            # Save original file for future re-processing
            _save_original_file(file_hash, content, upload.filename or "upload.bin")

            existing = await db.execute(
                select(Flight).where(Flight.source_file_hash == file_hash)
            )
            if existing.scalar_one_or_none():
                skipped += 1
                continue

            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(
                    f"{PARSER_URL}/parse",
                    files={"file": (upload.filename or "upload.txt", content)},
                    headers=parser_headers,
                )
                if resp.status_code != 200:
                    errors.append(f"{upload.filename}: parser returned {resp.status_code}")
                    continue

                data = resp.json()
                if data.get("errors"):
                    errors.extend(data["errors"])

                for parsed in data.get("flights", []):
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
                        start_time=_parse_datetime(parsed.get("start_time")),
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

                    battery_data = parsed.get("battery_data")
                    if battery_data and battery_data.get("serial"):
                        try:
                            async with db.begin_nested():
                                await _track_battery(db, flight, battery_data)
                        except Exception as bat_exc:
                            logger.warning("Battery tracking failed for flight %s: %s", flight.id, bat_exc)

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


# ── Reprocess status ──────────────────────────────────────────────
@router.get("/reprocess/status")
async def reprocess_status(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Return counts of flights that could benefit from re-processing,
    including how many have original files stored on disk."""
    from sqlalchemy import or_

    total_result = await db.execute(
        select(func.count(Flight.id)).where(Flight.source == "dji_txt")
    )
    total_dji = total_result.scalar() or 0

    # DJI flights needing reprocess
    reprocess_q = await db.execute(
        select(Flight.source_file_hash).where(
            Flight.source == "dji_txt",
            or_(
                Flight.point_count == 0,
                Flight.point_count.is_(None),
                Flight.gps_track.is_(None),
            ),
        )
    )
    reprocess_hashes = [row[0] for row in reprocess_q.all() if row[0]]
    reprocessable = len(reprocess_hashes)

    # How many have original files on disk?
    stored_count = sum(1 for h in reprocess_hashes if _get_stored_file_path(h) is not None)

    logger.info("Reprocess status: %d/%d DJI flights need re-processing (%d have stored files)",
                reprocessable, total_dji, stored_count)
    return {
        "reprocessable": reprocessable,
        "total_dji": total_dji,
        "stored_on_disk": stored_count,
        "need_manual_upload": reprocessable - stored_count,
    }


# ── Reprocess ALL from stored files ──────────────────────────────
@router.post("/reprocess/all")
async def reprocess_all_from_stored(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Re-process all flights that have original files stored on disk.

    Finds flights needing reprocess (missing GPS/telemetry data), reads
    their original file from /data/uploads/flight_logs/, re-parses through
    the flight-parser service with the current DJI API key, and updates
    the flight records in place.
    """
    from sqlalchemy import or_

    dji_key = await _get_dji_api_key(db)
    parser_headers = {}
    if dji_key:
        parser_headers["X-DJI-Api-Key"] = dji_key

    # Find all flights that need re-processing
    result = await db.execute(
        select(Flight).where(
            Flight.source == "dji_txt",
            or_(
                Flight.point_count == 0,
                Flight.point_count.is_(None),
                Flight.gps_track.is_(None),
            ),
        )
    )
    flights_to_reprocess = list(result.scalars().all())

    updated = 0
    skipped_no_file = 0
    errors = []

    logger.info("Reprocess all: found %d flights needing re-processing", len(flights_to_reprocess))

    for flight in flights_to_reprocess:
        file_path = _get_stored_file_path(flight.source_file_hash) if flight.source_file_hash else None
        if not file_path:
            skipped_no_file += 1
            continue

        try:
            content = file_path.read_bytes()
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(
                    f"{PARSER_URL}/parse",
                    files={"file": (file_path.name, content)},
                    headers=parser_headers,
                )
                if resp.status_code != 200:
                    errors.append(f"{flight.name}: parser returned {resp.status_code}")
                    continue

                data = resp.json()
                if data.get("errors"):
                    errors.extend(data["errors"])

                parsed_flights = data.get("flights", [])
                if not parsed_flights:
                    errors.append(f"{flight.name}: parser returned no flights")
                    continue

                parsed = parsed_flights[0]
                flight.duration_secs = parsed.get("duration_secs", 0)
                flight.total_distance = parsed.get("total_distance", 0)
                flight.max_altitude = parsed.get("max_altitude", 0)
                flight.max_speed = parsed.get("max_speed", 0)
                flight.home_lat = parsed.get("home_lat")
                flight.home_lon = parsed.get("home_lon")
                flight.point_count = parsed.get("point_count", 0)
                flight.gps_track = parsed.get("gps_track")
                flight.telemetry = parsed.get("telemetry")
                flight.raw_metadata = parsed.get("raw_metadata")
                await db.flush()
                updated += 1
                logger.info("Reprocess all: updated flight %s (%s) — %d points",
                            flight.id, flight.name, parsed.get("point_count", 0))

        except httpx.ConnectError:
            errors.append(f"{flight.name}: flight-parser service unavailable")
            break  # No point continuing if parser is down
        except Exception as e:
            errors.append(f"{flight.name}: {str(e)}")

    logger.info("Reprocess all complete: %d updated, %d skipped (no file), %d errors",
                updated, skipped_no_file, len(errors))
    return {
        "updated": updated,
        "skipped_no_file": skipped_no_file,
        "errors": errors,
        "total_attempted": len(flights_to_reprocess),
    }


# ── Reprocess uploaded flight logs (manual re-upload) ─────────────
@router.post("/reprocess")
async def reprocess_flights(
    files: list[UploadFile] = File(...),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Re-upload flight logs to update existing flights matched by file hash.

    Unlike /upload which skips duplicates, this endpoint updates existing flight
    records with freshly parsed data (useful after adding/changing the DJI API key).
    """
    updated_count = 0
    imported_count = 0
    skipped = 0
    errors = []

    dji_key = await _get_dji_api_key(db)
    parser_headers = {}
    if dji_key:
        parser_headers["X-DJI-Api-Key"] = dji_key

    for upload in files:
        try:
            content = await upload.read()
            file_hash = hashlib.sha256(content).hexdigest()

            # Save/overwrite original file
            _save_original_file(file_hash, content, upload.filename or "upload.bin")

            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(
                    f"{PARSER_URL}/parse",
                    files={"file": (upload.filename or "upload.txt", content)},
                    headers=parser_headers,
                )
                if resp.status_code != 200:
                    errors.append(f"{upload.filename}: parser returned {resp.status_code}")
                    continue

                data = resp.json()
                if data.get("errors"):
                    errors.extend(data["errors"])

                for parsed in data.get("flights", []):
                    ph = parsed.get("file_hash", file_hash)
                    existing_result = await db.execute(
                        select(Flight).where(Flight.source_file_hash == ph)
                    )
                    existing_flight = existing_result.scalar_one_or_none()

                    if existing_flight:
                        existing_flight.duration_secs = parsed.get("duration_secs", 0)
                        existing_flight.total_distance = parsed.get("total_distance", 0)
                        existing_flight.max_altitude = parsed.get("max_altitude", 0)
                        existing_flight.max_speed = parsed.get("max_speed", 0)
                        existing_flight.home_lat = parsed.get("home_lat")
                        existing_flight.home_lon = parsed.get("home_lon")
                        existing_flight.point_count = parsed.get("point_count", 0)
                        existing_flight.gps_track = parsed.get("gps_track")
                        existing_flight.telemetry = parsed.get("telemetry")
                        existing_flight.raw_metadata = parsed.get("raw_metadata")
                        await db.flush()
                        updated_count += 1
                        logger.info("Reprocess: updated flight %s (%s) — %d points",
                                    existing_flight.id, existing_flight.name,
                                    parsed.get("point_count", 0))
                    else:
                        flight = Flight(
                            name=parsed.get("name", upload.filename or "Unknown"),
                            drone_model=parsed.get("drone_model"),
                            drone_serial=parsed.get("drone_serial"),
                            battery_serial=parsed.get("battery_serial"),
                            start_time=_parse_datetime(parsed.get("start_time")),
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
                        imported_count += 1
                        logger.info("Reprocess: imported new flight %s (%s)", flight.id, flight.name)

                        battery_data = parsed.get("battery_data")
                        if battery_data and battery_data.get("serial"):
                            try:
                                async with db.begin_nested():
                                    await _track_battery(db, flight, battery_data)
                            except Exception as bat_exc:
                                logger.warning("Battery tracking failed for flight %s: %s", flight.id, bat_exc)

        except httpx.ConnectError:
            errors.append(f"{upload.filename}: flight-parser service unavailable")
        except Exception as e:
            errors.append(f"{upload.filename}: {str(e)}")

    logger.info("Reprocess complete: %d updated, %d imported, %d skipped, %d errors",
                updated_count, imported_count, skipped, len(errors))
    return {"updated": updated_count, "imported": imported_count, "skipped": skipped, "errors": errors}


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
        step = (len(arr) - 1) / (target - 1)
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

    # Fetch DJI API key from settings to pass to the parser
    dji_key = await _get_dji_api_key(db)
    parser_headers = {}
    if dji_key:
        parser_headers["X-DJI-Api-Key"] = dji_key

    # Send files to the parser service
    for upload in files:
        try:
            content = await upload.read()
            file_hash = hashlib.sha256(content).hexdigest()

            # Save original file for future re-processing
            _save_original_file(file_hash, content, upload.filename or "upload.bin")

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
                    headers=parser_headers,
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
                        start_time=_parse_datetime(parsed.get("start_time")),
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

                    # Auto-track battery — best-effort via savepoint so failures
                    # don't rollback the flight import
                    battery_data = parsed.get("battery_data")
                    if battery_data and battery_data.get("serial"):
                        try:
                            async with db.begin_nested():
                                await _track_battery(db, flight, battery_data)
                        except Exception as bat_exc:
                            logger.warning("Battery tracking failed for flight %s: %s", flight.id, bat_exc)

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
# ── Purge all flights ────────────────────────────────────────────────
# NOTE: must be registered before /{flight_id} to avoid route shadowing
@router.delete("/purge/all", status_code=200)
async def purge_all_flights(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Delete ALL flights, battery logs, and batteries for a clean re-sync."""
    from sqlalchemy import delete as sql_delete

    # Delete all battery logs (not just flight-linked — full wipe for clean ODL re-sync)
    await db.execute(sql_delete(BatteryLog))
    # Delete all batteries
    bat_result = await db.execute(sql_delete(Battery))
    bat_count = bat_result.rowcount
    # Delete all flights
    result = await db.execute(sql_delete(Flight))
    count = result.rowcount
    await db.commit()
    return {"deleted": count, "batteries_deleted": bat_count}


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

    # Fetch custom equipment names from ODL (battery + drone nicknames)
    equipment = {"battery_names": {}, "aircraft_names": {}}
    try:
        equipment = await opendronelog_client.get_equipment_names(db)
        logger.info("ODL import: loaded %d battery names, %d aircraft names",
                     len(equipment.get("battery_names", {})), len(equipment.get("aircraft_names", {})))
    except Exception as exc:
        logger.warning("ODL import: could not fetch equipment_names: %s", exc)

    battery_names = equipment.get("battery_names", {})   # serial -> custom name
    aircraft_names = equipment.get("aircraft_names", {})  # serial -> custom name

    for odl in odl_flights:
        try:
            odl_id = str(odl.get("id", ""))
            odl_hash = f"odl_{odl_id}"

            # Dedup by ODL flight ID (stable across renames)
            existing = await db.execute(
                select(Flight).where(Flight.source_file_hash == odl_hash)
            )
            if existing.scalar_one_or_none():
                skipped += 1
                continue

            # Flight display name: displayName from ODL, else fileName
            name = odl.get("display_name") or odl.get("name") or f"ODL Flight {odl_id}"

            # Parse start_time (ODL sends ISO 8601)
            start_raw = odl.get("start_time")
            start_dt = _parse_datetime(start_raw)
            if not start_dt:
                logger.warning("ODL import: could not parse start_time '%s' for flight %s (%s)", start_raw, odl_id, name)

            # Drone: model is hardware (droneModel), custom name from equipment_names (priority)
            # then aircraftName (DJI app name embedded in log file) as fallback
            drone_model = odl.get("drone_model") or None
            drone_serial = odl.get("drone_serial") or None
            drone_name = None
            if drone_serial:
                drone_name = aircraft_names.get(drone_serial) or aircraft_names.get(drone_serial.upper()) or None
            if not drone_name:
                drone_name = odl.get("drone_name") or None  # aircraftName from log

            # Battery custom name from equipment_names
            bat_serial = odl.get("battery_serial") or None
            bat_name = ""
            if bat_serial:
                bat_name = battery_names.get(bat_serial) or battery_names.get(bat_serial.upper()) or ""

            # Fetch GPS track
            track = []
            try:
                track = await opendronelog_client.get_flight_track(odl_id, db)
            except Exception:
                pass

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

            flight = Flight(
                name=name,
                drone_model=drone_model,
                drone_name=drone_name,
                drone_serial=drone_serial,
                battery_serial=bat_serial,
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
                source_file_hash=odl_hash,
                original_filename=odl.get("file_name") or None,
            )
            db.add(flight)
            await db.flush()
            imported += 1

            # Auto-create battery record with custom name from equipment_names
            if bat_serial:
                await _track_battery_from_odl(db, flight, bat_serial, bat_name, drone_model)

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

                # Fetch custom equipment names once
                equipment = {"battery_names": {}, "aircraft_names": {}}
                try:
                    equipment = await opendronelog_client.get_equipment_names(db)
                    logger.info("ODL import: loaded %d battery names, %d aircraft names",
                                len(equipment.get("battery_names", {})), len(equipment.get("aircraft_names", {})))
                except Exception as exc:
                    logger.warning("ODL import: could not fetch equipment_names: %s", exc)

                battery_names = equipment.get("battery_names", {})
                aircraft_names = equipment.get("aircraft_names", {})

                imported = 0
                skipped = 0
                error_count = 0
                error_details = []

                for i, odl in enumerate(odl_flights):
                    odl_id = str(odl.get("id", ""))
                    odl_hash = f"odl_{odl_id}"
                    name = odl.get("display_name") or odl.get("name") or f"ODL Flight {odl_id}"

                    try:
                        # Dedup by ODL flight ID
                        existing = await db.execute(
                            select(Flight).where(Flight.source_file_hash == odl_hash)
                        )
                        if existing.scalar_one_or_none():
                            skipped += 1
                            logger.debug("ODL import: skipped duplicate '%s'", name)
                            yield sse({"type": "progress", "current": i + 1, "total": total, "imported": imported, "skipped": skipped, "errors": error_count, "flight_name": name})
                            continue

                        # Parse start_time (ISO 8601 from ODL)
                        start_raw = odl.get("start_time")
                        start_dt = _parse_datetime(start_raw)
                        if not start_dt:
                            logger.warning("ODL import: could not parse start_time '%s' for flight %s", start_raw, odl_id)

                        # Drone: model is hardware, custom name from equipment_names (priority)
                        # then aircraftName (DJI app name from log file) as fallback
                        drone_model = odl.get("drone_model") or None
                        drone_serial = odl.get("drone_serial") or None
                        drone_name = None
                        if drone_serial:
                            drone_name = aircraft_names.get(drone_serial) or aircraft_names.get(drone_serial.upper()) or None
                        if not drone_name:
                            drone_name = odl.get("drone_name") or None  # aircraftName from log

                        # Battery custom name from equipment_names
                        bat_serial = odl.get("battery_serial") or None
                        bat_name = ""
                        if bat_serial:
                            bat_name = battery_names.get(bat_serial) or battery_names.get(bat_serial.upper()) or ""

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

                        flight = Flight(
                            name=name,
                            drone_model=drone_model,
                            drone_name=drone_name,
                            drone_serial=drone_serial,
                            battery_serial=bat_serial,
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
                            source_file_hash=odl_hash,
                            original_filename=odl.get("file_name") or None,
                        )
                        db.add(flight)
                        await db.flush()
                        imported += 1
                        logger.info("ODL import: imported '%s' (%d/%d)", name, i + 1, total)

                        # Auto-create battery record with custom name from equipment_names
                        if bat_serial:
                            await _track_battery_from_odl(db, flight, bat_serial, bat_name, drone_model)

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

    # Look up by actual hardware serial
    result = await db.execute(select(Battery).where(Battery.serial == serial))
    battery = result.scalar_one_or_none()

    # Migration: if battery was previously stored with custom_name as serial, find and fix it
    if not battery and custom_name:
        result2 = await db.execute(select(Battery).where(Battery.serial == custom_name))
        battery = result2.scalar_one_or_none()
        if battery:
            # Fix: move the custom name to the name field, restore actual serial
            battery.name = custom_name
            battery.serial = serial

    if not battery:
        battery = Battery(serial=serial, name=custom_name or None, model=drone_model, cycle_count=0)
        db.add(battery)
        await db.flush()
    else:
        # Always update name from ODL if provided (ODL is source of truth for display names)
        if custom_name:
            battery.name = custom_name
        # Update drone model if not set
        if drone_model and not battery.model:
            battery.model = drone_model

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
