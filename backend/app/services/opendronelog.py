import httpx

from app.config import settings


class OpenDroneLogClient:
    """Client for the OpenDroneLog REST API."""

    def __init__(self):
        self.base_url = settings.opendronelog_url.rstrip("/") if settings.opendronelog_url else ""

    @property
    def configured(self) -> bool:
        return bool(self.base_url)

    async def list_flights(self) -> list[dict]:
        """Get all flights from OpenDroneLog."""
        if not self.configured:
            return []
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(f"{self.base_url}/api/flights")
            resp.raise_for_status()
            return resp.json()

    async def get_flight(self, flight_id: str) -> dict:
        """Get detailed flight data including telemetry."""
        if not self.configured:
            return {}
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(f"{self.base_url}/api/flights/{flight_id}")
            resp.raise_for_status()
            return resp.json()

    async def get_flight_track(self, flight_id: str) -> list[dict]:
        """Get GPS track data for a flight (lat, lng, alt arrays)."""
        if not self.configured:
            return []
        async with httpx.AsyncClient(timeout=30) as client:
            # Try the export endpoint first, fallback to track
            try:
                resp = await client.get(f"{self.base_url}/api/flights/{flight_id}/track")
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPError:
                # Fallback: extract from full flight data
                flight = await self.get_flight(flight_id)
                return flight.get("track", flight.get("gps_data", []))


opendronelog_client = OpenDroneLogClient()
