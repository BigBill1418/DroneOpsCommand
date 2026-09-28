"""EyesOn flight-record read API — ADR-0049.

EyesOn (the streaming platform) matches its stream sessions to DJI flight
records so a stream drop can be read against what the aircraft was doing
("dropped 8 s before take-off"). This router is the ONLY surface it reads.

**Why a scoped token and not a service-account login (ADR-0048).** A
service account is a full ``users`` row: its JWT reads and writes customers,
invoices and missions. EyesOn needs two read-only views of ``flights``. The
credential stored on the EyesOn host is therefore scoped to exactly these two
GET routes and to nothing else — a leak of it exposes flight telemetry, not
the business.

**Storage.** Only the SHA-256 of the token lives here
(``EYESON_SERVICE_TOKEN_SHA256``); the raw token lives only on the EyesOn
host. Comparison is constant-time. With the hash unset the router answers
``503`` to every request — structurally dark on self-hosted / OSS / demo.

**Heavy columns (ADR-0019).** The list route selects explicit scalar
columns and never touches ``gps_track`` / ``telemetry`` / ``raw_metadata``.
The timeline route detoasts them for ONE flight, then downsamples through
the shared ``select_indices`` so every returned point is a real sample.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.flight import Flight
from app.models.flight_details import FlightDetails, FlightSeries
from app.services.telemetry_downsample import select_indices
from app.utils.timezone import iso_utc

logger = logging.getLogger("droneops.eyeson_integration")

router = APIRouter(prefix="/api/integrations/eyeson", tags=["integrations"])

#: Longest window one list request may span. EyesOn asks for one session
#: ± 10 min; a day-plus ceiling bounds the query without constraining it.
MAX_WINDOW = timedelta(hours=48)
#: No real flight record is longer than this; bounds the start_time scan so a
#: flight that STARTED before the window but overlaps it is still found.
MAX_FLIGHT = timedelta(hours=6)
MAX_FLIGHTS_RETURNED = 200
FRAME_TOLERANCE = timedelta(minutes=5)
TELEMETRY_KEYS = ("altitude", "speed", "battery_pct", "signal_strength", "distance_from_home")
FRAME_SERIES = ("t_offset_s", "rc_uplink", "rc_downlink", "vps_height_m")


def require_eyeson_service_token(
    x_eyeson_service_token: str | None = Header(default=None),
) -> None:
    expected = settings.eyeson_service_token_sha256.strip().lower()
    if not expected:
        raise HTTPException(status_code=503, detail="EyesOn integration not configured")
    if not x_eyeson_service_token:
        raise HTTPException(status_code=401, detail="Missing service token")
    presented = hashlib.sha256(x_eyeson_service_token.encode()).hexdigest()
    if not hmac.compare_digest(presented, expected):
        logger.warning("[EYESON-API] rejected service token (hash prefix %s)", presented[:8])
        raise HTTPException(status_code=401, detail="Invalid service token")


def _parse_instant(value: str, field: str) -> datetime:
    """ISO-8601 → naive UTC (the storage convention). Naive input is UTC."""
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=400, detail=f"{field} must be ISO-8601")
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _plausible(frame: datetime | None, header: datetime) -> datetime:
    """A frame bound, unless it disagrees with the header by > FRAME_TOLERANCE.

    Production carries ``first_frame_at = 1970-01-01`` on flights whose frame
    clock failed to decode (2 of the 3 2026-09-16 M4TD flights). The raw value
    is still returned to the caller; only the overlap filter distrusts it.
    """
    if frame is None or abs(frame - header) > FRAME_TOLERANCE:
        return header
    return frame


@router.get("/flights", dependencies=[Depends(require_eyeson_service_token)])
async def list_flights_in_window(
    start: str = Query(..., description="Window start, ISO-8601 (UTC if naive)"),
    end: str = Query(..., description="Window end, ISO-8601 (UTC if naive)"),
    db: AsyncSession = Depends(get_db),
):
    """Flights whose record interval overlaps ``[start, end]``."""
    lo, hi = _parse_instant(start, "start"), _parse_instant(end, "end")
    if hi <= lo:
        raise HTTPException(status_code=400, detail="end must be after start")
    if hi - lo > MAX_WINDOW:
        raise HTTPException(status_code=400, detail="window exceeds 48 h")

    rows = (
        await db.execute(
            select(
                Flight.id,
                Flight.name,
                Flight.start_time,
                Flight.duration_secs,
                Flight.drone_model,
                Flight.drone_serial,
                Flight.source,
                FlightDetails.first_frame_at,
                FlightDetails.last_frame_at,
            )
            .outerjoin(FlightDetails, FlightDetails.flight_id == Flight.id)
            .where(and_(Flight.start_time <= hi, Flight.start_time >= lo - MAX_FLIGHT))
            .order_by(Flight.start_time)
            .limit(MAX_FLIGHTS_RETURNED)
        )
    ).all()

    out = []
    for r in rows:
        header_end = r.start_time + timedelta(seconds=r.duration_secs or 0)
        begin = _plausible(r.first_frame_at, r.start_time)
        finish = _plausible(r.last_frame_at, header_end)
        if finish < lo or begin > hi:
            continue
        out.append({
            "id": str(r.id),
            "name": r.name,
            "start_time": iso_utc(r.start_time),
            "duration_secs": r.duration_secs,
            "drone_model": r.drone_model,
            "drone_serial": r.drone_serial,
            "source": r.source,
            "first_frame_at": iso_utc(r.first_frame_at),
            "last_frame_at": iso_utc(r.last_frame_at),
        })
    logger.info("[EYESON-API] flights window %s..%s -> %d", iso_utc(lo), iso_utc(hi), len(out))
    return {"flights": out}


def _pick(values: list | None, idx: list[int]) -> list | None:
    if not values:
        return None
    return [values[i] if i < len(values) else None for i in idx]


@router.get("/flights/{flight_id}/timeline", dependencies=[Depends(require_eyeson_service_token)])
async def flight_timeline(
    flight_id: UUID,
    max_points: int = Query(3000, ge=10, le=10000),
    db: AsyncSession = Depends(get_db),
):
    """One flight's time-stamped samples, downsampled by index stride.

    Three blocks, each on its own time base, aligned by the caller:
    ``telemetry`` (absolute ISO timestamps), ``track`` (absolute ISO per
    point) and ``frame`` (``t_offset_s`` from ``frame.origin``).
    """
    row = (
        await db.execute(
            select(Flight.start_time, Flight.telemetry, Flight.gps_track).where(Flight.id == flight_id)
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Flight not found")

    telemetry = row.telemetry or {}
    stamps = telemetry.get("timestamps") or []
    t_idx = select_indices(len(stamps), max_points)
    telemetry_out = {"timestamps": [stamps[i] for i in t_idx]}
    for key in TELEMETRY_KEYS:
        telemetry_out[key] = _pick(telemetry.get(key), t_idx)

    track = row.gps_track if isinstance(row.gps_track, list) else []
    track_out = [
        {"t": p.get("timestamp"), "lat": p.get("lat"), "lng": p.get("lng"), "heading": p.get("heading")}
        for p in (track[i] for i in select_indices(len(track), max_points))
        if isinstance(p, dict)
    ]

    origin = (
        await db.execute(select(FlightDetails.first_frame_at).where(FlightDetails.flight_id == flight_id))
    ).scalar_one_or_none()
    series_rows = (
        await db.execute(
            select(FlightSeries.name, FlightSeries.values).where(
                FlightSeries.flight_id == flight_id,
                FlightSeries.source == "frame",
                FlightSeries.name.in_(FRAME_SERIES),
            )
        )
    ).all()
    stored = {r[0]: (r[1] or []) for r in series_rows}
    base = stored.get("t_offset_s", [])
    f_idx = select_indices(len(base), max_points)
    frame_out: dict = {"origin": iso_utc(origin)}
    for name in FRAME_SERIES:
        frame_out[name] = _pick(stored.get(name), f_idx)

    logger.info(
        "[EYESON-API] timeline flight=%s telemetry=%d track=%d frame=%d",
        flight_id, len(t_idx), len(track_out), len(f_idx),
    )
    return {
        "flight_id": str(flight_id),
        "start_time": iso_utc(row.start_time),
        "telemetry": telemetry_out,
        "track": track_out,
        "frame": frame_out,
    }
