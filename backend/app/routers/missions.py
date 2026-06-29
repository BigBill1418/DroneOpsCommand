import asyncio
import logging
import os
import shutil
import uuid as uuid_mod
from datetime import datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile, File, Form, status
from pydantic import BaseModel, ValidationError
from PIL import Image as PILImage
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import noload, raiseload, selectinload

from app.auth.client_auth import create_client_token, hash_token
from app.auth.jwt import get_current_user
from app.config import settings
from app.utils.timezone import iso_utc
from app.database import get_db
from app.models.client_portal import ClientAccessToken
from app.models.customer import Customer
from app.models.mission import Mission, MissionFlight, MissionImage, MissionStatus, MissionSource
from app.models.user import User
from app.schemas.mission import (
    MissionCreate,
    MissionFlightBulkAttach,
    MissionFlightCreate,
    MissionFlightResponse,
    MissionImageResponse,
    MissionListItemResponse,
    MissionResponse,
    MissionUpdate,
)
from app.services.opendronelog import opendronelog_client

logger = logging.getLogger("doc.missions")

router = APIRouter(prefix="/api/missions", tags=["missions"])

# Heavy per-flight cache keys that duplicate the full GPS track / telemetry.
# (ADR-0025) They are NEVER read from the mission DETAIL payload by any
# frontend consumer — MissionDetail.tsx and MissionFlightsEdit.tsx read only
# scalar cache fields (duration_secs, display_name, drone_model, …). The map
# and flight-replay views load tracks ON DEMAND (routers/maps.py via
# Flight.gps_track; FlightReplay via the per-flight detail route). Stripping
# these keys before serialization makes the detail/write responses O(rows)
# instead of O(track points) and is the read-side half of the ADR-0025 OOM
# fix; A2 (not writing the track at attach) is the write-side half.
_CACHE_HEAVY_KEYS = ("track", "gps_data", "coordinates", "telemetry")

# The scalar display fields lifted from a native Flight onto the cache at
# attach (ADR-0025). Deliberately excludes the GPS track.
_FLIGHT_SCALAR_CACHE_KEYS = (
    "id", "name", "display_name", "drone_model", "drone_serial",
    "start_time", "duration_secs", "total_distance", "max_altitude",
    "max_speed", "home_lat", "home_lon", "point_count",
)


def _strip_cache_heavy_keys(cache: dict | None) -> dict | None:
    """Return a copy of ``cache`` with the heavy track/telemetry keys removed.

    DB-agnostic (pure Python) so it works identically on the Postgres prod
    path and the SQLite/stubbed test paths. Returns the original object
    untouched when there is nothing heavy to strip (the common case for
    go-forward missions, whose caches are already scalar-only per A2).
    """
    if not cache or not any(k in cache for k in _CACHE_HEAVY_KEYS):
        return cache
    return {k: v for k, v in cache.items() if k not in _CACHE_HEAVY_KEYS}


def _scalar_cache_from_flight(local_flight, base: dict | None = None) -> dict:
    """Build a scalar-only display cache from a native ``Flight`` (ADR-0025).

    No ``track`` key — the GPS track stays once on ``Flight.gps_track`` and is
    loaded on demand by the map/report paths. ``base`` (the picker's display
    row) is preserved for any extra display fields but has its heavy keys
    stripped so a stale client can't smuggle a track back into the cache.
    """
    cache = dict(_strip_cache_heavy_keys(base) or {})
    cache.update({
        "id": str(local_flight.id),
        "name": local_flight.name,
        "display_name": local_flight.name,
        "drone_model": local_flight.drone_model,
        "drone_serial": local_flight.drone_serial,
        "start_time": iso_utc(local_flight.start_time),
        "duration_secs": local_flight.duration_secs,
        "total_distance": local_flight.total_distance,
        "max_altitude": local_flight.max_altitude,
        "max_speed": local_flight.max_speed,
        "home_lat": local_flight.home_lat,
        "home_lon": local_flight.home_lon,
        "point_count": local_flight.point_count,
    })
    return cache


def _serialize_mission(mission: Mission) -> MissionResponse:
    """Serialize a mission to ``MissionResponse`` with heavy cache keys stripped.

    The detail (``GET``) and every write re-query (POST/PUT/PATCH) return the
    full mission graph. Without stripping, a large mission's response body is
    O(N_flights × ~19k track points) and OOM-kills the worker mid-serialize
    (ADR-0025). This strips track/gps_data/coordinates/telemetry from each
    flight's cache on the OUTBOUND response only — the stored rows are never
    mutated, so no write is triggered on a read and legacy track data on disk
    is preserved.
    """
    resp = MissionResponse.model_validate(mission)
    for fr in resp.flights:
        fr.flight_data_cache = _strip_cache_heavy_keys(fr.flight_data_cache)
    return resp


def _mission_graph_options():
    """Loader options for every query whose rows feed ``MissionResponse``.

    CRITICAL (2026-06-11 audit P1-2 — same OOM class as ADR-0019): the
    ``MissionFlight.flight`` relationship is ``lazy="selectin"`` at the
    mapper level, so a bare ``selectinload(Mission.flights)`` cascades into
    loading every attached ``Flight`` row IN FULL — gps_track (~19k points
    per flight), telemetry time-series and raw_metadata included — even
    though ``MissionFlightResponse`` never serializes ``flight`` (the
    display fields, including the track, come from ``flight_data_cache``).
    Under the 1 GiB cgroup cap that decompressed the TOASTed track history
    twice over on every Mission Hub list load. ``raiseload`` is scoped
    per-query here (NOT a mapper-level lazy="raise") and fails loudly if a
    future change starts touching ``mf.flight`` on these paths instead of
    silently re-introducing the OOM.
    """
    return (
        selectinload(Mission.flights).options(
            raiseload(MissionFlight.flight),
            # aircraft IS serialized (MissionFlightResponse.aircraft) —
            # keep it eagerly loaded, explicitly rather than via the
            # mapper-level selectin default.
            selectinload(MissionFlight.aircraft),
        ),
        selectinload(Mission.images),
        selectinload(Mission.customer),
    )


def _mission_list_options():
    """Loader options for GET /api/missions (list) — FU-8 #1 lean list.

    The list serializes ``MissionListItemResponse``, which carries ONLY
    scalar mission columns — no ``flights``, no ``images`` (see
    ``Missions.tsx``, which renders title/type/location/date/status/
    is_billable and nothing else). The ``flights`` and ``images``
    relationships are ``lazy="selectin"`` at the mapper level, so a bare
    ``select(Mission)`` would STILL eager-load them on the list path —
    dragging ``MissionFlight.flight_data_cache`` (which duplicates the
    full ~19k-point GPS track per attached flight) into every list row.
    That made the list payload O(track points) instead of O(rows).

    These ``noload`` options suppress the relationship loads on the list
    query, so the work is one ``SELECT`` over the scalar mission columns —
    strictly O(rows). ``noload`` (not ``raiseload``) is intentional: the
    lean schema never accesses them, but if some future list-path code
    peeks at ``m.flights`` it gets an empty list rather than a 500 — the
    list contract has no flight/image/customer data to be wrong about.
    ``customer`` is suppressed too: ``MissionListItemResponse`` doesn't
    serialize it, so the mapper-level ``lazy="selectin"`` default would be
    a wasted extra round-trip. Detail and write re-queries keep
    ``_mission_graph_options()`` (full shape) untouched.
    """
    return (
        noload(Mission.flights),
        noload(Mission.images),
        noload(Mission.customer),
    )


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


@router.get("", response_model=list[MissionListItemResponse])
async def list_missions(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Mission Hub list — lean rows (FU-8 #1).

    Returns ``MissionListItemResponse`` (scalar columns only), NOT the
    full ``MissionResponse``: the list UI renders no flight/image data,
    and the full shape dragged ``flight_data_cache`` — which duplicates
    the entire GPS track per attached flight — into every row, making the
    payload O(track points). ``_mission_list_options()`` ``noload``s the
    flights/images/customer relationships so the query is O(rows). The
    detail route (``GET /api/missions/{id}``) keeps the full shape.
    """
    result = await db.execute(
        select(Mission)
        .order_by(Mission.created_at.desc())
        .options(*_mission_list_options())
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
        # ADR-0016 — store the MissionSource enum's plain value ("website"),
        # not the member repr, on the VARCHAR `source` column.
        if isinstance(fields.get("source"), MissionSource):
            fields["source"] = fields["source"].value
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
            select(Mission).where(Mission.id == mission.id).options(*_mission_graph_options())
        )
        mission = result.scalar_one()

        # Auto-send client portal email if customer is assigned
        if mission.customer_id:
            await _send_portal_email_for_mission(mission.id, mission.customer_id, db)

        return _serialize_mission(mission)
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
        select(Mission).where(Mission.id == mission_id).options(*_mission_graph_options())
    )
    mission = result.scalar_one_or_none()
    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")
    return _serialize_mission(mission)


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
            # ADR-0016 — persist the MissionSource enum's plain value on the
            # VARCHAR `source` column (value may also be None to clear it).
            if key == "source" and isinstance(value, MissionSource):
                value = value.value
            setattr(mission, key, value)

        await db.flush()
        # Re-query with explicit eager loads so relationships are populated for response
        result = await db.execute(
            select(Mission).where(Mission.id == mission_id).options(*_mission_graph_options())
        )
        mission = result.scalar_one()

        # Auto-send portal email if customer_id was set or changed
        new_customer_id = update_fields.get("customer_id")
        if new_customer_id and new_customer_id != old_customer_id:
            await _send_portal_email_for_mission(mission.id, new_customer_id, db)

        return _serialize_mission(mission)
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
        select(Mission).where(Mission.id == mission_id).options(*_mission_graph_options())
    )
    return _serialize_mission(result.scalar_one())


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

async def _find_existing_attachment(
    db: AsyncSession,
    mission_id: UUID,
    flight_id: UUID | None,
    opendronelog_flight_id: str | None,
) -> MissionFlight | None:
    """Return the MissionFlight already attaching this flight to this mission.

    Idempotency key mirrors the partial unique indexes added in migration
    0003 / ADR-0026: native flights match on ``(mission_id, flight_id)``,
    legacy ODL rows on ``(mission_id, opendronelog_flight_id)``. Returns None
    when the flight is not yet attached (the insert path runs) or when neither
    key is present (a malformed row we cannot dedup — let it through).
    """
    if flight_id is not None:
        q = select(MissionFlight).where(
            MissionFlight.mission_id == mission_id,
            MissionFlight.flight_id == flight_id,
        )
    elif opendronelog_flight_id:
        q = select(MissionFlight).where(
            MissionFlight.mission_id == mission_id,
            MissionFlight.opendronelog_flight_id == opendronelog_flight_id,
        )
    else:
        return None
    return (await db.execute(q)).scalars().first()


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

    # ADR-0026: idempotent single-add. The legacy path had ZERO dedup, so a
    # retry storm (attach timing out / 500ing while the operator re-clicked)
    # inserted the same flight many times — the "savannah" mission reached 50
    # rows for 27 flights and every report total inflated 100-160%. If this
    # flight is already attached, return the existing row instead of inserting
    # a duplicate. (Re-clicking a successful attach is now a safe no-op.)
    existing = await _find_existing_attachment(
        db, mission_id, data.flight_id, data.opendronelog_flight_id
    )
    if existing is not None:
        logger.info(
            "[ADD-FLIGHT] idempotent skip — flight already attached "
            "(mission=%s flight_id=%s odl=%s) returning existing row %s",
            mission_id, data.flight_id, data.opendronelog_flight_id, existing.id,
        )
        return existing

    # Aircraft attribution is derived from the flight log — the operator
    # never picks it. Flight uploads already fleet-match by serial/model
    # (see flight_library._match_fleet_aircraft), so by the time a flight
    # is being attached to a mission, Flight.aircraft_id is authoritative.
    # We ignore any client-sent aircraft_id to keep a single source of
    # truth.
    if data.flight_id:
        from app.models.flight import Flight as FlightModel
        flt_result = await db.execute(select(FlightModel).where(FlightModel.id == data.flight_id))
        local_flight = flt_result.scalar_one_or_none()
        if local_flight:
            data.aircraft_id = local_flight.aircraft_id
            # ADR-0025: store SCALAR display fields only — never the GPS track.
            # The track stays once on Flight.gps_track and is loaded on demand
            # by the map/report paths via flight_id. Writing it here duplicated
            # ~19k points per attach and made every full-mission read O(track).
            data.flight_data_cache = _scalar_cache_from_flight(
                local_flight, data.flight_data_cache
            )
    else:
        # Legacy ODL row — no Flight row to read from. Fleet-match by
        # serial/model out of the cache so reports/financials still see
        # an aircraft.
        cache = data.flight_data_cache or {}
        try:
            from app.routers.flight_library import _match_fleet_aircraft
            fleet_match = await _match_fleet_aircraft(
                db,
                cache.get("drone_serial"),
                cache.get("drone_model"),
            )
            if fleet_match is not None:
                data.aircraft_id = fleet_match.id
            else:
                data.aircraft_id = None
        except Exception as exc:
            logger.warning("Fleet match failed for ODL flight attach: %s", exc)
            data.aircraft_id = None

        # Enrich flight_data_cache with GPS track from OpenDroneLog
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
    try:
        await db.flush()
    except IntegrityError:
        # Backstop for the partial unique indexes (ADR-0026). Two concurrent
        # attach requests can both pass the SELECT guard above and race to
        # INSERT; the DB rejects the second. Roll back this row and return the
        # one that won — the attach is still idempotent under concurrency.
        await db.rollback()
        existing = await _find_existing_attachment(
            db, mission_id, data.flight_id, data.opendronelog_flight_id
        )
        if existing is not None:
            logger.info(
                "[ADD-FLIGHT] unique-constraint race resolved — returning "
                "existing row %s (mission=%s flight_id=%s odl=%s)",
                existing.id, mission_id, data.flight_id, data.opendronelog_flight_id,
            )
            return existing
        raise
    await db.refresh(flight)
    return flight


@router.post(
    "/{mission_id}/flights/bulk",
    response_model=list[MissionFlightResponse],
    status_code=status.HTTP_201_CREATED,
)
async def add_flights_bulk(
    mission_id: UUID,
    data: MissionFlightBulkAttach,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Attach many flights to a mission in ONE transaction (ADR-0025).

    Replaces the one-POST-per-click flow that made adding a large mission
    ("savannah") painfully slow. Behaviour:

    * Single flush — all new ``MissionFlight`` rows commit together.
    * Idempotent — flights already attached (matched by native ``flight_id``
      or legacy ``opendronelog_flight_id``) are skipped, and duplicates
      WITHIN the batch are de-duplicated. Re-submitting the same selection
      is a safe no-op.
    * Scalar caches only — for native flights the server derives the display
      cache + ``aircraft_id`` from ``Flight`` and NEVER stores the GPS track
      (loaded on demand later). Native flight rows are bulk-loaded with the
      heavy JSON columns deferred so a 500-flight attach stays O(rows).
    * Aircraft is always derived server-side (native: ``Flight.aircraft_id``;
      legacy ODL: fleet-match by serial/model). Client-sent aircraft is
      ignored — single source of truth is the flight log.

    Returns the rows that were newly created (already-attached skips are not
    re-returned), each with its derived aircraft.
    """
    from app.models.flight import Flight as FlightModel
    from sqlalchemy.orm import defer

    result = await db.execute(select(Mission).where(Mission.id == mission_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Mission not found")

    if not data.flights:
        return []

    # Existing attachments — for idempotent skip.
    existing_q = await db.execute(
        select(MissionFlight.flight_id, MissionFlight.opendronelog_flight_id).where(
            MissionFlight.mission_id == mission_id
        )
    )
    existing_flight_ids: set[str] = set()
    existing_odl_ids: set[str] = set()
    for fid, odl in existing_q.all():
        if fid is not None:
            existing_flight_ids.add(str(fid))
        if odl:
            existing_odl_ids.add(str(odl))

    # Batch-load native Flight rows (scalars only — defer the heavy JSON so a
    # large attach never materializes any GPS track / telemetry).
    native_ids = {item.flight_id for item in data.flights if item.flight_id is not None}
    flights_by_id: dict[UUID, FlightModel] = {}
    if native_ids:
        flt_rows = await db.execute(
            select(FlightModel)
            .where(FlightModel.id.in_(native_ids))
            .options(
                defer(FlightModel.gps_track),
                defer(FlightModel.telemetry),
                defer(FlightModel.raw_metadata),
            )
        )
        flights_by_id = {f.id: f for f in flt_rows.scalars().all()}

    created: list[MissionFlight] = []
    seen_flight_ids: set[str] = set()
    seen_odl_ids: set[str] = set()
    skipped = 0

    for item in data.flights:
        if item.flight_id is not None:
            key = str(item.flight_id)
            if key in existing_flight_ids or key in seen_flight_ids:
                skipped += 1
                continue
            local_flight = flights_by_id.get(item.flight_id)
            if local_flight is None:
                # Unknown flight_id — skip rather than insert a dangling row.
                logger.warning(
                    "[BULK-ATTACH] skipping unknown flight_id=%s (mission=%s)",
                    item.flight_id, mission_id,
                )
                skipped += 1
                continue
            seen_flight_ids.add(key)
            mf = MissionFlight(
                mission_id=mission_id,
                flight_id=item.flight_id,
                opendronelog_flight_id=None,
                aircraft_id=local_flight.aircraft_id,
                flight_data_cache=_scalar_cache_from_flight(
                    local_flight, item.flight_data_cache
                ),
            )
            created.append(mf)
        else:
            # Legacy ODL row — no Flight to load from. Fleet-match by
            # serial/model out of the cache; keep scalar display fields but
            # strip any heavy track the client smuggled in (bulk never stores
            # tracks — the ODL single-add path handles track-bearing rows).
            odl_key = str(item.opendronelog_flight_id or "")
            if not odl_key:
                skipped += 1
                continue
            if odl_key in existing_odl_ids or odl_key in seen_odl_ids:
                skipped += 1
                continue
            seen_odl_ids.add(odl_key)
            cache = _strip_cache_heavy_keys(item.flight_data_cache) or {}
            aircraft_id = None
            try:
                from app.routers.flight_library import _match_fleet_aircraft
                fleet_match = await _match_fleet_aircraft(
                    db, cache.get("drone_serial"), cache.get("drone_model")
                )
                aircraft_id = fleet_match.id if fleet_match is not None else None
            except Exception as exc:
                logger.warning("[BULK-ATTACH] fleet match failed for ODL flight: %s", exc)
            mf = MissionFlight(
                mission_id=mission_id,
                flight_id=None,
                opendronelog_flight_id=item.opendronelog_flight_id,
                aircraft_id=aircraft_id,
                flight_data_cache=cache,
            )
            created.append(mf)

    for mf in created:
        db.add(mf)
    await db.flush()

    logger.info(
        "[BULK-ATTACH] mission=%s attached=%d skipped=%d (requested=%d) user=%s",
        mission_id, len(created), skipped, len(data.flights),
        getattr(_user, "username", "unknown"),
    )

    if not created:
        return []

    # Re-query the created rows with aircraft eagerly loaded for the response
    # (caches are scalar-only so this stays small even for hundreds of rows).
    created_ids = [mf.id for mf in created]
    rows = await db.execute(
        select(MissionFlight)
        .where(MissionFlight.id.in_(created_ids))
        .options(selectinload(MissionFlight.aircraft))
    )
    return rows.scalars().all()


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
MAX_IMAGE_UPLOAD_BYTES = 60 * 1024 * 1024  # 60 MB — DJI stills routinely run 40–55 MB
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/tiff"}

# Bound concurrent decodes: even draft-downscaled, a 48 MP still costs tens
# of MB of pixel buffer; the backend runs under a 1 GiB cgroup cap and was
# OOM-killed on 2026-06-11 when the old whole-file-in-RAM path stacked up.
_image_decode_sem = asyncio.Semaphore(2)


def _upload_size_bytes(file: UploadFile) -> int:
    """Size of an already-parsed multipart upload without reading it into RAM."""
    if file.size is not None:
        return file.size
    f = file.file
    pos = f.tell()
    f.seek(0, os.SEEK_END)
    size = f.tell()
    f.seek(pos)
    return size


def _process_image_upload(src, dest_dir: str, fallback_ext: str) -> str:
    """Decode, downscale, and persist an uploaded image. Runs in a worker thread.

    Reads straight from the (disk-spooled) multipart temp file — the full
    original is never materialized as one bytes object. JPEG draft mode
    decodes at a reduced scale ≥2× the target so LANCZOS keeps its quality
    headroom while a 48 MP decode drops from ~150 MB to ~35 MB.
    Unparseable files fall back to a raw streamed copy (previous behavior).
    """
    name = uuid_mod.uuid4()
    src.seek(0)
    try:
        img = PILImage.open(src)
        img.draft("RGB", (MAX_IMAGE_DIMENSION * 2, MAX_IMAGE_DIMENSION * 2))
        try:
            from PIL import ImageOps
            img = ImageOps.exif_transpose(img)
        except Exception:
            pass

        if img.width > MAX_IMAGE_DIMENSION or img.height > MAX_IMAGE_DIMENSION:
            img.thumbnail((MAX_IMAGE_DIMENSION, MAX_IMAGE_DIMENSION), PILImage.LANCZOS)

        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        dest = os.path.join(dest_dir, f"{name}.jpg")
        img.save(dest, format="JPEG", quality=82, optimize=True)
        img.close()
        return dest
    except Exception as exc:
        logger.warning("Image resize failed, storing original: %s", exc)
        src.seek(0)
        dest = os.path.join(dest_dir, f"{name}{fallback_ext or '.jpg'}")
        with open(dest, "wb") as out:
            shutil.copyfileobj(src, out, 1024 * 1024)
        return dest


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

    # Cheap rejections first — before any byte processing or disk writes.
    if file.content_type and file.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(status_code=400, detail="Only JPEG, PNG, WebP, and TIFF images are allowed")
    size = _upload_size_bytes(file)
    if size > MAX_IMAGE_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Image too large (60MB max)")
    logger.info("Image upload for mission %s: %s (%d bytes)", mission_id, file.filename, size)

    upload_dir = os.path.join(settings.upload_dir, str(mission_id))
    os.makedirs(upload_dir, exist_ok=True)

    fallback_ext = os.path.splitext(file.filename)[1] if file.filename else ".jpg"
    loop = asyncio.get_running_loop()
    async with _image_decode_sem:
        file_path = await loop.run_in_executor(
            None, _process_image_upload, file.file, upload_dir, fallback_ext
        )

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
