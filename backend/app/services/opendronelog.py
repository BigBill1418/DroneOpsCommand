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

    ODL actual schema (from source):
      GET /api/flights returns camelCase: id, fileName, displayName, droneModel,
      droneSerial, aircraftName, batterySerial, startTime (ISO 8601), durationSecs,
      totalDistance, maxAltitude, maxSpeed, homeLat, homeLon, pointCount, notes,
      color, tags, cycleCount, rcSerial, batteryLife.

    Custom names in ODL:
      - aircraftName = custom drone nickname (stored in equipment_names table)
      - displayName  = custom flight name (stored in flight_customizations table)
      - battery names are in a separate equipment_names table, not on flights

    We map these to our schema:
      - drone_model  = hardware model (droneModel)
      - drone_name   = custom nickname (aircraftName)
      - name/display_name = custom flight name (displayName, fallback to fileName)
    """
    def _first(keys: list[str], default=None):
        for k in keys:
            v = raw.get(k)
            if v is not None and v != "":
                return v
        return default

    return {
        # Preserve all original keys
        **raw,
        # Normalized aliases
        "id": raw.get("id"),
        "name": _first(["displayName", "display_name", "fileName", "file_name", "name"], ""),
        "file_name": _first(["fileName", "file_name", "filename"], ""),
        "display_name": _first(["displayName", "display_name"], ""),
        # drone_model = hardware model (e.g. "DJI Matrice 300 RTK")
        "drone_model": _first(["droneModel", "drone_model"], ""),
        # drone_name = custom nickname (ODL calls this aircraftName)
        "drone_name": _first(["aircraftName", "aircraft_name", "droneName", "drone_name"], ""),
        "drone_serial": _first(["droneSerial", "drone_serial"], ""),
        "battery_serial": _first(["batterySerial", "battery_serial"], ""),
        # battery_name populated later from equipment_names endpoint
        "battery_name": _first(["batteryName", "battery_name"], ""),
        "start_time": _first(["startTime", "start_time"], ""),
        "duration_secs": _first(["durationSecs", "duration_secs", "duration"], 0),
        "total_distance": _first(["totalDistance", "total_distance"], 0),
        "max_altitude": _first(["maxAltitude", "max_altitude"], 0),
        "max_speed": _first(["maxSpeed", "max_speed"], 0),
        "home_lat": _first(["homeLat", "home_lat"], None),
        "home_lon": _first(["homeLon", "home_lon"], None),
        "point_count": _first(["pointCount", "point_count"], 0),
        "notes": _first(["notes"], ""),
        "color": _first(["color"], ""),
        "tags": _first(["tags"], []),
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

    async def get_equipment_names(self, db: AsyncSession | None = None) -> dict:
        """Fetch custom equipment names from ODL.

        ODL stores custom names for drones and batteries in a separate table.
        Returns: { "battery_names": {"SERIAL": "Custom Name"}, "aircraft_names": {"SERIAL": "Custom Name"} }
        """
        base_url = await self.get_url(db)
        if not base_url:
            return {"battery_names": {}, "aircraft_names": {}}
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            try:
                resp = await client.get(f"{base_url}/api/equipment_names")
                if resp.status_code < 400:
                    data = resp.json()
                    return {
                        "battery_names": data.get("battery_names", {}),
                        "aircraft_names": data.get("aircraft_names", {}),
                    }
            except Exception as exc:
                logger.warning("OpenDroneLog equipment_names error: %s", exc)
        return {"battery_names": {}, "aircraft_names": {}}

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
