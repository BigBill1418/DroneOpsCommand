import asyncio
import logging
import time
from functools import partial
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.jwt import get_current_user
from app.database import get_db
from app.models.mission import Mission, MissionFlight
from app.models.user import User
from app.services.map_renderer import (
    calculate_area_acres,
    extract_gps_tracks,
    generate_map_geojson,
    render_static_map,
)

logger = logging.getLogger("doc.maps")

router = APIRouter(prefix="/api/missions", tags=["maps"])


async def _load_mission(db: AsyncSession, mission_id: UUID) -> Mission:
    """Load a mission with flights and aircraft eagerly loaded."""
    result = await db.execute(
        select(Mission)
        .where(Mission.id == mission_id)
        .options(selectinload(Mission.flights).selectinload(MissionFlight.aircraft))
    )
    mission = result.scalar_one_or_none()
    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")
    return mission


def _flights_to_dicts(mission: Mission) -> list[dict]:
    """Convert mission flights to dict format for map services."""
    flights = []
    for f in mission.flights:
        flight_dict = {
            "opendronelog_flight_id": f.opendronelog_flight_id,
            "flight_data_cache": f.flight_data_cache,
        }
        if f.aircraft:
            flight_dict["aircraft"] = {
                "model_name": f.aircraft.model_name,
                "manufacturer": f.aircraft.manufacturer,
            }
        flights.append(flight_dict)
    return flights


@router.get("/{mission_id}/map")
async def get_map_data(
    mission_id: UUID,
    include_coverage: bool = False,
    buffer_meters: float = 30.0,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Get GeoJSON data for interactive flight path map.

    Pass ?include_coverage=true to also return area coverage in the same
    response, avoiding a second round-trip and redundant DB/CPU work.
    """
    start = time.perf_counter()
    mission = await _load_mission(db, mission_id)
    flights = _flights_to_dicts(mission)
    loop = asyncio.get_running_loop()

    try:
        if include_coverage:
            # Run GeoJSON and coverage extraction in parallel threads
            geojson_fut = loop.run_in_executor(None, generate_map_geojson, flights)
            tracks_fut = loop.run_in_executor(None, extract_gps_tracks, flights)
            geojson_result, tracks = await asyncio.gather(geojson_fut, tracks_fut)
            acres = await loop.run_in_executor(
                None, partial(calculate_area_acres, tracks, buffer_meters=buffer_meters)
            )
            logger.info("Map+coverage for mission %s: %.2f acres (%.2fs)",
                        mission_id, acres, time.perf_counter() - start)
            return {
                "geojson": geojson_result,
                "coverage": {
                    "acres": round(acres, 2),
                    "square_yards": round(acres * 4840, 0) if acres < 1 else None,
                    "num_flights": len(tracks),
                    "total_points": sum(len(t) for t in tracks),
                },
            }
        else:
            result = await loop.run_in_executor(None, generate_map_geojson, flights)
            logger.info("Map GeoJSON for mission %s: %.2fs", mission_id, time.perf_counter() - start)
            return result
    except Exception as exc:
        logger.error("Map GeoJSON failed for mission %s: %s", mission_id, exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Map generation failed")


@router.get("/{mission_id}/map/coverage")
async def get_coverage(
    mission_id: UUID,
    buffer_meters: float = 30.0,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Calculate area coverage in acres."""
    start = time.perf_counter()
    mission = await _load_mission(db, mission_id)
    flights = _flights_to_dicts(mission)

    loop = asyncio.get_running_loop()
    try:
        tracks = await loop.run_in_executor(None, extract_gps_tracks, flights)
        acres = await loop.run_in_executor(
            None, partial(calculate_area_acres, tracks, buffer_meters=buffer_meters)
        )
    except Exception as exc:
        logger.error("Coverage calculation failed for mission %s: %s", mission_id, exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Coverage calculation failed")

    logger.info("Coverage for mission %s: %.2f acres, %d tracks (%.2fs)",
                mission_id, acres, len(tracks), time.perf_counter() - start)

    return {
        "acres": round(acres, 2),
        "square_yards": round(acres * 4840, 0) if acres < 1 else None,
        "num_flights": len(tracks),
        "total_points": sum(len(t) for t in tracks),
    }


@router.post("/{mission_id}/map/render")
async def render_map(
    mission_id: UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Render a static map image for PDF inclusion."""
    start = time.perf_counter()
    mission = await _load_mission(db, mission_id)
    flights = _flights_to_dicts(mission)
    loop = asyncio.get_running_loop()
    try:
        map_path = await loop.run_in_executor(None, render_static_map, flights)
        logger.info("Map rendered for mission %s: %s (%.2fs)",
                     mission_id, map_path, time.perf_counter() - start)
    except Exception as exc:
        logger.error("Map render failed for mission %s: %s", mission_id, exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Map render failed")

    return {"map_path": map_path}
