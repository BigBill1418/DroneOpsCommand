import asyncio
import io
import logging
import os
import uuid as uuid_mod
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, status
from PIL import Image as PILImage
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.jwt import get_current_user
from app.config import settings
from app.database import get_db
from app.models.mission import Mission, MissionFlight, MissionImage
from app.models.user import User
from app.schemas.mission import (
    MissionCreate,
    MissionFlightCreate,
    MissionFlightResponse,
    MissionImageResponse,
    MissionResponse,
    MissionUpdate,
)
from app.services.opendronelog import opendronelog_client

logger = logging.getLogger("doc.missions")

router = APIRouter(prefix="/api/missions", tags=["missions"])


@router.get("", response_model=list[MissionResponse])
async def list_missions(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(Mission)
        .order_by(Mission.created_at.desc())
        .options(
            selectinload(Mission.flights),
            selectinload(Mission.images),
            selectinload(Mission.customer),
        )
    )
    return result.scalars().all()


@router.post("", response_model=MissionResponse, status_code=status.HTTP_201_CREATED)
async def create_mission(
    data: MissionCreate,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    try:
        fields = data.model_dump(exclude_none=True)
        # Strip timezone from expires_at — DB column is TIMESTAMP WITHOUT TIME ZONE
        if isinstance(fields.get("download_link_expires_at"), datetime):
            fields["download_link_expires_at"] = fields["download_link_expires_at"].replace(tzinfo=None)
        mission = Mission(**fields)
        db.add(mission)
        await db.flush()
        # Re-query with explicit eager loads so relationships are populated for response
        result = await db.execute(
            select(Mission).where(Mission.id == mission.id).options(
                selectinload(Mission.flights),
                selectinload(Mission.images),
                selectinload(Mission.customer),
            )
        )
        mission = result.scalar_one()
        return mission
    except Exception as exc:
        logger.exception("Failed to create mission: %s", exc)
        raise HTTPException(status_code=500, detail="An internal error occurred")


@router.get("/{mission_id}", response_model=MissionResponse)
async def get_mission(
    mission_id: UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(Mission).where(Mission.id == mission_id).options(
            selectinload(Mission.flights),
            selectinload(Mission.images),
            selectinload(Mission.customer),
        )
    )
    mission = result.scalar_one_or_none()
    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")
    return mission


@router.put("/{mission_id}", response_model=MissionResponse)
async def update_mission(
    mission_id: UUID,
    data: MissionUpdate,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    result = await db.execute(select(Mission).where(Mission.id == mission_id))
    mission = result.scalar_one_or_none()
    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")

    try:
        for key, value in data.model_dump(exclude_unset=True).items():
            # Strip timezone from expires_at — DB column is TIMESTAMP WITHOUT TIME ZONE
            if key == "download_link_expires_at" and isinstance(value, datetime):
                value = value.replace(tzinfo=None)
            setattr(mission, key, value)

        await db.flush()
        # Re-query with explicit eager loads so relationships are populated for response
        result = await db.execute(
            select(Mission).where(Mission.id == mission_id).options(
                selectinload(Mission.flights),
                selectinload(Mission.images),
                selectinload(Mission.customer),
            )
        )
        mission = result.scalar_one()
        return mission
    except Exception as exc:
        logger.exception("Failed to update mission: %s", exc)
        raise HTTPException(status_code=500, detail="An internal error occurred")


@router.delete("/{mission_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_mission(
    mission_id: UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    result = await db.execute(select(Mission).where(Mission.id == mission_id))
    mission = result.scalar_one_or_none()
    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")
    await db.delete(mission)


# --- Mission Flights ---

@router.post("/{mission_id}/flights", response_model=MissionFlightResponse, status_code=status.HTTP_201_CREATED)
async def add_flight(
    mission_id: UUID,
    data: MissionFlightCreate,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    result = await db.execute(select(Mission).where(Mission.id == mission_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Mission not found")

    # If linking a local flight, populate cache from Flight record
    if data.flight_id:
        from app.models.flight import Flight as FlightModel
        flt_result = await db.execute(select(FlightModel).where(FlightModel.id == data.flight_id))
        local_flight = flt_result.scalar_one_or_none()
        if local_flight:
            cache = data.flight_data_cache or {}
            cache.update({
                "id": str(local_flight.id),
                "name": local_flight.name,
                "display_name": local_flight.name,
                "drone_model": local_flight.drone_model,
                "drone_serial": local_flight.drone_serial,
                "start_time": local_flight.start_time.isoformat() if local_flight.start_time else None,
                "duration_secs": local_flight.duration_secs,
                "total_distance": local_flight.total_distance,
                "max_altitude": local_flight.max_altitude,
                "max_speed": local_flight.max_speed,
                "home_lat": local_flight.home_lat,
                "home_lon": local_flight.home_lon,
                "point_count": local_flight.point_count,
                "track": local_flight.gps_track or [],
            })
            data.flight_data_cache = cache
    else:
        # Legacy: enrich flight_data_cache with GPS track from OpenDroneLog
        cache = data.flight_data_cache or {}
        has_track = any(
            key in cache and isinstance(cache[key], list) and len(cache[key]) > 0
            for key in ("track", "gps_data", "coordinates")
        )
        if not has_track and data.opendronelog_flight_id:
            try:
                track = await opendronelog_client.get_flight_track(
                    data.opendronelog_flight_id, db
                )
                if track:
                    cache["track"] = track
                    data.flight_data_cache = cache
            except Exception as exc:
                logger.warning(
                    "Could not fetch GPS track for flight %s: %s",
                    data.opendronelog_flight_id,
                    exc,
                )

    flight = MissionFlight(mission_id=mission_id, **data.model_dump())
    db.add(flight)
    await db.flush()
    await db.refresh(flight)
    return flight


@router.put("/{mission_id}/flights/{flight_id}", response_model=MissionFlightResponse)
async def update_flight(
    mission_id: UUID,
    flight_id: UUID,
    data: MissionFlightCreate,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(MissionFlight).where(
            MissionFlight.id == flight_id, MissionFlight.mission_id == mission_id
        )
    )
    flight = result.scalar_one_or_none()
    if not flight:
        raise HTTPException(status_code=404, detail="Flight not found")

    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(flight, key, value)

    await db.flush()
    await db.refresh(flight)
    return flight


@router.patch("/{mission_id}/flights/{flight_id}/aircraft")
async def assign_aircraft(
    mission_id: UUID,
    flight_id: UUID,
    data: dict,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Lightweight endpoint to assign/unassign an aircraft to a flight."""
    result = await db.execute(
        select(MissionFlight).where(
            MissionFlight.id == flight_id, MissionFlight.mission_id == mission_id
        )
    )
    flight = result.scalar_one_or_none()
    if not flight:
        raise HTTPException(status_code=404, detail="Flight not found")

    aircraft_id = data.get("aircraft_id")
    flight.aircraft_id = UUID(aircraft_id) if aircraft_id else None
    await db.flush()
    await db.refresh(flight)
    return {"id": str(flight.id), "aircraft_id": str(flight.aircraft_id) if flight.aircraft_id else None}


@router.delete("/{mission_id}/flights/{flight_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_flight(
    mission_id: UUID,
    flight_id: UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(MissionFlight).where(
            MissionFlight.id == flight_id, MissionFlight.mission_id == mission_id
        )
    )
    flight = result.scalar_one_or_none()
    if not flight:
        raise HTTPException(status_code=404, detail="Flight not found")
    await db.delete(flight)


# --- Mission Images ---

MAX_IMAGE_DIMENSION = 1920  # Max width or height for report images


def _write_file(path: str, content: bytes):
    """Write bytes to disk (runs in executor to avoid blocking)."""
    with open(path, "wb") as f:
        f.write(content)


def _resize_image(content: bytes, max_dim: int = MAX_IMAGE_DIMENSION) -> tuple[bytes, str]:
    """Resize image if it exceeds max dimensions. Returns (bytes, extension)."""
    try:
        img = PILImage.open(io.BytesIO(content))
        # Preserve orientation from EXIF
        try:
            from PIL import ImageOps
            img = ImageOps.exif_transpose(img)
        except Exception:
            pass

        if img.width > max_dim or img.height > max_dim:
            img.thumbnail((max_dim, max_dim), PILImage.LANCZOS)

        # Save as JPEG for consistency and smaller file size
        buf = io.BytesIO()
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        img.save(buf, format="JPEG", quality=82, optimize=True)
        return buf.getvalue(), ".jpg"
    except Exception as exc:
        logger.warning("Image resize failed, using original: %s", exc)
        return content, ""


@router.post("/{mission_id}/images", response_model=MissionImageResponse, status_code=status.HTTP_201_CREATED)
async def upload_image(
    mission_id: UUID,
    file: UploadFile = File(...),
    caption: str = Form(default=""),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    result = await db.execute(select(Mission).where(Mission.id == mission_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Mission not found")

    # Save file
    upload_dir = os.path.join(settings.upload_dir, str(mission_id))
    os.makedirs(upload_dir, exist_ok=True)

    content = await file.read()
    if len(content) > 50_000_000:
        raise HTTPException(status_code=413, detail="Image too large (50MB max)")
    ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/tiff"}
    if file.content_type and file.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(status_code=400, detail="Only JPEG, PNG, WebP, and TIFF images are allowed")
    logger.info("Image upload for mission %s: %s (%d bytes)", mission_id, file.filename, len(content))

    # Resize large images in thread executor to avoid blocking
    loop = asyncio.get_running_loop()
    resized_content, forced_ext = await loop.run_in_executor(None, _resize_image, content)
    ext = forced_ext or (os.path.splitext(file.filename)[1] if file.filename else ".jpg")
    filename = f"{uuid_mod.uuid4()}{ext}"
    file_path = os.path.join(upload_dir, filename)

    await loop.run_in_executor(None, _write_file, file_path, resized_content)

    # Get sort order using COUNT instead of loading all images
    count_result = await db.execute(
        select(func.count()).select_from(MissionImage).where(MissionImage.mission_id == mission_id)
    )
    sort_order = count_result.scalar() or 0

    image = MissionImage(
        mission_id=mission_id,
        file_path=file_path,
        caption=caption or None,
        sort_order=sort_order,
    )
    db.add(image)
    await db.flush()
    await db.refresh(image)
    logger.info("Image saved for mission %s: %s", mission_id, file_path)
    return image


@router.delete("/{mission_id}/images/{image_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_image(
    mission_id: UUID,
    image_id: UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(MissionImage).where(
            MissionImage.id == image_id, MissionImage.mission_id == mission_id
        )
    )
    image = result.scalar_one_or_none()
    if not image:
        raise HTTPException(status_code=404, detail="Image not found")

    if os.path.exists(image.file_path):
        os.remove(image.file_path)
    await db.delete(image)
