"""Pydantic schemas for Pilot CRUD and response models."""

from pydantic import BaseModel
from datetime import datetime


class PilotCreate(BaseModel):
    name: str
    email: str | None = None
    phone: str | None = None
    faa_certificate_number: str | None = None
    faa_certificate_expiry: datetime | None = None
    notes: str | None = None


class PilotUpdate(PilotCreate):
    is_active: bool = True


class PilotResponse(PilotUpdate):
    id: str
    created_at: datetime
    updated_at: datetime
    total_flight_hours: float = 0
    total_flights: int = 0

    class Config:
        from_attributes = True


class PilotSummary(BaseModel):
    id: str
    name: str
    is_active: bool
    total_flight_hours: float = 0
    total_flights: int = 0
    faa_certificate_expiry: datetime | None = None
