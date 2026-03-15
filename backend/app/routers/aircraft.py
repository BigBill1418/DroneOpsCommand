from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import get_current_user
from app.database import get_db
from app.models.aircraft import Aircraft
from app.models.user import User
from app.schemas.aircraft import AircraftCreate, AircraftResponse, AircraftUpdate

router = APIRouter(prefix="/api/aircraft", tags=["aircraft"])


@router.get("", response_model=list[AircraftResponse])
async def list_aircraft(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    result = await db.execute(select(Aircraft).order_by(Aircraft.model_name))
    return result.scalars().all()


@router.post("", response_model=AircraftResponse, status_code=status.HTTP_201_CREATED)
async def create_aircraft(
    data: AircraftCreate,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    aircraft = Aircraft(**data.model_dump())
    db.add(aircraft)
    await db.flush()
    await db.refresh(aircraft)
    return aircraft


@router.get("/{aircraft_id}", response_model=AircraftResponse)
async def get_aircraft(
    aircraft_id: UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    result = await db.execute(select(Aircraft).where(Aircraft.id == aircraft_id))
    aircraft = result.scalar_one_or_none()
    if not aircraft:
        raise HTTPException(status_code=404, detail="Aircraft not found")
    return aircraft


@router.put("/{aircraft_id}", response_model=AircraftResponse)
async def update_aircraft(
    aircraft_id: UUID,
    data: AircraftUpdate,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    result = await db.execute(select(Aircraft).where(Aircraft.id == aircraft_id))
    aircraft = result.scalar_one_or_none()
    if not aircraft:
        raise HTTPException(status_code=404, detail="Aircraft not found")

    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(aircraft, key, value)

    await db.flush()
    await db.refresh(aircraft)
    return aircraft


@router.delete("/{aircraft_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_aircraft(
    aircraft_id: UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    result = await db.execute(select(Aircraft).where(Aircraft.id == aircraft_id))
    aircraft = result.scalar_one_or_none()
    if not aircraft:
        raise HTTPException(status_code=404, detail="Aircraft not found")
    await db.delete(aircraft)
