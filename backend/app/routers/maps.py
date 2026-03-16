import asyncio
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
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Get GeoJSON data for interactive flight path map."""
    mission = await _load_mission(db, mission_id)
    flights = _flights_to_dicts(mission)
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, generate_map_geojson, flights)


@router.get("/{mission_id}/map/coverage")
async def get_coverage(
    mission_id: UUID,
    buffer_meters: float = 30.0,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Calculate area coverage in acres."""
    mission = await _load_mission(db, mission_id)
    flights = _flights_to_dicts(mission)

    loop = asyncio.get_event_loop()
    tracks = await loop.run_in_executor(None, extract_gps_tracks, flights)
    acres = await loop.run_in_executor(
        None, partial(calculate_area_acres, tracks, buffer_meters=buffer_meters)
    )

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
    mission = await _load_mission(db, mission_id)
    flights = _flights_to_dicts(mission)
    loop = asyncio.get_event_loop()
    map_path = await loop.run_in_executor(None, render_static_map, flights)

    return {"map_path": map_path}
