"""Pydantic v2 schemas for the TOS-acceptance route module.

The request schema enforces:

* Plausible name length (rejects empty + ridiculous)
* Real email (``EmailStr`` requires the optional ``email-validator``
  dependency — declared in ``backend/requirements.txt``)
* Optional company / title with sane caps
* Explicit ``confirm: True`` — the checkbox MUST have been checked.
  The custom validator rejects ``False`` (and any non-True input)
  with a 422 so the route layer never sees a degraded acceptance.

The response schema is built ``from_attributes`` so the route can
return the SQLAlchemy ``TosAcceptance`` row directly with one extra
synthesised ``download_url`` field.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class TosAcceptanceRequest(BaseModel):
    """Customer-supplied acceptance form payload."""

    full_name: str = Field(min_length=2, max_length=120)
    email: EmailStr
    company: str = Field(default="", max_length=120)
    title: str = Field(default="", max_length=80)
    confirm: bool = Field(description="Explicit checkbox — must be True")

    # Optional intake-flow correlation. Both nullable because the page
    # may also be reached without an intake token (cold visitor).
    customer_id: uuid.UUID | None = None
    intake_token: str | None = Field(default=None, max_length=64)

    @field_validator("confirm")
    @classmethod
    def must_confirm(cls, v: bool) -> bool:
        if v is not True:
            raise ValueError("Acceptance checkbox must be checked")
        return v


class TosAcceptanceResponse(BaseModel):
    """What the API returns to the customer's browser after acceptance."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    audit_id: str
    accepted_at: datetime
    template_version: str
    template_sha256: str
    signed_sha256: str
    download_url: str


# ── Operator browse-list schemas (v2.66.0 audit UI) ──────────────────


class TosAcceptanceListItem(BaseModel):
    """One row in the operator audit-browse list.

    Excludes ``signed_pdf_path`` (filesystem-internal — operators don't
    need to know the on-disk path) and instead surfaces a relative
    ``download_url`` the UI can hand to <a href> / FileSaver. The
    download URL is the existing operator-gated
    ``GET /api/tos/signed/{audit_id}`` endpoint.

    All hash fields stay full-length so the operator can copy-paste
    them into ``sha256sum`` for tamper verification; the UI is in
    charge of truncating for display.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    audit_id: str
    customer_id: uuid.UUID | None
    intake_token: str | None
    client_name: str
    client_email: str
    client_company: str
    client_title: str
    client_ip: str
    user_agent: str
    accepted_at: datetime
    template_version: str
    template_sha256: str
    signed_sha256: str
    signed_pdf_size: int
    created_at: datetime
    download_url: str


class TosAcceptanceListResponse(BaseModel):
    """Paginated envelope for ``GET /api/tos/acceptances``."""

    items: list[TosAcceptanceListItem]
    total: int
    limit: int
    offset: int
