import logging

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings

logger = logging.getLogger(__name__)

# OpenDroneLog REST API endpoints (per official docs)
# Listing:  GET /api/flights -> Flight[]
# Detail:   GET /api/flight_data?flight_id={id}&max_points={n} -> { flight, telemetry, track, messages }
# Import:   POST /api/import
# Delete:   DELETE /api/flights/delete?flight_id={id}

FLIGHT_LIST_ENDPOINTS = ["/api/flights", "/flights", "/api/v1/flights"]


async def _get_opendronelog_url(db: AsyncSession | None = None) -> str:
    """Get OpenDroneLog URL from database first, then fall back to env var."""
    if db:
        try:
            from app.models.system_settings import SystemSetting
            result = await db.execute(
                select(SystemSetting).where(SystemSetting.key == "opendronelog_url")
            )
            row = result.scalar_one_or_none()
            if row and row.value:
                return row.value.rstrip("/")
        except Exception:
            pass
    url = settings.opendronelog_url
    return url.rstrip("/") if url else ""


def _extract_flights(data: object) -> list[dict]:
    """Extract a list of flights from an OpenDroneLog API response.

    Handles plain arrays and common paginated wrappers like
    { flights: [...] }, { data: [...] }, { results: [...] }, { items: [...] }.
    """
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("flights", "data", "results", "items"):
            if key in data and isinstance(data[key], list):
                return data[key]
    return []


def _normalize_flight(raw: dict) -> dict:
    """Normalize an OpenDroneLog flight object to a consistent schema.

    OpenDroneLog uses camelCase (id, fileName, displayName, droneModel,
    durationSecs, totalDistance, maxAltitude, maxSpeed, startTime, homeLat,
    homeLon, pointCount, notes, color, tags, droneSerial, batterySerial).

    We produce a flat dict with both the original keys and normalized aliases
    so the frontend can use predictable field names.
    """
    return {
        # Preserve all original keys
        **raw,
        # Normalized aliases for frontend consumption
        "id": raw.get("id"),
        "name": raw.get("displayName") or raw.get("fileName") or raw.get("name") or raw.get("title") or "",
        "file_name": raw.get("fileName") or raw.get("file_name") or "",
        "display_name": raw.get("displayName") or raw.get("display_name") or "",
        "drone_model": raw.get("droneModel") or raw.get("drone_model") or raw.get("drone") or raw.get("aircraft") or raw.get("model") or "",
        "drone_serial": raw.get("droneSerial") or raw.get("drone_serial") or "",
        "battery_serial": raw.get("batterySerial") or raw.get("battery_serial") or "",
        "start_time": raw.get("startTime") or raw.get("start_time") or raw.get("date") or raw.get("created_at") or "",
        "duration_secs": raw.get("durationSecs") or raw.get("duration_secs") or raw.get("duration") or raw.get("duration_seconds") or raw.get("flight_duration") or 0,
        "total_distance": raw.get("totalDistance") or raw.get("total_distance") or raw.get("distance") or raw.get("distance_meters") or 0,
        "max_altitude": raw.get("maxAltitude") or raw.get("max_altitude") or raw.get("max_alt") or raw.get("altitude_max") or 0,
        "max_speed": raw.get("maxSpeed") or raw.get("max_speed") or 0,
        "home_lat": raw.get("homeLat") or raw.get("home_lat") or None,
        "home_lon": raw.get("homeLon") or raw.get("home_lon") or None,
        "point_count": raw.get("pointCount") or raw.get("point_count") or 0,
        "notes": raw.get("notes") or "",
        "color": raw.get("color") or "",
        "tags": raw.get("tags") or [],
    }


class OpenDroneLogClient:
    """Client for the OpenDroneLog REST API."""

    async def get_url(self, db: AsyncSession | None = None) -> str:
        return await _get_opendronelog_url(db)

    async def is_configured(self, db: AsyncSession | None = None) -> bool:
        return bool(await self.get_url(db))

    async def list_flights(self, db: AsyncSession | None = None) -> list[dict]:
        """Get all flights from OpenDroneLog. Tries common API patterns."""
        base_url = await self.get_url(db)
        if not base_url:
            return []

        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            last_error = None
            for suffix in FLIGHT_LIST_ENDPOINTS:
                url = f"{base_url}{suffix}"
                try:
                    resp = await client.get(url)
                    if resp.status_code == 404:
                        continue
                    if resp.status_code >= 400:
                        last_error = f"HTTP {resp.status_code} from {url}"
                        logger.warning("OpenDroneLog %s returned %s", url, resp.status_code)
                        continue
                    data = resp.json()
                    flights = _extract_flights(data)
                    normalized = [_normalize_flight(f) for f in flights]
                    logger.info("OpenDroneLog: %d flights from %s", len(normalized), url)
                    return normalized
                except httpx.ConnectError as e:
                    raise ConnectionError(
                        f"Cannot connect to OpenDroneLog at {base_url}. "
                        f"If OpenDroneLog is running on the host machine, use "
                        f"'http://host.docker.internal:<port>' as the URL. Error: {e}"
                    )
                except httpx.TimeoutException:
                    last_error = f"Timeout fetching {url}"
                    logger.warning("OpenDroneLog timeout: %s", url)
                    continue
                except Exception as exc:
                    last_error = f"{type(exc).__name__}: {exc} from {url}"
                    logger.warning("OpenDroneLog error fetching %s: %s", url, exc)
                    continue

            if last_error:
                raise ConnectionError(
                    f"No working flights endpoint found at {base_url}. Last error: {last_error}"
                )
            return []

    async def get_flight(self, flight_id: str, db: AsyncSession | None = None) -> dict:
        """Get detailed flight data including telemetry.

        OpenDroneLog uses: GET /api/flight_data?flight_id={id}&max_points={n}
        Falls back to legacy patterns for compatibility.
        """
        base_url = await self.get_url(db)
        if not base_url:
            return {}
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            # Primary: OpenDroneLog's actual endpoint
            for pattern in [
                f"{base_url}/api/flight_data?flight_id={flight_id}&max_points=5000",
                f"{base_url}/api/flight_data?flight_id={flight_id}",
                # Legacy fallbacks
                f"{base_url}/api/flights/{flight_id}",
                f"{base_url}/flights/{flight_id}",
                f"{base_url}/api/v1/flights/{flight_id}",
            ]:
                try:
                    resp = await client.get(pattern)
                    if resp.status_code == 404:
                        continue
                    resp.raise_for_status()
                    data = resp.json()
                    # OpenDroneLog returns { flight, telemetry, track, messages }
                    if isinstance(data, dict) and "flight" in data:
                        result = _normalize_flight(data["flight"])
                        result["telemetry"] = data.get("telemetry", {})
                        result["track"] = data.get("track", [])
                        result["messages"] = data.get("messages", [])
                        return result
                    # Legacy format: just a flight object
                    return _normalize_flight(data) if isinstance(data, dict) else data
                except httpx.HTTPStatusError as e:
                    if e.response.status_code == 404:
                        continue
                    raise
                except httpx.ConnectError:
                    raise
                except Exception as exc:
                    logger.warning("OpenDroneLog get_flight error for %s: %s", pattern, exc)
                    continue
            return {}

    async def get_flight_track(self, flight_id: str, db: AsyncSession | None = None) -> list:
        """Get GPS track data for a flight.

        OpenDroneLog embeds track in the flight_data response as
        track: [[lon, lat, alt], ...]. We convert to [{lat, lng, alt}, ...]
        for the frontend map component.
        """
        base_url = await self.get_url(db)
        if not base_url:
            return []
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            # Primary: get from flight_data endpoint
            for pattern in [
                f"{base_url}/api/flight_data?flight_id={flight_id}&max_points=5000",
                f"{base_url}/api/flight_data?flight_id={flight_id}",
            ]:
                try:
                    resp = await client.get(pattern)
                    if resp.status_code == 404:
                        continue
                    resp.raise_for_status()
                    data = resp.json()
                    if isinstance(data, dict):
                        track = data.get("track", [])
                        if track and isinstance(track, list):
                            # ODL track format: [[lon, lat, alt], ...]
                            if isinstance(track[0], (list, tuple)) and len(track[0]) >= 2:
                                return [
                                    {"lat": pt[1], "lng": pt[0], "alt": pt[2] if len(pt) > 2 else 0}
                                    for pt in track
                                ]
                            return track
                except Exception as exc:
                    logger.warning("OpenDroneLog track error: %s", exc)
                    continue

            # Fallback: legacy endpoints
            for pattern in [
                f"{base_url}/api/flights/{flight_id}/track",
                f"{base_url}/flights/{flight_id}/track",
                f"{base_url}/api/v1/flights/{flight_id}/track",
            ]:
                try:
                    resp = await client.get(pattern)
                    resp.raise_for_status()
                    return resp.json()
                except Exception:
                    continue

            # Last resort: extract from full flight data
            flight = await self.get_flight(flight_id, db)
            track = flight.get("track", flight.get("gps_data", []))
            if track and isinstance(track, list) and isinstance(track[0], (list, tuple)):
                return [
                    {"lat": pt[1], "lng": pt[0], "alt": pt[2] if len(pt) > 2 else 0}
                    for pt in track
                ]
            return track

    async def test_connection(self, db: AsyncSession | None = None) -> dict:
        """Test connection to OpenDroneLog and return status info."""
        base_url = await self.get_url(db)
        if not base_url:
            return {"status": "error", "message": "OpenDroneLog URL is not configured"}

        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            # Try to reach the base URL first
            try:
                resp = await client.get(base_url)
            except Exception as e:
                return {
                    "status": "error",
                    "message": f"Cannot reach {base_url}: {str(e)}. "
                    f"If running on the host, try http://host.docker.internal:<port>",
                    "url": base_url,
                }

            # Now try to find the flights endpoint
            for suffix in FLIGHT_LIST_ENDPOINTS:
                endpoint = f"{base_url}{suffix}"
                try:
                    resp = await client.get(endpoint)
                    if resp.status_code < 400:
                        data = resp.json()
                        flights = _extract_flights(data)
                        count = len(flights) if flights else 0
                        return {
                            "status": "online",
                            "message": f"Connected. Found {count} flight(s).",
                            "url": base_url,
                            "api_endpoint": endpoint,
                            "flight_count": count,
                        }
                except Exception:
                    continue

            return {
                "status": "error",
                "message": f"Server reachable at {base_url} but no flights API found. "
                "Tried /api/flights, /flights, /api/v1/flights",
                "url": base_url,
            }


opendronelog_client = OpenDroneLogClient()
