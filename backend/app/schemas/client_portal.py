from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


# ── Token management (operator-side) ────────────────────────────────

class ClientLinkCreate(BaseModel):
    """Operator creates a client portal link for a mission."""
    expires_days: int = 30


class ClientLinkResponse(BaseModel):
    token_id: str
    portal_url: str
    expires_at: datetime
    customer_id: str
    mission_ids: list[str]


class ClientLinkSendRequest(BaseModel):
    """Operator triggers email delivery of the portal link."""
    pass  # email comes from the customer record


# ── Client auth ─────────────────────────────────────────────────────

class ClientTokenValidateResponse(BaseModel):
    valid: bool
    customer_name: str | None = None
    customer_email: str | None = None
    customer_id: str | None = None
    mission_ids: list[str] = []
    expires_at: datetime | None = None
    has_password: bool = False


class ClientLoginRequest(BaseModel):
    email: str
    password: str


class ClientLoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    customer_name: str | None = None
    mission_ids: list[str] = []
    expires_at: datetime | None = None


# ── Client mission views (filtered, no operator data) ───────────────

class ClientMissionSummary(BaseModel):
    id: str
    title: str
    mission_type: str
    mission_date: str | None = None
    location_name: str | None = None
    status: str

    model_config = {"from_attributes": True}


class ClientMissionDetail(BaseModel):
    id: str
    title: str
    mission_type: str
    description: str | None = None
    mission_date: str | None = None
    location_name: str | None = None
    status: str
    created_at: datetime
    image_count: int = 0

    model_config = {"from_attributes": True}
