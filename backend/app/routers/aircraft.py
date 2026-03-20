import asyncio
import io
import logging
import os
import uuid as uuid_mod
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, status
from PIL import Image as PILImage
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import get_current_user
from app.config import settings
from app.database import get_db
from app.models.aircraft import Aircraft
from app.models.battery import Battery
from app.models.user import User
from app.schemas.aircraft import AircraftCreate, AircraftResponse, AircraftUpdate

logger = logging.getLogger("doc.aircraft")

router = APIRouter(prefix="/api/aircraft", tags=["aircraft"])

MAX_AIRCRAFT_IMAGE_DIMENSION = 800
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_IMAGE_SIZE = 10_000_000  # 10 MB


def _write_file(path: str, content: bytes):
    """Write bytes to disk (runs in executor to avoid blocking)."""
    with open(path, "wb") as f:
        f.write(content)


def _resize_image(content: bytes, max_dim: int = MAX_AIRCRAFT_IMAGE_DIMENSION) -> tuple[bytes, str]:
    """Resize image if it exceeds max dimensions. Returns (bytes, extension)."""
    try:
        img = PILImage.open(io.BytesIO(content))
        try:
            from PIL import ImageOps
            img = ImageOps.exif_transpose(img)
        except Exception:
            pass

        if img.width > max_dim or img.height > max_dim:
            img.thumbnail((max_dim, max_dim), PILImage.LANCZOS)

        buf = io.BytesIO()
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        img.save(buf, format="JPEG", quality=82, optimize=True)
        return buf.getvalue(), ".jpg"
    except Exception as exc:
        logger.warning("Aircraft image resize failed, using original: %s", exc)
        return content, ""


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

    old_model_name = aircraft.model_name
    updates = data.model_dump(exclude_unset=True)

    for key, value in updates.items():
        setattr(aircraft, key, value)

    # Cascade: if model_name changed, update all batteries referencing this aircraft
    new_model_name = updates.get("model_name")
    if new_model_name and new_model_name != old_model_name:
        await db.execute(
            update(Battery)
            .where(Battery.aircraft_id == aircraft_id)
            .values(model=new_model_name)
        )
        # Also update batteries that had the old model name string (no FK link)
        await db.execute(
            update(Battery)
            .where(Battery.model == old_model_name, Battery.aircraft_id.is_(None))
            .values(model=new_model_name)
        )

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
    # Clean up image file if one exists
    if aircraft.image_filename:
        try:
            old_path = os.path.join(settings.upload_dir, aircraft.image_filename)
            os.remove(old_path)
        except OSError:
            pass
    await db.delete(aircraft)


@router.post("/{aircraft_id}/image", response_model=AircraftResponse)
async def upload_aircraft_image(
    aircraft_id: UUID,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    result = await db.execute(select(Aircraft).where(Aircraft.id == aircraft_id))
    aircraft = result.scalar_one_or_none()
    if not aircraft:
        raise HTTPException(status_code=404, detail="Aircraft not found")

    content = await file.read()
    if len(content) > MAX_IMAGE_SIZE:
        raise HTTPException(status_code=413, detail="Image too large (10MB max)")
    if file.content_type and file.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(status_code=400, detail="Only JPEG, PNG, and WebP images are allowed")

    # Delete old image if replacing
    if aircraft.image_filename:
        try:
            old_path = os.path.join(settings.upload_dir, aircraft.image_filename)
            os.remove(old_path)
        except OSError:
            pass

    # Save new image
    upload_dir = os.path.join(settings.upload_dir, "aircraft", str(aircraft_id))
    os.makedirs(upload_dir, exist_ok=True)

    loop = asyncio.get_running_loop()
    resized_content, forced_ext = await loop.run_in_executor(None, _resize_image, content)
    ext = forced_ext or (os.path.splitext(file.filename)[1] if file.filename else ".jpg")
    filename = f"{uuid_mod.uuid4()}{ext}"
    file_path = os.path.join(upload_dir, filename)

    await loop.run_in_executor(None, _write_file, file_path, resized_content)

    # Store relative path (relative to upload_dir) so it's served at /uploads/aircraft/...
    relative_path = os.path.join("aircraft", str(aircraft_id), filename)
    aircraft.image_filename = relative_path

    await db.flush()
    await db.refresh(aircraft)
    logger.info("Aircraft image saved for %s: %s", aircraft_id, relative_path)
    return aircraft


@router.delete("/{aircraft_id}/image", response_model=AircraftResponse)
async def delete_aircraft_image(
    aircraft_id: UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    result = await db.execute(select(Aircraft).where(Aircraft.id == aircraft_id))
    aircraft = result.scalar_one_or_none()
    if not aircraft:
        raise HTTPException(status_code=404, detail="Aircraft not found")

    if aircraft.image_filename:
        try:
            old_path = os.path.join(settings.upload_dir, aircraft.image_filename)
            os.remove(old_path)
        except OSError:
            pass
        aircraft.image_filename = None
        await db.flush()
        await db.refresh(aircraft)

    return aircraft
