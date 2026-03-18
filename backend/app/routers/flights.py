import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import get_current_user
from app.database import get_db
from app.models.user import User
from app.services.opendronelog import opendronelog_client

logger = logging.getLogger("doc.flights")

router = APIRouter(prefix="/api/flights", tags=["flights"])


@router.get("")
async def list_flights(
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all flights from OpenDroneLog."""
    if not await opendronelog_client.is_configured(db):
        raise HTTPException(status_code=400, detail="OpenDroneLog URL not configured. Set it in Settings.")
    try:
        return await opendronelog_client.list_flights(db)
    except ConnectionError as e:
        logger.error("Failed to reach flight data service: %s", e)
        raise HTTPException(status_code=502, detail="Failed to reach flight data service")
    except Exception as e:
        logger.error("Failed to reach flight data service: %s", e)
        raise HTTPException(status_code=502, detail="Failed to reach flight data service")


@router.get("/test")
async def test_connection(
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Test connection to OpenDroneLog."""
    return await opendronelog_client.test_connection(db)


@router.get("/{flight_id}")
async def get_flight(
    flight_id: str,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get detailed flight data from OpenDroneLog."""
    if not await opendronelog_client.is_configured(db):
        raise HTTPException(status_code=400, detail="OpenDroneLog URL not configured")
    try:
        return await opendronelog_client.get_flight(flight_id, db)
    except Exception as e:
        logger.error("Failed to reach flight data service for flight %s: %s", flight_id, e)
        raise HTTPException(status_code=502, detail="Failed to reach flight data service")


@router.get("/{flight_id}/track")
async def get_flight_track(
    flight_id: str,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get GPS track data for a flight."""
    if not await opendronelog_client.is_configured(db):
        raise HTTPException(status_code=400, detail="OpenDroneLog URL not configured")
    try:
        return await opendronelog_client.get_flight_track(flight_id, db)
    except Exception as e:
        logger.error("Failed to reach flight data service for flight track %s: %s", flight_id, e)
        raise HTTPException(status_code=502, detail="Failed to reach flight data service")
