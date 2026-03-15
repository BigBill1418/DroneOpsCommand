from fastapi import APIRouter, Depends, HTTPException

from app.auth.jwt import get_current_user
from app.models.user import User
from app.services.opendronelog import opendronelog_client

router = APIRouter(prefix="/api/flights", tags=["flights"])


@router.get("")
async def list_flights(_user: User = Depends(get_current_user)):
    """List all flights from OpenDroneLog."""
    if not opendronelog_client.configured:
        raise HTTPException(status_code=400, detail="OpenDroneLog URL not configured")
    try:
        return await opendronelog_client.list_flights()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to reach OpenDroneLog: {str(e)}")


@router.get("/{flight_id}")
async def get_flight(flight_id: str, _user: User = Depends(get_current_user)):
    """Get detailed flight data from OpenDroneLog."""
    if not opendronelog_client.configured:
        raise HTTPException(status_code=400, detail="OpenDroneLog URL not configured")
    try:
        return await opendronelog_client.get_flight(flight_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to reach OpenDroneLog: {str(e)}")


@router.get("/{flight_id}/track")
async def get_flight_track(flight_id: str, _user: User = Depends(get_current_user)):
    """Get GPS track data for a flight."""
    if not opendronelog_client.configured:
        raise HTTPException(status_code=400, detail="OpenDroneLog URL not configured")
    try:
        return await opendronelog_client.get_flight_track(flight_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to reach OpenDroneLog: {str(e)}")
