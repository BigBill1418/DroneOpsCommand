"""Weather and FAA airspace data for the operations dashboard."""

import logging
from datetime import datetime, timedelta

import httpx
from fastapi import APIRouter, Depends

from app.auth.jwt import get_current_user
from app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/weather", tags=["weather"])

# 97402 = Eugene, OR
DEFAULT_LAT = 44.05
DEFAULT_LON = -123.09
DEFAULT_LABEL = "Eugene, OR 97402"

# Wind direction labels
WIND_DIRS = [
    "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
    "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
]


def _deg_to_compass(deg: float) -> str:
    idx = round(deg / 22.5) % 16
    return WIND_DIRS[idx]


# WMO weather codes → human-readable descriptions
WMO_CODES = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Depositing rime fog",
    51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle",
    61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
    71: "Slight snow", 73: "Moderate snow", 75: "Heavy snow",
    77: "Snow grains", 80: "Slight rain showers", 81: "Moderate rain showers",
    82: "Violent rain showers", 85: "Slight snow showers", 86: "Heavy snow showers",
    95: "Thunderstorm", 96: "Thunderstorm w/ slight hail", 99: "Thunderstorm w/ heavy hail",
}


@router.get("/current")
async def get_weather_and_airspace(
    _user: User = Depends(get_current_user),
):
    """Fetch current weather (Open-Meteo) and FAA TFRs for the operations area."""
    weather = await _fetch_weather()
    tfrs = await _fetch_tfrs()
    notams = await _fetch_notams()

    return {
        "location": DEFAULT_LABEL,
        "weather": weather,
        "tfrs": tfrs,
        "notams": notams,
        "fetched_at": datetime.utcnow().isoformat(),
    }


async def _fetch_weather() -> dict:
    """Fetch current weather from Open-Meteo (free, no API key)."""
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={DEFAULT_LAT}&longitude={DEFAULT_LON}"
        "&current=temperature_2m,relative_humidity_2m,weather_code,"
        "wind_speed_10m,wind_direction_10m,wind_gusts_10m,"
        "cloud_cover,visibility,pressure_msl"
        "&temperature_unit=fahrenheit"
        "&wind_speed_unit=mph"
        "&timezone=America/Los_Angeles"
    )
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()

        current = data.get("current", {})
        weather_code = current.get("weather_code", 0)
        wind_dir_deg = current.get("wind_direction_10m", 0)

        return {
            "temperature_f": current.get("temperature_2m"),
            "humidity_pct": current.get("relative_humidity_2m"),
            "condition": WMO_CODES.get(weather_code, "Unknown"),
            "weather_code": weather_code,
            "wind_speed_mph": current.get("wind_speed_10m"),
            "wind_direction_deg": wind_dir_deg,
            "wind_direction": _deg_to_compass(wind_dir_deg) if wind_dir_deg is not None else None,
            "wind_gusts_mph": current.get("wind_gusts_10m"),
            "cloud_cover_pct": current.get("cloud_cover"),
            "visibility_m": current.get("visibility"),
            "pressure_msl_hpa": current.get("pressure_msl"),
        }
    except Exception as exc:
        logger.warning("Failed to fetch weather: %s", exc)
        return {"error": str(exc)}


async def _fetch_tfrs() -> list[dict]:
    """Fetch active FAA TFRs from the FAA's public GeoJSON feed."""
    url = "https://tfr.faa.gov/tfr2/list.html"
    # The FAA publishes TFR data as XML/HTML. We'll use their simpler endpoint.
    # For reliability, we scrape the ADDS TFR feed which returns structured data.
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            # Use the FAA's TFR API endpoint
            resp = await client.get(
                "https://tfr.faa.gov/tfr_map_498/tfrQueryList.jsp",
                headers={"Accept": "text/html"},
            )
            if resp.status_code != 200:
                return []

            # Parse simple TFR listing — extract NOTAM IDs and descriptions
            text = resp.text
            tfrs = []
            # Look for TFR entries — they contain FDC NOTAM identifiers
            import re
            # Match TFR table rows containing NOTAM info
            rows = re.findall(
                r'<tr[^>]*>.*?notamId=([^"&]+).*?</tr>',
                text, re.DOTALL
            )
            for notam_id in rows[:10]:  # Limit to 10
                tfrs.append({
                    "notam_id": notam_id.strip(),
                    "type": "TFR",
                })

            # If parsing fails, return a status message
            if not tfrs:
                return [{"status": "No active TFRs parsed — check tfr.faa.gov for current data"}]

            return tfrs
    except Exception as exc:
        logger.warning("Failed to fetch TFRs: %s", exc)
        return [{"status": f"TFR feed unavailable: {exc}"}]


async def _fetch_notams() -> list[dict]:
    """Fetch NOTAMs for nearby airports from the FAA NOTAM API.

    The closest airports to 97402 (Eugene, OR) are:
    - KEUG (Mahlon Sweet Field / Eugene Airport)
    """
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            # Use the FAA's public NOTAM search
            resp = await client.get(
                "https://api.aviationapi.com/v1/notams",
                params={"apt": "KEUG"},
            )
            if resp.status_code != 200:
                return [{"status": "NOTAM feed unavailable"}]

            data = resp.json()
            notams = []

            # aviationapi returns { "KEUG": [...] }
            keug_notams = data.get("KEUG", data if isinstance(data, list) else [])
            for notam in keug_notams[:8]:  # Limit to 8 most recent
                notams.append({
                    "id": notam.get("notam_id", notam.get("id", "")),
                    "type": notam.get("classification", notam.get("type", "NOTAM")),
                    "text": notam.get("notam", notam.get("message", notam.get("text", ""))),
                    "effective": notam.get("effective_date", notam.get("effective", "")),
                    "expires": notam.get("expire_date", notam.get("expire", "")),
                })

            if not notams:
                return [{"status": "No active NOTAMs for KEUG"}]

            return notams
    except Exception as exc:
        logger.warning("Failed to fetch NOTAMs: %s", exc)
        return [{"status": f"NOTAM feed unavailable: {exc}"}]
