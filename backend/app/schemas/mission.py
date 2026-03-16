from datetime import datetime, date
from uuid import UUID

from pydantic import BaseModel

from app.models.mission import MissionType, MissionStatus
from app.schemas.aircraft import AircraftResponse


class MissionFlightCreate(BaseModel):
    opendronelog_flight_id: str
    aircraft_id: UUID | None = None
    flight_data_cache: dict | None = None


class MissionFlightResponse(BaseModel):
    id: UUID
    opendronelog_flight_id: str
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
    unas_folder_path: str | None = None
    download_link_url: str | None = None
    download_link_expires_at: datetime | None = None


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
    unas_folder_path: str | None = None
    download_link_url: str | None = None
    download_link_expires_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    flights: list[MissionFlightResponse] = []
    images: list[MissionImageResponse] = []

    model_config = {"from_attributes": True}
