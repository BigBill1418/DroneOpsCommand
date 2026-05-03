from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class CustomerCreate(BaseModel):
    name: str
    email: str | None = None
    phone: str | None = None
    address: str | None = None
    city: str | None = None
    state: str | None = None
    zip_code: str | None = None
    company: str | None = None
    notes: str | None = None


class CustomerUpdate(BaseModel):
    name: str | None = None
    email: str | None = None
    phone: str | None = None
    address: str | None = None
    city: str | None = None
    state: str | None = None
    zip_code: str | None = None
    company: str | None = None
    notes: str | None = None


class CustomerResponse(BaseModel):
    id: UUID
    name: str
    email: str | None
    phone: str | None
    address: str | None
    city: str | None
    state: str | None
    zip_code: str | None
    company: str | None
    notes: str | None
    tos_signed: bool = False
    tos_signed_at: datetime | None = None
    intake_completed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    # v2.66.3 — pointer to the LATEST tos_acceptances row for this
    # customer. Null for legacy canvas-signed customers (the legacy
    # ``tos_signed_at`` + ``tos_pdf_path`` columns remain the source of
    # truth for those — operator UI falls back accordingly).
    latest_tos_audit_id: str | None = None
    latest_tos_signed_sha: str | None = None
    latest_tos_template_version: str | None = None

    model_config = {"from_attributes": True}


class IntakeTokenResponse(BaseModel):
    intake_token: str
    intake_url: str
    expires_at: datetime
    customer_id: str


class IntakePublicResponse(BaseModel):
    customer_name: str | None
    customer_email: str | None
    customer_phone: str | None
    customer_address: str | None
    customer_city: str | None
    customer_state: str | None
    customer_zip_code: str | None
    customer_company: str | None
    tos_pdf_url: str | None
    already_completed: bool


class IntakeFormData(BaseModel):
    name: str
    email: str
    phone: str | None = None
    address: str | None = None
    city: str | None = None
    state: str | None = None
    zip_code: str | None = None
    company: str | None = None
    signature_data: str  # base64 PNG
    tos_accepted: bool
