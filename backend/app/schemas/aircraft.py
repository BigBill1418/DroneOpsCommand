from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class AircraftCreate(BaseModel):
    model_name: str
    manufacturer: str = "DJI"
    image_filename: str | None = None
    specs: dict = {}


class AircraftUpdate(BaseModel):
    model_name: str | None = None
    manufacturer: str | None = None
    image_filename: str | None = None
    specs: dict | None = None


class AircraftResponse(BaseModel):
    id: UUID
    model_name: str
    manufacturer: str
    image_filename: str | None
    specs: dict
    created_at: datetime

    model_config = {"from_attributes": True}
