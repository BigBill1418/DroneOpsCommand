import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings


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

        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            # Try common OpenDroneLog API patterns
            endpoints = [
                f"{base_url}/api/flights",
                f"{base_url}/flights",
                f"{base_url}/api/v1/flights",
            ]
            last_error = None
            for url in endpoints:
                try:
                    resp = await client.get(url)
                    resp.raise_for_status()
                    data = resp.json()
                    # Handle both array and paginated responses
                    if isinstance(data, list):
                        return data
                    if isinstance(data, dict):
                        # Common patterns: { flights: [...] }, { data: [...] }, { results: [...] }
                        for key in ("flights", "data", "results", "items"):
                            if key in data and isinstance(data[key], list):
                                return data[key]
                        return [data]  # Single flight response
                except httpx.HTTPStatusError as e:
                    last_error = e
                    if e.response.status_code == 404:
                        continue  # Try next endpoint
                    raise
                except httpx.ConnectError as e:
                    raise ConnectionError(
                        f"Cannot connect to OpenDroneLog at {base_url}. "
                        f"If OpenDroneLog is running on the host machine, use "
                        f"'http://host.docker.internal:<port>' as the URL. Error: {e}"
                    )

            if last_error:
                raise last_error
            return []

    async def get_flight(self, flight_id: str, db: AsyncSession | None = None) -> dict:
        """Get detailed flight data including telemetry."""
        base_url = await self.get_url(db)
        if not base_url:
            return {}
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            for pattern in [
                f"{base_url}/api/flights/{flight_id}",
                f"{base_url}/flights/{flight_id}",
                f"{base_url}/api/v1/flights/{flight_id}",
            ]:
                try:
                    resp = await client.get(pattern)
                    resp.raise_for_status()
                    return resp.json()
                except httpx.HTTPStatusError as e:
                    if e.response.status_code == 404:
                        continue
                    raise
            return {}

    async def get_flight_track(self, flight_id: str, db: AsyncSession | None = None) -> list[dict]:
        """Get GPS track data for a flight (lat, lng, alt arrays)."""
        base_url = await self.get_url(db)
        if not base_url:
            return []
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            for pattern in [
                f"{base_url}/api/flights/{flight_id}/track",
                f"{base_url}/flights/{flight_id}/track",
                f"{base_url}/api/v1/flights/{flight_id}/track",
            ]:
                try:
                    resp = await client.get(pattern)
                    resp.raise_for_status()
                    return resp.json()
                except httpx.HTTPStatusError as e:
                    if e.response.status_code == 404:
                        continue
                    raise

            # Fallback: extract from full flight data
            flight = await self.get_flight(flight_id, db)
            return flight.get("track", flight.get("gps_data", []))

    async def test_connection(self, db: AsyncSession | None = None) -> dict:
        """Test connection to OpenDroneLog and return status info."""
        base_url = await self.get_url(db)
        if not base_url:
            return {"status": "error", "message": "OpenDroneLog URL is not configured"}

        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            # Try to reach the base URL first
            try:
                resp = await client.get(base_url)
                base_reachable = resp.status_code < 500
            except Exception as e:
                return {
                    "status": "error",
                    "message": f"Cannot reach {base_url}: {str(e)}. "
                    f"If running on the host, try http://host.docker.internal:<port>",
                    "url": base_url,
                }

            # Now try to find the flights endpoint
            for endpoint in [
                f"{base_url}/api/flights",
                f"{base_url}/flights",
                f"{base_url}/api/v1/flights",
            ]:
                try:
                    resp = await client.get(endpoint)
                    if resp.status_code < 400:
                        data = resp.json()
                        count = len(data) if isinstance(data, list) else "unknown"
                        return {
                            "status": "online",
                            "message": f"Connected. Found {count} flight(s).",
                            "url": base_url,
                            "api_endpoint": endpoint,
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
