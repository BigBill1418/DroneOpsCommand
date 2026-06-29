"""Physical-plausibility metrics for flight distance (ADR-0028, C1).

A single GPS teleport poisons a naive consecutive-point haversine sum: the
real OpenDroneLog row ``f57c9373`` reports ``total_distance = 12,583,855 m``
over 554 s (an implied 22,722 m/s — orbital) while its own stored track, summed
with an outlier gate, is only ``3,603.9 m`` (6.5 m/s average vs a 29.1 m/s max —
physically consistent).

This module is the Python twin of the Rust parser gate
(``flight-parser/src/gate.rs``). The thresholds and segment logic are kept
deliberately identical so the parser, the OpenDroneLog ingest clamp
(``import_from_opendronelog``) and the data-repair migration all agree on what
"physically impossible" means.
"""

from __future__ import annotations

import math
from datetime import datetime

# Hard physical upper bound on horizontal ground speed (m/s). 60 m/s ≈ 134 mph,
# far above any consumer/prosumer multirotor max (~30 m/s), so a real fast pass
# is never gated while a teleport (thousands of m/s) always is.
MAX_SEGMENT_SPEED_MS = 60.0

# Fallback bound (metres) when a segment has no usable Δt.
MAX_SEGMENT_DISTANCE_M = 500.0

# An ODL passthrough distance whose implied average speed exceeds this multiple
# of the reported max speed is physically impossible (an average can never
# exceed the maximum) and is treated as corrupt.
ODL_IMPLAUSIBLE_RATIO = 3.0

_EARTH_RADIUS_M = 6_371_000.0

_LAT_KEYS = ("lat", "latitude", "Lat", "Latitude")
_LNG_KEYS = ("lng", "lon", "long", "longitude", "Lng", "Lon", "Longitude")
_TS_KEYS = ("timestamp", "time", "datetime", "ts")

_TS_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y/%m/%d %H:%M:%S",
)


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres between two WGS84 points."""
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    )
    return _EARTH_RADIUS_M * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def segment_ok(dist_m: float, dt_secs: float | None) -> bool:
    """Per-segment physical-plausibility test (mirror of Rust ``gate::segment_ok``)."""
    if dt_secs is not None and dt_secs > 0.0:
        return dist_m / dt_secs <= MAX_SEGMENT_SPEED_MS
    return dist_m <= MAX_SEGMENT_DISTANCE_M


def _point_lat_lng(pt: object) -> tuple[float, float] | None:
    """Extract (lat, lng) from a dict point or a ``[lng, lat, ...]`` / ``[lat, lng]`` pair."""
    if isinstance(pt, dict):
        lat = next((pt[k] for k in _LAT_KEYS if k in pt and pt[k] is not None), None)
        lng = next((pt[k] for k in _LNG_KEYS if k in pt and pt[k] is not None), None)
        if lat is None or lng is None:
            return None
        try:
            return float(lat), float(lng)
        except (TypeError, ValueError):
            return None
    if isinstance(pt, (list, tuple)) and len(pt) >= 2:
        # ODL raw track is [lon, lat, alt]; a [lat, lng] pair is also accepted.
        # We can't always disambiguate, but distance is order-symmetric for the
        # gate's purposes, so prefer the ODL [lon, lat] convention.
        try:
            return float(pt[1]), float(pt[0])
        except (TypeError, ValueError):
            return None
    return None


def _point_ts(pt: object) -> datetime | None:
    if not isinstance(pt, dict):
        return None
    raw = next((pt[k] for k in _TS_KEYS if k in pt and pt[k]), None)
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw
    s = str(raw).strip()
    if s.endswith("Z"):
        s = s[:-1]
    for fmt in _TS_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def gated_track_distance(track: object) -> tuple[float, int]:
    """Sum a GPS track's path length with the C1 outlier gate.

    Returns ``(distance_m, dropped_segments)``. Points missing coordinates are
    skipped. A segment is summed only when ``segment_ok`` accepts it — so a
    teleport (a stray point thousands of km away, including a null-island
    ``(0,0)`` fix mid-track) is excluded rather than poisoning the total. No
    null-island *prefilter* is applied: home-relative tracks legitimately sit
    near the origin and their inter-point path length is valid; only the
    *segment* gate decides what counts.
    """
    if not isinstance(track, (list, tuple)) or len(track) < 2:
        return 0.0, 0
    total = 0.0
    dropped = 0
    prev_ll: tuple[float, float] | None = None
    prev_ts: datetime | None = None
    for pt in track:
        ll = _point_lat_lng(pt)
        if ll is None:
            continue
        ts = _point_ts(pt)
        if prev_ll is not None:
            d = haversine_m(prev_ll[0], prev_ll[1], ll[0], ll[1])
            dt = None
            if prev_ts is not None and ts is not None:
                dt = (ts - prev_ts).total_seconds()
            if segment_ok(d, dt):
                total += d
            else:
                dropped += 1
        prev_ll = ll
        prev_ts = ts
    return total, dropped


def odl_distance_is_implausible(
    total_distance: float, duration_secs: float, max_speed: float
) -> bool:
    """True when an ODL passthrough distance is physically impossible.

    Primary test: implied average speed exceeds ``ODL_IMPLAUSIBLE_RATIO ×`` the
    reported max speed (an average can never exceed the max). When the reported
    max speed is missing/zero, fall back to the absolute speed cap so a huge
    distance with no max-speed reference is still caught.
    """
    if duration_secs <= 0 or total_distance <= 0:
        return False
    implied_avg = total_distance / duration_secs
    if max_speed and max_speed > 0:
        return implied_avg > ODL_IMPLAUSIBLE_RATIO * max_speed
    return implied_avg > MAX_SEGMENT_SPEED_MS


def sanitize_odl_distance(
    total_distance: float,
    duration_secs: float,
    max_speed: float,
    track: object,
) -> tuple[float, dict | None]:
    """Return a physically-plausible distance for an ODL flight + a repair note.

    If the reported distance is plausible, returns ``(total_distance, None)``.
    Otherwise recomputes from the track with the outlier gate; if that yields a
    usable, plausible value it is used (``method='track_recompute'``), else the
    distance is clamped to ``max_speed × duration`` (``method='speed_clamp'``).
    The returned dict is suitable for stashing in ``raw_metadata`` as an audit
    trail. ``None`` track / empty track still produces a clamp.
    """
    if not odl_distance_is_implausible(total_distance, duration_secs, max_speed):
        return total_distance, None

    recomputed, dropped = gated_track_distance(track)
    note: dict = {
        "original_distance_m": total_distance,
        "duration_secs": duration_secs,
        "max_speed_ms": max_speed,
    }
    cap = (max_speed if max_speed and max_speed > 0 else MAX_SEGMENT_SPEED_MS) * duration_secs
    if recomputed > 0 and recomputed <= cap * 1.05:
        note.update(method="track_recompute", dropped_segments=dropped,
                    sanitized_distance_m=recomputed)
        return recomputed, note
    note.update(method="speed_clamp", dropped_segments=dropped,
                sanitized_distance_m=cap)
    return cap, note
