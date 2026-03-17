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
    tos_signed: bool = False
    tos_signed_at: datetime | None = None
    intake_completed_at: datetime | None = None
    intake_token: str | None = None
    signature_data: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class IntakeTokenResponse(BaseModel):
    intake_token: str
    intake_url: str
    expires_at: datetime


class IntakePublicResponse(BaseModel):
    customer_name: str | None
    customer_email: str | None
    customer_phone: str | None
    customer_address: str | None
    customer_company: str | None
    tos_pdf_url: str | None
    already_completed: bool


class IntakeFormData(BaseModel):
    name: str
    email: str
    phone: str | None = None
    address: str | None = None
    company: str | None = None
    signature_data: str  # base64 PNG
    tos_accepted: bool
