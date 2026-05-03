"""v2.66.3 — CustomerResponse exposes the latest tos_acceptances pointer.

The Customers operator UI needs three fields beyond the legacy
``tos_signed`` boolean to render the new AcroForm flow correctly:

* ``latest_tos_audit_id``         — fetch the signed PDF via /api/tos/signed/<id>
* ``latest_tos_signed_sha``       — first 12 chars shown in the badge tooltip
* ``latest_tos_template_version`` — e.g. "DOC-001/TOS/REV3"

For legacy canvas-signed customers (no tos_acceptances row) all three
remain null and the operator UI falls back to the legacy
``tos_pdf_path`` + ``tos_signed_at`` columns.

Tests are pure schema-level — no DB needed.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.schemas.customer import CustomerResponse


def _ts() -> datetime:
    return datetime(2026, 5, 3, 16, 31, tzinfo=timezone.utc)


def test_customer_response_includes_latest_tos_audit_fields_when_set():
    """AcroForm-flow customer: all three new fields populated."""
    payload = {
        "id": uuid.uuid4(),
        "name": "Casey Operator",
        "email": "casey@example.com",
        "phone": None,
        "address": None,
        "city": None,
        "state": None,
        "zip_code": None,
        "company": None,
        "notes": None,
        "tos_signed": True,
        "tos_signed_at": _ts(),
        "intake_completed_at": None,
        "created_at": _ts(),
        "updated_at": _ts(),
        "latest_tos_audit_id": "DOC-20260503163100-abcd1234",
        "latest_tos_signed_sha": "0" * 64,
        "latest_tos_template_version": "DOC-001/TOS/REV3",
    }
    resp = CustomerResponse(**payload)
    assert resp.latest_tos_audit_id == "DOC-20260503163100-abcd1234"
    assert resp.latest_tos_signed_sha == "0" * 64
    assert resp.latest_tos_template_version == "DOC-001/TOS/REV3"
    assert resp.tos_signed is True


def test_customer_response_defaults_latest_audit_fields_to_null():
    """Legacy canvas-signed customer or never-signed customer: the three
    new fields default to None — UI falls back to the legacy columns."""
    payload = {
        "id": uuid.uuid4(),
        "name": "Legacy Customer",
        "email": "legacy@example.com",
        "phone": None,
        "address": None,
        "city": None,
        "state": None,
        "zip_code": None,
        "company": None,
        "notes": None,
        "tos_signed": True,
        "tos_signed_at": _ts(),
        "intake_completed_at": None,
        "created_at": _ts(),
        "updated_at": _ts(),
    }
    resp = CustomerResponse(**payload)
    assert resp.latest_tos_audit_id is None
    assert resp.latest_tos_signed_sha is None
    assert resp.latest_tos_template_version is None
    # Legacy boolean still load-bearing for these.
    assert resp.tos_signed is True


def test_customer_response_serializes_to_dict_for_api_response():
    """FastAPI emits via model_dump — make sure the new fields survive
    the JSON round-trip and don't get stripped by exclusion rules."""
    cid = uuid.uuid4()
    resp = CustomerResponse(
        id=cid,
        name="Casey",
        email=None, phone=None, address=None, city=None, state=None,
        zip_code=None, company=None, notes=None,
        tos_signed=True, tos_signed_at=_ts(), intake_completed_at=None,
        created_at=_ts(), updated_at=_ts(),
        latest_tos_audit_id="DOC-XYZ",
        latest_tos_signed_sha="abc123",
        latest_tos_template_version="DOC-001/TOS/REV3",
    )
    dumped = resp.model_dump(mode="json")
    assert dumped["latest_tos_audit_id"] == "DOC-XYZ"
    assert dumped["latest_tos_signed_sha"] == "abc123"
    assert dumped["latest_tos_template_version"] == "DOC-001/TOS/REV3"
    assert dumped["id"] == str(cid)
