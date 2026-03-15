from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import get_current_user
from app.database import get_db
from app.models.mission import Mission
from app.models.user import User
from app.services.map_renderer import (
    calculate_area_acres,
    extract_gps_tracks,
    generate_map_geojson,
    render_static_map,
)

router = APIRouter(prefix="/api/missions", tags=["maps"])


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
    result = await db.execute(select(Mission).where(Mission.id == mission_id))
    mission = result.scalar_one_or_none()
    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")

    flights = _flights_to_dicts(mission)
    return generate_map_geojson(flights)


@router.get("/{mission_id}/map/coverage")
async def get_coverage(
    mission_id: UUID,
    buffer_meters: float = 30.0,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Calculate area coverage in acres."""
    result = await db.execute(select(Mission).where(Mission.id == mission_id))
    mission = result.scalar_one_or_none()
    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")

    flights = _flights_to_dicts(mission)
    tracks = extract_gps_tracks(flights)
    acres = calculate_area_acres(tracks, buffer_meters=buffer_meters)

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
    result = await db.execute(select(Mission).where(Mission.id == mission_id))
    mission = result.scalar_one_or_none()
    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")

    flights = _flights_to_dicts(mission)
    map_path = render_static_map(flights)

    return {"map_path": map_path}
