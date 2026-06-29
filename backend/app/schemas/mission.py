from datetime import datetime, date
from uuid import UUID

from pydantic import BaseModel

from app.models.mission import MissionType, MissionStatus, MissionSource
from app.schemas.aircraft import AircraftResponse


class MissionFlightCreate(BaseModel):
    opendronelog_flight_id: str | None = None
    flight_id: UUID | None = None
    aircraft_id: UUID | None = None
    flight_data_cache: dict | None = None


class MissionFlightBulkItem(BaseModel):
    """One flight to attach in a bulk request (ADR-0025).

    Same identity fields as ``MissionFlightCreate`` (a native ``flight_id``
    XOR a legacy ``opendronelog_flight_id``); ``flight_data_cache`` is the
    picker's display row. The server derives aircraft + scalar cache and
    NEVER stores the GPS track for native flights (the track is loaded on
    demand from ``Flight.gps_track``). ``aircraft_id`` is intentionally
    absent — it is always derived server-side from the flight log.
    """

    opendronelog_flight_id: str | None = None
    flight_id: UUID | None = None
    flight_data_cache: dict | None = None


class MissionFlightBulkAttach(BaseModel):
    """Body for POST /api/missions/{id}/flights/bulk (ADR-0025)."""

    flights: list[MissionFlightBulkItem]


class MissionFlightResponse(BaseModel):
    id: UUID
    opendronelog_flight_id: str | None
    flight_id: UUID | None = None
    aircraft_id: UUID | None
    aircraft: AircraftResponse | None = None
    flight_data_cache: dict | None
    added_at: datetime

    model_config = {"from_attributes": True}


class MissionImageResponse(BaseModel):
    id: UUID
    file_path: str
    caption: str | None
    sort_order: int

    model_config = {"from_attributes": True}


class MissionCreate(BaseModel):
    customer_id: UUID | None = None
    title: str
    mission_type: MissionType = MissionType.OTHER
    description: str | None = None
    mission_date: date | None = None
    location_name: str | None = None
    area_coordinates: dict | None = None
    is_billable: bool = False
    # ADR-0016 — lead-source attribution. `MissionSource` constrains the
    # allowed values (Pydantic returns 422 on anything else); NULL/omitted
    # means origin unknown. `source_ref` is a free-text external reference.
    source: MissionSource | None = None
    source_ref: str | None = None
    unas_folder_path: str | None = None
    download_link_url: str | None = None
    download_link_expires_at: datetime | None = None


class MissionUpdate(BaseModel):
    customer_id: UUID | None = None
    title: str | None = None
    mission_type: MissionType | None = None
    description: str | None = None
    mission_date: date | None = None
    location_name: str | None = None
    area_coordinates: dict | None = None
    status: MissionStatus | None = None
    is_billable: bool | None = None
    source: MissionSource | None = None
    source_ref: str | None = None
    unas_folder_path: str | None = None
    download_link_url: str | None = None
    download_link_expires_at: datetime | None = None
    client_notes: str | None = None


class MissionResponse(BaseModel):
    id: UUID
    customer_id: UUID | None
    title: str
    mission_type: MissionType
    description: str | None
    mission_date: date | None
    location_name: str | None
    area_coordinates: dict | None
    status: MissionStatus
    is_billable: bool
    source: MissionSource | None = None
    source_ref: str | None = None
    unas_folder_path: str | None = None
    download_link_url: str | None = None
    download_link_expires_at: datetime | None = None
    client_notes: str | None = None
    created_at: datetime
    updated_at: datetime
    flights: list[MissionFlightResponse] = []
    images: list[MissionImageResponse] = []

    model_config = {"from_attributes": True}


class MissionListItemResponse(BaseModel):
    """Lean row shape for GET /api/missions (list) — FU-8 #1.

    The Mission Hub list (frontend ``Missions.tsx``) renders ONLY the
    scalar mission columns below: title, type, location, date, status,
    is_billable. It never touches ``flights`` or ``images``. The full
    ``MissionResponse`` shape dragged ``flights[].flight_data_cache`` into
    every list row, and that cache duplicates the entire GPS track
    (~19k points per flight) — so the list payload was O(track) instead
    of O(rows). v2.69.0 already raiseload-ed ``MissionFlight.flight``
    (the relationship → ``Flight.gps_track`` cascade); this schema closes
    the remaining leg by dropping the cache-column copy from the list
    contract entirely.

    Detail (``GET /api/missions/{id}``) and all write re-queries keep the
    full ``MissionResponse`` shape byte-identical — ``MissionDetail.tsx``
    reads ``flights[].flight_data_cache.duration_secs`` and the flight /
    image counts, so the Hub still gets everything it needs.

    Drops ``flights`` and ``images`` relative to ``MissionResponse``;
    every scalar field is preserved verbatim so existing list consumers
    that read any of them are unaffected.
    """

    id: UUID
    customer_id: UUID | None
    title: str
    mission_type: MissionType
    description: str | None
    mission_date: date | None
    location_name: str | None
    area_coordinates: dict | None
    status: MissionStatus
    is_billable: bool
    source: MissionSource | None = None
    source_ref: str | None = None
    unas_folder_path: str | None = None
    download_link_url: str | None = None
    download_link_expires_at: datetime | None = None
    client_notes: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
