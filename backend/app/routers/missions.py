import io
import os
import uuid as uuid_mod
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, status
from PIL import Image as PILImage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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

router = APIRouter(prefix="/api/missions", tags=["missions"])


@router.get("", response_model=list[MissionResponse])
async def list_missions(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    result = await db.execute(select(Mission).order_by(Mission.created_at.desc()))
    return result.scalars().all()


@router.post("", response_model=MissionResponse, status_code=status.HTTP_201_CREATED)
async def create_mission(
    data: MissionCreate,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    mission = Mission(**data.model_dump())
    db.add(mission)
    await db.flush()
    await db.refresh(mission)
    return mission


@router.get("/{mission_id}", response_model=MissionResponse)
async def get_mission(
    mission_id: UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    result = await db.execute(select(Mission).where(Mission.id == mission_id))
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

    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(mission, key, value)

    await db.flush()
    await db.refresh(mission)
    return mission


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
    except Exception:
        # If we can't process it, return original
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

    # Resize large images for report use
    resized_content, forced_ext = _resize_image(content)
    ext = forced_ext or (os.path.splitext(file.filename)[1] if file.filename else ".jpg")
    filename = f"{uuid_mod.uuid4()}{ext}"
    file_path = os.path.join(upload_dir, filename)

    with open(file_path, "wb") as f:
        f.write(resized_content)

    # Get sort order
    count_result = await db.execute(
        select(MissionImage).where(MissionImage.mission_id == mission_id)
    )
    sort_order = len(count_result.scalars().all())

    image = MissionImage(
        mission_id=mission_id,
        file_path=file_path,
        caption=caption or None,
        sort_order=sort_order,
    )
    db.add(image)
    await db.flush()
    await db.refresh(image)
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
