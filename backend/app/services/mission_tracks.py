"""Bounded, on-demand GPS-track loading for mission map/report paths (ADR-0025).

Root-cause context (same OOM family as ADR-0019/0020): every full-mission
read used to materialize the entire GPS track for every attached flight at
once — O(N_flights × ~19k points). For a large mission ("savannah": hundreds
of flights) that decompressed past the 1536 MiB backend cgroup cap and the
kernel OOM-killed uvicorn mid-response.

ADR-0025 stops duplicating the track into ``MissionFlight.flight_data_cache``
at attach time (native flights store scalars only). The authoritative track
now lives once on ``Flight.gps_track`` and is loaded ON DEMAND, one flight at
a time, only by the paths that actually draw or measure it (report + map).

``load_bounded_flight_tracks`` is that loader. It NEVER holds more than one
raw track in memory simultaneously: per flight it pulls the raw track,
immediately decimates it to a bounded vertex cap (the same
``MAX_RENDER_VERTICES_PER_TRACK`` already validated for rendering — see
ADR-0020), and discards the raw points before moving on. Peak memory is
therefore O(one raw track + N × cap) instead of O(N × raw points).

Track source resolution, in priority order, per flight:
  1. The cache itself, IF it already carries a track (legacy native rows
     attached before ADR-0025, and legacy-ODL rows that have no ``Flight``
     row and store their ODL track in the cache as the only copy).
  2. ``Flight.gps_track`` via ``MissionFlight.flight_id`` (the modern native
     path) — fetched as a single deferred-safe column read per flight.

The returned dicts match the shape the map services already consume
(``extract_gps_tracks`` / ``generate_map_geojson`` / ``render_static_map``),
so the pure-geometry layer is unchanged.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.flight import Flight
from app.models.mission import Mission
from app.services.map_renderer import (
    MAX_RENDER_VERTICES_PER_TRACK,
    _decimate,
    extract_gps_tracks,
)

logger = logging.getLogger("doc.mission_tracks")

# Cache keys under which a raw GPS track may be stored (legacy / ODL rows).
_TRACK_KEYS = ("track", "gps_data", "coordinates")


def _cache_has_track(cache: dict | None) -> bool:
    if not cache:
        return False
    return any(
        isinstance(cache.get(k), list) and len(cache.get(k)) > 0 for k in _TRACK_KEYS
    )


def _decimate_raw_track(raw: list) -> list:
    """Parse a raw track into (lat, lng) tuples and cap the vertex count.

    Reuses ``extract_gps_tracks`` for the robust multi-format point parser
    (lat/lng/lon/latitude/longitude dict shapes and [lat, lng] pairs), then
    decimates to the bounded cap so a single pathological track can never
    blow memory.
    """
    parsed = extract_gps_tracks([{"flight_data_cache": {"track": raw}}])
    if not parsed:
        return []
    return _decimate(parsed[0], MAX_RENDER_VERTICES_PER_TRACK)


async def load_bounded_flight_tracks(db: AsyncSession, mission: Mission) -> list[dict]:
    """Load each mission flight's track on demand, decimated and bounded.

    ``mission.flights`` must already be loaded with their ``aircraft``
    relationship (the map/report loaders do this). This function does NOT
    touch ``MissionFlight.flight`` (the relationship) — it reads the single
    ``Flight.gps_track`` column by ``flight_id`` so it never drags telemetry
    / raw_metadata along.

    Returns a list of map-service flight dicts (one per flight that yielded
    a non-empty track), each carrying a decimated ``flight_data_cache.track``
    plus the aircraft + ODL id the geometry layer reads.
    """
    flights: list[dict] = []
    raw_total = 0
    kept_total = 0

    for mf in mission.flights:
        cache = mf.flight_data_cache or {}
        raw: list | None = None

        if _cache_has_track(cache):
            for k in _TRACK_KEYS:
                val = cache.get(k)
                if isinstance(val, list) and val:
                    raw = val
                    break
        elif mf.flight_id is not None:
            # One small column read; the raw track lives only for this
            # iteration and is decimated immediately below.
            res = await db.execute(
                select(Flight.gps_track).where(Flight.id == mf.flight_id)
            )
            raw = res.scalar_one_or_none()

        if not raw:
            continue

        raw_total += len(raw)
        track = _decimate_raw_track(raw)
        # Drop the raw reference promptly so a long mission doesn't accumulate
        # the full-resolution tracks across iterations.
        raw = None
        if not track:
            continue
        kept_total += len(track)

        flight_dict: dict = {
            "opendronelog_flight_id": mf.opendronelog_flight_id,
            "flight_data_cache": {"track": track},
        }
        if mf.aircraft:
            flight_dict["aircraft"] = {
                "model_name": mf.aircraft.model_name,
                "manufacturer": mf.aircraft.manufacturer,
            }
        flights.append(flight_dict)

    logger.info(
        "Bounded track load for mission %s: %d flights with tracks, "
        "%d raw points decimated to %d (cap %d/track)",
        getattr(mission, "id", "?"),
        len(flights),
        raw_total,
        kept_total,
        MAX_RENDER_VERTICES_PER_TRACK,
    )
    return flights
