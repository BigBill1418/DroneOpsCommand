from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class CustomerCreate(BaseModel):
    name: str
    email: str | None = None
    phone: str | None = None
    address: str | None = None
    company: str | None = None
    notes: str | None = None


class CustomerUpdate(BaseModel):
    name: str | None = None
    email: str | None = None
    phone: str | None = None
    address: str | None = None
    company: str | None = None
    notes: str | None = None


class CustomerResponse(BaseModel):
    id: UUID
    name: str
    email: str | None
    phone: str | None
    address: str | None
    company: str | None
    notes: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
