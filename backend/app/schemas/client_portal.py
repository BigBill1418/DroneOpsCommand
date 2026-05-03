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
    client_notes: str | None = None
    created_at: datetime
    image_count: int = 0

    model_config = {"from_attributes": True}


# ── Client invoice views ────────────────────────────────────────────

class ClientInvoiceLineItem(BaseModel):
    description: str
    quantity: float
    unit_price: float
    total: float


class ClientInvoiceResponse(BaseModel):
    """Customer-facing invoice payload — ADR-0008 + ADR-0009.

    Adds the deposit columns and the computed `payment_phase` so the
    client portal can render the 4-step phase strip + the two-row
    deposit/balance table without duplicating the truth-table logic
    in TypeScript.
    """
    id: str
    total: float
    paid_in_full: bool
    paid_at: datetime | None = None
    payment_method: str | None = None
    line_items: list[ClientInvoiceLineItem] = []

    # ADR-0009 deposit fields.
    deposit_required: bool = False
    deposit_amount: float = 0
    deposit_paid: bool = False
    deposit_paid_at: datetime | None = None
    deposit_payment_method: str | None = None
    balance_amount: float = 0
    # One of: "deposit_due", "awaiting_completion", "balance_due",
    # "paid_in_full". See app/models/invoice.py:compute_payment_phase
    # for the truth table.
    payment_phase: str


class ClientPaymentResponse(BaseModel):
    checkout_url: str
