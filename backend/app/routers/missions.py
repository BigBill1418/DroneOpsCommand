import asyncio
import io
import logging
import os
import uuid as uuid_mod
from datetime import datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile, File, Form, status
from pydantic import BaseModel, ValidationError
from PIL import Image as PILImage
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.client_auth import create_client_token, hash_token
from app.auth.jwt import get_current_user
from app.config import settings
from app.database import get_db
from app.models.client_portal import ClientAccessToken
from app.models.customer import Customer
from app.models.mission import Mission, MissionFlight, MissionImage, MissionStatus
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


async def _send_portal_email_for_mission(mission_id: UUID, customer_id: UUID, db: AsyncSession) -> None:
    """Generate a client portal token and email it to the customer. Non-blocking on failure."""
    try:
        result = await db.execute(select(Customer).where(Customer.id == customer_id))
        customer = result.scalar_one_or_none()
        if not customer or not customer.email:
            logger.info("Skipping portal email for mission=%s: customer %s has no email", mission_id, customer_id)
            return

        result = await db.execute(select(Mission).where(Mission.id == mission_id))
        mission = result.scalar_one_or_none()
        if not mission:
            logger.warning("Skipping portal email: mission=%s not found after create", mission_id)
            return

        mission_ids = [str(mission.id)]
        client_jwt = create_client_token(customer.id, mission_ids, settings.client_token_expire_days)
        expires_at = datetime.utcnow() + timedelta(days=settings.client_token_expire_days)

        token_record = ClientAccessToken(
            customer_id=customer.id,
            token_hash=hash_token(client_jwt),
            mission_scope=[str(mission.id)],
            expires_at=expires_at,
        )
        db.add(token_record)
        await db.flush()

        frontend_url = settings.frontend_url.rstrip("/")
        portal_url = f"{frontend_url}/client/{client_jwt}"

        from app.services.email_service import send_client_portal_email
        await send_client_portal_email(
            to_email=customer.email,
            customer_name=customer.name,
            mission_title=mission.title,
            portal_url=portal_url,
            expires_at=expires_at,
            db=db,
        )
        logger.info("Auto-sent portal email to %s for mission=%s", customer.email, mission_id)
    except Exception as exc:
        logger.error("Failed to auto-send portal email for mission=%s: %s", mission_id, exc, exc_info=True)


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
    request: Request,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Create a new mission.

    v2.67.0 (Mission Hub redesign, spec §4) — TWO defensive guards make
    the duplicate-mission class physically impossible:

    1. Reject any request whose body smuggles an ``id`` field. The Hub's
       MissionCreateModal NEVER sends ``id`` on POST; per-facet editors
       use PUT against ``/api/missions/{id}`` for updates. A future
       stale-bundle or hand-rolled client that POSTs an ``id`` is now
       blocked with 400 instead of silently creating a duplicate row.
    2. Log a WARNING when the same ``(customer_id, title, mission_date)``
       triple was already created in the last 5 minutes. Operators may
       legitimately want two missions for the same customer/title on the
       same day, so we don't reject — but we surface the signal so the
       ADR-0013 4xx-burst alert can graduate it later if needed.
    """
    # ── Parse raw body so we can inspect for forbidden `id` field ──
    try:
        raw = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    if not isinstance(raw, dict):
        raise HTTPException(status_code=400, detail="Body must be a JSON object")

    # Defensive guard #1 — POST must NEVER carry `id`.
    if "id" in raw:
        logger.warning(
            "[MISSION-POST-REJECTED] body contained 'id'=%r (user=%s) — "
            "client must use PUT /api/missions/{id} for updates",
            raw.get("id"), getattr(_user, "username", "unknown"),
        )
        raise HTTPException(
            status_code=400,
            detail="POST /api/missions must not include 'id' in body — use PUT /api/missions/{id} for updates",
        )

    # Validate against the Pydantic schema (preserves prior 422 behaviour
    # for malformed bodies).
    try:
        data = MissionCreate.model_validate(raw)
    except ValidationError as ve:
        # Mirror FastAPI's default 422 envelope so frontend error
        # handling stays unchanged.
        raise HTTPException(status_code=422, detail=ve.errors())

    # Defensive guard #2 — soft duplicate detection (log only, don't reject).
    try:
        if data.title and data.customer_id is not None:
            cutoff = datetime.utcnow() - timedelta(minutes=5)
            dup_q = select(func.count()).select_from(Mission).where(
                Mission.customer_id == data.customer_id,
                Mission.title == data.title,
                Mission.mission_date == data.mission_date,
                Mission.created_at >= cutoff,
            )
            dup_result = await db.execute(dup_q)
            dup_count = dup_result.scalar() or 0
            if dup_count > 0:
                logger.warning(
                    "[MISSION-POST-DUP] possible duplicate POST: "
                    "customer_id=%s title=%r mission_date=%s already created %d time(s) "
                    "in last 5min (user=%s) — allowed (operator override intentional)",
                    data.customer_id, data.title, data.mission_date,
                    dup_count, getattr(_user, "username", "unknown"),
                )
    except Exception as dup_exc:  # pragma: no cover — purely diagnostic
        logger.warning("[MISSION-POST-DUP] dup-check skipped: %s", dup_exc)

    try:
        fields = data.model_dump(exclude_none=True)
        # Strip timezone from expires_at — DB column is TIMESTAMP WITHOUT TIME ZONE
        if isinstance(fields.get("download_link_expires_at"), datetime):
            fields["download_link_expires_at"] = fields["download_link_expires_at"].replace(tzinfo=None)
        mission = Mission(**fields)
        db.add(mission)
        await db.flush()
        logger.info(
            "[MISSION-CREATED] id=%s title=%r customer_id=%s user=%s",
            mission.id, mission.title, mission.customer_id,
            getattr(_user, "username", "unknown"),
        )
        # Re-query with explicit eager loads so relationships are populated for response
        result = await db.execute(
            select(Mission).where(Mission.id == mission.id).options(
                selectinload(Mission.flights),
                selectinload(Mission.images),
                selectinload(Mission.customer),
            )
        )
        mission = result.scalar_one()

        # Auto-send client portal email if customer is assigned
        if mission.customer_id:
            await _send_portal_email_for_mission(mission.id, mission.customer_id, db)

        return mission
    except HTTPException:
        raise
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
        old_customer_id = mission.customer_id
        update_fields = data.model_dump(exclude_unset=True)

        for key, value in update_fields.items():
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

        # Auto-send portal email if customer_id was set or changed
        new_customer_id = update_fields.get("customer_id")
        if new_customer_id and new_customer_id != old_customer_id:
            await _send_portal_email_for_mission(mission.id, new_customer_id, db)

        return mission
    except Exception as exc:
        logger.exception("Failed to update mission: %s", exc)
        raise HTTPException(status_code=500, detail="An internal error occurred")


class MissionStatusPatch(BaseModel):
    """v2.67.0 — body schema for PATCH /api/missions/{id} status transitions.

    Validated against the ``MissionStatus`` enum (Pydantic returns 422
    on invalid values, preserving FastAPI's standard error envelope).
    """

    status: MissionStatus


@router.patch("/{mission_id}", response_model=MissionResponse)
async def patch_mission_status(
    mission_id: UUID,
    data: MissionStatusPatch,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
    reopen: bool = Query(
        default=False,
        description=(
            "When true, allows reverting a SENT mission to COMPLETED. "
            "Logs an audit event ([MISSION-REOPEN]) per spec §8.5. "
            "Required by the Hub's Reopen Mission flow."
        ),
    ),
):
    """Status-only transition for a mission (Mark COMPLETED, Mark SENT,
    Reopen). Used by the Mission Hub header buttons.

    Per spec §8.5 lockdown semantics:
      * Status SENT is treated as a final state. Reverting from SENT to
        anything other than COMPLETED is rejected with 400 — only the
        explicit Reopen flow (``?reopen=true``) is allowed to flip
        SENT back to COMPLETED, and that emits a ``[MISSION-REOPEN]``
        audit log line.
      * All other transitions are accepted (no DB-level state machine —
        the Hub UI is the source of truth for "what's a sane next
        state"; the backend just records the transition + logs it).
    """
    result = await db.execute(select(Mission).where(Mission.id == mission_id))
    mission = result.scalar_one_or_none()
    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")

    previous_status = mission.status
    new_status = data.status

    # Spec §8.5 lockdown: SENT → anything-else only via reopen flow.
    if previous_status == MissionStatus.SENT and new_status != MissionStatus.SENT:
        if new_status != MissionStatus.COMPLETED or not reopen:
            logger.warning(
                "[MISSION-STATUS-REJECTED] SENT→%s without reopen=true "
                "mission_id=%s user=%s",
                new_status.value, mission_id,
                getattr(_user, "username", "unknown"),
            )
            raise HTTPException(
                status_code=400,
                detail=(
                    "Cannot revert a SENT mission. Use the Reopen Mission "
                    "flow (PATCH ?reopen=true with status=completed) to "
                    "correct billing or replace a delivered artifact."
                ),
            )

    mission.status = new_status

    if reopen and previous_status == MissionStatus.SENT and new_status == MissionStatus.COMPLETED:
        logger.warning(
            "[MISSION-REOPEN] mission_id=%s previous_status=%s new_status=%s "
            "user=%s",
            mission_id, previous_status.value, new_status.value,
            getattr(_user, "username", "unknown"),
        )
    else:
        logger.info(
            "[MISSION-STATUS] from=%s to=%s mission_id=%s user=%s",
            previous_status.value, new_status.value, mission_id,
            getattr(_user, "username", "unknown"),
        )

    await db.flush()
    # Re-query with eager loads so the response is consistent with PUT.
    result = await db.execute(
        select(Mission).where(Mission.id == mission_id).options(
            selectinload(Mission.flights),
            selectinload(Mission.images),
            selectinload(Mission.customer),
        )
    )
    return result.scalar_one()


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

    try:
        os.remove(image.file_path)
    except OSError:
        pass
    await db.delete(image)
