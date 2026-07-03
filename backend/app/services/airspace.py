"""Airspace / LAANC-awareness for mission-creation pre-flight (ADR-0035).

This module powers an OPERATOR-FACING pre-flight airspace check surfaced
when an operator creates or schedules a mission at a location. It answers,
for a given coordinate:

- What airspace class is at the site (controlled B/C/D/E-surface vs
  uncontrolled G)?
- Is LAANC authorization *likely* required? (controlled ⇒ likely; this is
  awareness, NOT an authorization — DroneOpsCommand is not an FAA-approved
  USS. The operator still authorizes through Aloft/an approved USS.)
- Which facility controls the airspace, if resolvable?
- Are there active TFRs near the site?
- Is current/forecast weather suitable?

HARD RULES (see ADR-0029 / ADR-0035 and the guard tests):
- This is pre-flight *awareness* only. It surfaces facts. It NEVER renders a
  compliance verdict (no "violation" / "illegal" framing) — whether a flight
  is permissible is the certificated operator's determination.
- It is OPERATOR-FACING only and is NEVER wired into the CLIENT report. It is
  computed on demand and never persisted on the mission.

The airspace-class fetch queries the FAA's public Class Airspace ArcGIS
FeatureServer (free, no key) with a point-in-polygon geometry query — the
same "reach an authoritative aviation feed, degrade gracefully on error"
pattern the weather router already uses for METAR/TFR/NOTAM. The TFR/METAR/
weather feeds are the EXISTING ``app.routers.weather`` fetchers, reused
verbatim by the mission preflight route rather than reinvented here.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx

logger = logging.getLogger("doc.airspace")

# FAA public Class Airspace feature service (ArcGIS). Point-in-polygon query
# returns the intersecting controlled-airspace polygon(s) and their class.
# No API key required. Any failure here degrades to "airspace unknown".
FAA_CLASS_AIRSPACE_URL = (
    "https://services6.arcgis.com/ssFJjBXIUyZDrSYZ/ArcGIS/rest/services/"
    "Class_Airspace/FeatureServer/0/query"
)

# LAANC covers Class B, C, D, and the surface areas of Class E. Class G is
# uncontrolled (no LAANC needed). See derive_laanc_requirement.
_CONTROLLED_SURFACE_CLASSES = frozenset({"B", "C", "D"})
_VALID_CLASSES = frozenset({"B", "C", "D", "E", "G"})

# Wind thresholds (mph) for a plain-language suitability advisory. These are
# conservative small-UAS operability hints, NOT regulatory limits.
_WIND_CAUTION_MPH = 20.0
_WIND_WARNING_MPH = 30.0


def normalize_class(raw: str | None) -> str | None:
    """Normalize an airspace-class token to a single upper letter or None.

    Accepts ``"D"``, ``"class d"``, ``" B "`` etc. Returns None for anything
    that isn't one of B/C/D/E/G.
    """
    if not raw or not isinstance(raw, str):
        return None
    token = raw.strip().upper()
    if token.startswith("CLASS"):
        token = token[len("CLASS"):].strip()
    token = token.replace("CLASS_", "").strip()
    if token in _VALID_CLASSES:
        return token
    return None


def derive_laanc_requirement(airspace_class: str | None, *, e_surface: bool = False) -> bool | None:
    """Return whether LAANC authorization is *likely* required.

    Tri-state on purpose:
    - ``True``  — controlled airspace at the surface (B/C/D, or E-surface).
    - ``False`` — uncontrolled (G) or Class E starting above the surface.
    - ``None``  — airspace could not be determined. The caller MUST surface a
      "verify manually" advisory rather than assert a (potentially unsafe)
      default. In an aviation-safety context, fabricating "not required" from
      missing data is the dangerous failure mode.
    """
    cls = normalize_class(airspace_class)
    if cls is None:
        return None
    if cls in _CONTROLLED_SURFACE_CLASSES:
        return True
    if cls == "E":
        return bool(e_surface)
    return False  # Class G — uncontrolled


def _valid_latlon(lat: float, lon: float) -> bool:
    return -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0


def extract_latlon(area_coordinates: dict | None) -> tuple[float, float] | None:
    """Best-effort extraction of a representative (lat, lon) from a mission's
    free-form ``area_coordinates`` JSON.

    Handles the common shapes without assuming a single canonical schema:
    flat lat/lon(+aliases), a ``center`` object, GeoJSON Point, and GeoJSON
    Polygon (returns the vertex centroid). Returns None if no valid
    in-range coordinate can be derived.
    """
    if not isinstance(area_coordinates, dict):
        return None

    def _num(v) -> float | None:
        if isinstance(v, bool):  # bool is an int subclass — reject explicitly
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    # 1. Flat lat/lon with common aliases.
    lat = _num(area_coordinates.get("lat", area_coordinates.get("latitude")))
    lon = _num(area_coordinates.get("lon", area_coordinates.get("lng", area_coordinates.get("longitude"))))
    if lat is not None and lon is not None and _valid_latlon(lat, lon):
        return (lat, lon)

    # 2. Nested "center".
    center = area_coordinates.get("center")
    if isinstance(center, dict):
        got = extract_latlon(center)
        if got is not None:
            return got
    if isinstance(center, (list, tuple)) and len(center) == 2:
        clon, clat = _num(center[0]), _num(center[1])
        if clat is not None and clon is not None and _valid_latlon(clat, clon):
            return (clat, clon)

    # 3. GeoJSON geometry. Coordinates are [lon, lat] order.
    geo_type = str(area_coordinates.get("type", "")).lower()
    coords = area_coordinates.get("coordinates")
    if geo_type == "point" and isinstance(coords, (list, tuple)) and len(coords) >= 2:
        plon, plat = _num(coords[0]), _num(coords[1])
        if plat is not None and plon is not None and _valid_latlon(plat, plon):
            return (plat, plon)
    if geo_type == "polygon" and isinstance(coords, (list, tuple)) and coords:
        ring = coords[0]
        if isinstance(ring, (list, tuple)) and ring:
            pts = [
                (_num(pt[1]), _num(pt[0]))
                for pt in ring
                if isinstance(pt, (list, tuple)) and len(pt) >= 2
            ]
            # Drop the duplicated closing vertex if present.
            if len(pts) >= 2 and pts[0] == pts[-1]:
                pts = pts[:-1]
            pts = [(la, lo) for la, lo in pts if la is not None and lo is not None]
            if pts:
                mlat = sum(p[0] for p in pts) / len(pts)
                mlon = sum(p[1] for p in pts) / len(pts)
                if _valid_latlon(mlat, mlon):
                    return (mlat, mlon)

    return None


async def fetch_airspace_class(lat: float, lon: float) -> dict:
    """Resolve the airspace class at a point via the FAA Class Airspace feed.

    Returns a structured dict — NEVER raises. On any upstream failure the
    ``airspace_class`` is None and ``error`` is populated, so the caller can
    degrade to an "airspace unknown, verify manually" advisory.

    A point with no intersecting controlled-airspace polygon is uncontrolled
    Class G.
    """
    params = {
        "geometry": f"{lon},{lat}",
        "geometryType": "esriGeometryPoint",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "CLASS,NAME,LOCAL_TYPE,LOWER_VAL,LOWER_UOM,LOWER_CODE",
        "returnGeometry": "false",
        "f": "json",
    }
    try:
        async with httpx.AsyncClient(timeout=12, follow_redirects=True) as client:
            resp = await client.get(FAA_CLASS_AIRSPACE_URL, params=params)
            if resp.status_code != 200:
                return _airspace_error(f"HTTP {resp.status_code}")
            data = resp.json()
    except Exception as exc:  # network / JSON / timeout — degrade, never raise
        logger.warning("airspace class fetch failed lat=%.4f lon=%.4f: %s", lat, lon, exc)
        return _airspace_error(str(exc))

    if isinstance(data, dict) and data.get("error"):
        return _airspace_error(str(data.get("error")))

    features = data.get("features", []) if isinstance(data, dict) else []
    if not features:
        # No controlled-airspace polygon intersects ⇒ uncontrolled Class G.
        return {
            "airspace_class": "G",
            "controlling_facility": None,
            "e_surface": False,
            "source": "faa_class_airspace",
            "error": None,
        }

    # Pick the most restrictive intersecting class (B > C > D > E).
    priority = {"B": 4, "C": 3, "D": 2, "E": 1}
    best_cls: str | None = None
    best_name: str | None = None
    best_e_surface = False
    best_rank = -1
    for feat in features:
        attrs = feat.get("attributes", {}) if isinstance(feat, dict) else {}
        cls = normalize_class(attrs.get("CLASS") or attrs.get("LOCAL_TYPE"))
        if cls is None:
            continue
        rank = priority.get(cls, 0)
        if rank > best_rank:
            best_rank = rank
            best_cls = cls
            best_name = attrs.get("NAME") or None
            best_e_surface = cls == "E" and _is_surface(attrs)

    if best_cls is None:
        # Feature(s) present but class unparseable → treat as unknown.
        return _airspace_error("unrecognized airspace class in feature response")

    return {
        "airspace_class": best_cls,
        "controlling_facility": best_name,
        "e_surface": best_e_surface,
        "source": "faa_class_airspace",
        "error": None,
    }


def _is_surface(attrs: dict) -> bool:
    """Heuristic: does this Class-E polygon start at the surface?"""
    lower_val = attrs.get("LOWER_VAL")
    try:
        if lower_val is not None and float(lower_val) == 0.0:
            return True
    except (TypeError, ValueError):
        pass
    tokens = " ".join(
        str(attrs.get(k, "")).upper() for k in ("LOCAL_TYPE", "LOWER_CODE", "LOWER_UOM")
    )
    return "SFC" in tokens or "SURFACE" in tokens


def _airspace_error(msg: str) -> dict:
    return {
        "airspace_class": None,
        "controlling_facility": None,
        "e_surface": False,
        "source": "faa_class_airspace",
        "error": msg,
    }


def _active_tfrs(tfrs: list[dict] | None) -> list[dict]:
    """Filter the weather-router TFR payload down to genuine active TFRs.

    The weather fetcher returns ``[{"status": "No active TFRs..."}]`` when
    there are none; a real TFR carries a ``notam_id``/``text``.
    """
    if not isinstance(tfrs, list):
        return []
    return [t for t in tfrs if isinstance(t, dict) and (t.get("notam_id") or t.get("text"))]


def assemble_preflight(
    *,
    lat: float,
    lon: float,
    airport: str | None,
    airspace: dict,
    tfrs: list[dict] | None,
    weather: dict | None,
    metar: dict | None,
) -> dict:
    """Assemble the structured, operator-facing preflight from already-fetched
    feed results. Pure function — no I/O, never raises.

    Every advisory is a neutral, factual awareness statement. None of them
    render a compliance verdict (that is the operator's determination).
    """
    airspace = airspace or {}
    cls = normalize_class(airspace.get("airspace_class"))
    e_surface = bool(airspace.get("e_surface"))
    laanc = derive_laanc_requirement(cls, e_surface=e_surface)
    active = _active_tfrs(tfrs)

    weather = weather or {}
    metar = metar or {}
    weather_ok = not weather.get("error")
    metar_ok = not metar.get("error")

    degraded = bool(airspace.get("error")) or not weather_ok or not metar_ok

    advisories: list[dict] = []

    # ── Airspace / LAANC awareness ──
    if laanc is True:
        label = f"Class {cls}" if cls else "controlled airspace"
        facility = airspace.get("controlling_facility")
        fac = f" controlled by {facility}" if facility else ""
        advisories.append(
            {
                "code": "laanc_likely_required",
                "severity": "caution",
                "message": (
                    f"Site is in controlled airspace ({label}){fac}. LAANC "
                    "authorization is likely required before flight — request "
                    "it through an approved USS (e.g. Aloft)."
                ),
            }
        )
    elif laanc is False:
        label = f"Class {cls}" if cls else "uncontrolled airspace"
        advisories.append(
            {
                "code": "laanc_not_required",
                "severity": "info",
                "message": (
                    f"Site is in uncontrolled airspace ({label}). LAANC "
                    "authorization is not typically required; confirm no other "
                    "restrictions (TFRs, NSUFR, local ordinances) apply."
                ),
            }
        )
    else:
        advisories.append(
            {
                "code": "airspace_unavailable",
                "severity": "caution",
                "message": (
                    "Airspace class could not be determined from the FAA feed. "
                    "Verify airspace and LAANC applicability manually via the "
                    "FAA UAS facility maps or B4UFLY before flight."
                ),
            }
        )

    # ── TFRs ──
    if active:
        advisories.append(
            {
                "code": "active_tfr",
                "severity": "warning",
                "message": (
                    f"{len(active)} active TFR(s) reported near the site — review "
                    "each before flight."
                ),
            }
        )

    # ── Weather suitability ──
    flt_cat = (metar.get("flight_category") or "").upper()
    if flt_cat in ("IFR", "LIFR"):
        advisories.append(
            {
                "code": "weather_low_ceiling_visibility",
                "severity": "warning" if flt_cat == "LIFR" else "caution",
                "message": (
                    f"Nearest station reporting {flt_cat} — low ceiling/visibility. "
                    "Visual-line-of-sight conditions may be marginal."
                ),
            }
        )
    elif flt_cat == "MVFR":
        advisories.append(
            {
                "code": "weather_marginal_vfr",
                "severity": "caution",
                "message": "Nearest station reporting MVFR — use caution.",
            }
        )

    wind = weather.get("wind_gusts_mph") or weather.get("wind_speed_mph")
    try:
        wind_val = float(wind) if wind is not None else None
    except (TypeError, ValueError):
        wind_val = None
    if wind_val is not None and wind_val >= _WIND_CAUTION_MPH:
        advisories.append(
            {
                "code": "wind_elevated",
                "severity": "warning" if wind_val >= _WIND_WARNING_MPH else "caution",
                "message": (
                    f"Wind/gusts near {round(wind_val)} mph at the site — assess "
                    "against your aircraft's operating envelope."
                ),
            }
        )

    if degraded:
        advisories.append(
            {
                "code": "partial_data",
                "severity": "info",
                "message": (
                    "One or more aviation/weather feeds were unavailable; this "
                    "preflight is partial. Verify anything missing manually."
                ),
            }
        )

    return {
        "location": {"lat": lat, "lon": lon, "airport_ref": airport},
        "airspace_class": cls,
        "laanc_likely_required": laanc,
        "controlling_facility": airspace.get("controlling_facility"),
        "tfrs": active,
        "weather": {
            "conditions": weather if weather_ok else {"error": weather.get("error")},
            "metar": metar if metar_ok else {"error": metar.get("error")},
            "flight_category": flt_cat or None,
        },
        "advisories": advisories,
        "degraded": degraded,
        "disclaimer": (
            "Operator-facing pre-flight awareness only. DroneOpsCommand is not "
            "an FAA-approved USS and does not grant authorization. Airspace and "
            "flight legality are the certificated operator's determination."
        ),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
