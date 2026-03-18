"""Weather and FAA airspace data for the operations dashboard."""

import logging
import re
from datetime import datetime

import httpx
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import get_current_user
from app.database import get_db
from app.models.system_settings import SystemSetting
from app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/weather", tags=["weather"])

# Fallback defaults — overridden by Settings > Weather Location
DEFAULT_LAT = 44.05
DEFAULT_LON = -123.09
DEFAULT_LABEL = "Eugene, OR"
NEAREST_AIRPORT = "KEUG"


async def _load_weather_location(db: AsyncSession) -> tuple[float, float, str, str]:
    """Load configured weather location from DB, falling back to defaults."""
    result = await db.execute(
        select(SystemSetting).where(
            SystemSetting.key.in_(["weather_lat", "weather_lon", "weather_label", "weather_airport_icao"])
        )
    )
    rows = {r.key: r.value for r in result.scalars().all()}

    try:
        lat = float(rows["weather_lat"]) if rows.get("weather_lat") else DEFAULT_LAT
    except (ValueError, TypeError):
        lat = DEFAULT_LAT
    try:
        lon = float(rows["weather_lon"]) if rows.get("weather_lon") else DEFAULT_LON
    except (ValueError, TypeError):
        lon = DEFAULT_LON

    label = rows.get("weather_label") or DEFAULT_LABEL
    airport = rows.get("weather_airport_icao") or NEAREST_AIRPORT

    return lat, lon, label, airport

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

# Flight category colors for the dashboard
FLIGHT_CAT_INFO = {
    "VFR": {"label": "VFR", "color": "#00ff88", "desc": "Visual Flight Rules — clear for ops"},
    "MVFR": {"label": "MVFR", "color": "#00d4ff", "desc": "Marginal VFR — use caution"},
    "IFR": {"label": "IFR", "color": "#ff6b1a", "desc": "Instrument Flight Rules — poor visibility"},
    "LIFR": {"label": "LIFR", "color": "#ff4444", "desc": "Low IFR — do not fly"},
}


@router.get("/current")
async def get_weather_and_airspace(
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Fetch current weather (Open-Meteo + METAR) and FAA data for configured location."""
    lat, lon, label, airport = await _load_weather_location(db)

    weather = await _fetch_weather(lat, lon)
    metar = await _fetch_metar(airport)
    tfrs = await _fetch_tfrs(airport)
    notams = await _fetch_notams(airport)
    alerts = await _fetch_nws_alerts(lat, lon)

    return {
        "location": label,
        "airport": airport,
        "weather": weather,
        "metar": metar,
        "tfrs": tfrs,
        "notams": notams,
        "alerts": alerts,
        "fetched_at": datetime.utcnow().isoformat(),
    }


async def _fetch_weather(lat: float, lon: float) -> dict:
    """Fetch current weather from Open-Meteo (free, no API key)."""
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
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


async def _fetch_metar(airport: str) -> dict:
    """Fetch METAR from AviationWeather.gov (free, no key).

    Provides official aviation obs including flight category (VFR/IFR).
    """
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://aviationweather.gov/api/data/metar",
                params={"ids": airport, "format": "json"},
            )
            if resp.status_code != 200:
                return {"error": f"HTTP {resp.status_code}"}

            data = resp.json()
            if not data or not isinstance(data, list) or len(data) == 0:
                return {"error": "No METAR data returned"}

            obs = data[0]
            flt_cat = obs.get("fltCat", "")
            cat_info = FLIGHT_CAT_INFO.get(flt_cat, {})

            # Parse wind gusts from raw METAR if present (e.g. 16010G20KT)
            raw = obs.get("rawOb", "")
            gust_kt = None
            gust_match = re.search(r'\d{3}\d{2,3}G(\d{2,3})KT', raw)
            if gust_match:
                gust_kt = int(gust_match.group(1))

            return {
                "station": obs.get("icaoId", airport),
                "station_name": obs.get("name", ""),
                "report_time": obs.get("reportTime", ""),
                "raw_metar": raw,
                "flight_category": flt_cat,
                "flight_category_color": cat_info.get("color", "#5a6478"),
                "flight_category_desc": cat_info.get("desc", ""),
                "temp_c": obs.get("temp"),
                "dewpoint_c": obs.get("dewp"),
                "wind_dir_deg": obs.get("wdir"),
                "wind_speed_kt": obs.get("wspd"),
                "wind_gust_kt": gust_kt,
                "visibility": obs.get("visib"),
                "altimeter_hpa": obs.get("altim"),
                "clouds": obs.get("clouds", []),
            }
    except Exception as exc:
        logger.warning("Failed to fetch METAR: %s", exc)
        return {"error": str(exc)}


async def _fetch_tfrs(airport: str) -> list[dict]:
    """Fetch active FAA TFRs from AviationWeather.gov (official, reliable)."""
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(
                "https://aviationweather.gov/api/data/notam",
                params={
                    "icao": airport,
                    "format": "json",
                    "type": "tfr",
                },
            )
            if resp.status_code != 200:
                # Fallback: try the FAA TFR GeoJSON feed
                return await _fetch_tfrs_fallback()

            data = resp.json()
            if not data or not isinstance(data, list):
                return [{"status": "No active TFRs for area"}]

            tfrs = []
            for item in data[:10]:
                tfrs.append({
                    "notam_id": item.get("notamId", item.get("id", "")),
                    "type": "TFR",
                    "text": (item.get("text", item.get("message", "")) or "")[:200],
                    "effective": item.get("effectiveStart", item.get("effective", "")),
                    "expires": item.get("effectiveEnd", item.get("expire", "")),
                })

            if not tfrs:
                return [{"status": "No active TFRs for area"}]

            return tfrs
    except Exception as exc:
        logger.warning("Failed to fetch TFRs from AviationWeather: %s", exc)
        return await _fetch_tfrs_fallback()


async def _fetch_tfrs_fallback() -> list[dict]:
    """Fallback: try FAA TFR GeoJSON feed."""
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            resp = await client.get(
                "https://tfr.faa.gov/tfr2/list.html",
                headers={"Accept": "text/html"},
            )
            if resp.status_code == 200:
                # Just report that TFRs exist — link user to check manually
                notam_ids = re.findall(r'notamId=([^"&\s]+)', resp.text)
                if notam_ids:
                    return [{"notam_id": nid.strip(), "type": "TFR"} for nid in notam_ids[:10]]
            return [{"status": "No active TFRs parsed — check tfr.faa.gov"}]
    except Exception as exc:
        logger.warning("TFR fallback also failed: %s", exc)
        return [{"status": f"TFR feeds unavailable — check tfr.faa.gov manually"}]


async def _fetch_notams(airport: str) -> list[dict]:
    """Fetch NOTAMs from AviationWeather.gov (official FAA source, free, no key)."""
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(
                "https://aviationweather.gov/api/data/notam",
                params={
                    "icao": airport,
                    "format": "json",
                },
            )
            if resp.status_code != 200:
                return await _fetch_notams_fallback(airport)

            data = resp.json()
            if not data or not isinstance(data, list):
                return await _fetch_notams_fallback(airport)

            notams = []
            for notam in data[:8]:
                notams.append({
                    "id": notam.get("notamId", notam.get("id", "")),
                    "type": notam.get("classification", notam.get("type", "NOTAM")),
                    "text": notam.get("text", notam.get("message", notam.get("traditionalMessage", ""))),
                    "effective": notam.get("effectiveStart", notam.get("effective", "")),
                    "expires": notam.get("effectiveEnd", notam.get("expire", "")),
                })

            if not notams:
                return [{"status": f"No active NOTAMs for {airport}"}]

            return notams
    except Exception as exc:
        logger.warning("Failed to fetch NOTAMs from AviationWeather: %s", exc)
        return await _fetch_notams_fallback(airport)


async def _fetch_notams_fallback(airport: str) -> list[dict]:
    """Fallback: try aviationapi.com for NOTAMs."""
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            resp = await client.get(
                "https://api.aviationapi.com/v1/notams",
                params={"apt": airport},
            )
            if resp.status_code != 200:
                return [{"status": f"No active NOTAMs for {airport}"}]

            data = resp.json()
            airport_notams = data.get(airport, data if isinstance(data, list) else [])
            notams = []
            for notam in airport_notams[:8]:
                notams.append({
                    "id": notam.get("notam_id", notam.get("id", "")),
                    "type": notam.get("classification", notam.get("type", "NOTAM")),
                    "text": notam.get("notam", notam.get("message", notam.get("text", ""))),
                    "effective": notam.get("effective_date", notam.get("effective", "")),
                    "expires": notam.get("expire_date", notam.get("expire", "")),
                })

            if not notams:
                return [{"status": f"No active NOTAMs for {airport}"}]

            return notams
    except Exception as exc:
        logger.warning("NOTAM fallback also failed: %s", exc)
        return [{"status": "NOTAM feeds unavailable — check NOTAMs manually"}]


async def _fetch_nws_alerts(lat: float, lon: float) -> list[dict]:
    """Fetch active NWS weather alerts for the area (free, no key)."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://api.weather.gov/alerts/active",
                params={"point": f"{lat},{lon}"},
                headers={"User-Agent": "DroneOpsReport/1.0"},
            )
            if resp.status_code != 200:
                return []

            data = resp.json()
            alerts = []
            for feature in data.get("features", [])[:5]:
                props = feature.get("properties", {})
                alerts.append({
                    "event": props.get("event", ""),
                    "severity": props.get("severity", ""),
                    "headline": props.get("headline", ""),
                    "description": (props.get("description", "") or "")[:300],
                    "expires": props.get("expires", ""),
                })

            return alerts
    except Exception as exc:
        logger.warning("Failed to fetch NWS alerts: %s", exc)
        return []
