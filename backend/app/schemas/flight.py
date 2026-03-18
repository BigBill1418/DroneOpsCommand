from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from app.schemas.aircraft import AircraftResponse


class FlightCreate(BaseModel):
    """For manual flight entry."""
    name: str
    drone_model: str | None = None
    drone_serial: str | None = None
    battery_serial: str | None = None
    start_time: datetime | None = None
    duration_secs: float = 0
    total_distance: float = 0
    max_altitude: float = 0
    max_speed: float = 0
    home_lat: float | None = None
    home_lon: float | None = None
    notes: str | None = None
    tags: list[str] | None = None
    aircraft_id: UUID | None = None
    gps_track: list[dict] | None = None


class FlightUpdate(BaseModel):
    name: str | None = None
    drone_model: str | None = None
    drone_serial: str | None = None
    battery_serial: str | None = None
    start_time: datetime | None = None
    duration_secs: float | None = None
    total_distance: float | None = None
    max_altitude: float | None = None
    max_speed: float | None = None
    home_lat: float | None = None
    home_lon: float | None = None
    notes: str | None = None
    tags: list[str] | None = None
    aircraft_id: UUID | None = None


class FlightResponse(BaseModel):
    id: UUID
    name: str
    drone_model: str | None
    drone_serial: str | None
    battery_serial: str | None
    start_time: datetime | None
    duration_secs: float
    total_distance: float
    max_altitude: float
    max_speed: float
    home_lat: float | None
    home_lon: float | None
    point_count: int
    notes: str | None
    tags: list | None
    source: str
    original_filename: str | None
    aircraft_id: UUID | None
    aircraft: AircraftResponse | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class FlightDetailResponse(FlightResponse):
    """Full response including GPS track and telemetry."""
    gps_track: list | None = None
    telemetry: dict | None = None
    raw_metadata: dict | None = None


class FlightUploadResponse(BaseModel):
    imported: int
    skipped: int
    errors: list[str]
    flights: list[FlightResponse]


class BatteryResponse(BaseModel):
    id: UUID
    serial: str
    model: str | None
    purchase_date: str | None = None
    cycle_count: int
    last_voltage: float | None
    health_pct: float | None
    status: str
    notes: str | None
    aircraft_id: UUID | None
    aircraft: AircraftResponse | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class BatteryCreate(BaseModel):
    serial: str
    model: str | None = None
    purchase_date: str | None = None
    status: str = "active"
    notes: str | None = None
    aircraft_id: UUID | None = None


class BatteryUpdate(BaseModel):
    model: str | None = None
    purchase_date: str | None = None
    status: str | None = None
    notes: str | None = None
    aircraft_id: UUID | None = None


class BatteryLogResponse(BaseModel):
    id: UUID
    battery_id: UUID
    flight_id: UUID | None
    timestamp: datetime
    start_voltage: float | None
    end_voltage: float | None
    min_voltage: float | None
    max_temp: float | None
    cycles_at_time: int | None
    discharge_mah: float | None

    model_config = {"from_attributes": True}


class MaintenanceRecordCreate(BaseModel):
    aircraft_id: UUID
    maintenance_type: str
    description: str | None = None
    performed_at: str
    flight_hours_at: float | None = None
    next_due_hours: float | None = None
    next_due_date: str | None = None
    cost: float | None = None
    notes: str | None = None


class MaintenanceRecordResponse(BaseModel):
    id: UUID
    aircraft_id: UUID
    maintenance_type: str
    description: str | None
    performed_at: str
    flight_hours_at: float | None
    next_due_hours: float | None
    next_due_date: str | None
    cost: float | None
    notes: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class MaintenanceScheduleCreate(BaseModel):
    aircraft_id: UUID
    maintenance_type: str
    interval_hours: float | None = None
    interval_days: int | None = None
    description: str | None = None


class MaintenanceScheduleResponse(BaseModel):
    id: UUID
    aircraft_id: UUID
    maintenance_type: str
    interval_hours: float | None
    interval_days: int | None
    last_performed: str | None
    description: str | None
    created_at: datetime

    model_config = {"from_attributes": True}
