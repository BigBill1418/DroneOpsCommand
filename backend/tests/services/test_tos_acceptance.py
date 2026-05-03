"""Tests for the TOS-acceptance helper.

Hermetic — no DB, no SMTP, no FastAPI. Operates on the Rev 3 fixture
PDF copied from BOS-HQ prod (``data/uploads/tos/default_tos.pdf``).

Run from the repo root with the venv that has the pinned deps:

    cd backend
    pip install -r requirements.txt -r requirements-dev.txt
    pytest tests/services/test_tos_acceptance.py -v
"""

from __future__ import annotations

import io
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pypdf import PdfReader

from app.services.tos_acceptance import (
    REQUIRED_FIELDS,
    AcceptanceContext,
    ClientIdentity,
    accept_tos,
    template_has_required_fields,
    verify_signed_tos,
)

TEMPLATE = Path(__file__).parent / "fixtures" / "BarnardHQ-Terms-of-Service.pdf"


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── template_has_required_fields ─────────────────────────────────────


def test_template_has_required_fields():
    assert template_has_required_fields(TEMPLATE.read_bytes()) is True


def test_template_rejects_random_pdf():
    # Minimal but valid-looking PDF with no AcroForm at all.
    assert template_has_required_fields(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n") is False


def test_template_rejects_empty():
    assert template_has_required_fields(b"") is False


def test_template_rejects_garbage():
    assert template_has_required_fields(b"this is not a pdf") is False


# ── accept_tos round-trip ────────────────────────────────────────────


def test_round_trip():
    client = ClientIdentity("Test User", "test@example.com", "Co", "Ops")
    ctx = AcceptanceContext(
        ip="10.0.0.1", user_agent="pytest", accepted_at=_now(),
    )
    signed, record = accept_tos(client, ctx, template_bytes=TEMPLATE.read_bytes())

    assert verify_signed_tos(signed, record.signed_sha256) is True
    assert record.audit_id.startswith("DOC-")
    assert len(record.template_sha256) == 64
    assert len(record.signed_sha256) == 64
    assert record.template_sha256 != record.signed_sha256
    assert len(signed) > 0


def test_email_normalization():
    """Email is trimmed and lower-cased before going into the PDF."""
    client = ClientIdentity("X", "  MIXED@CASE.com  ", "", "")
    ctx = AcceptanceContext(ip="10.0.0.1", user_agent="pytest", accepted_at=_now())
    _, record = accept_tos(client, ctx, template_bytes=TEMPLATE.read_bytes())
    assert record.field_values["client_email"] == "mixed@case.com"


def test_missing_template_raises():
    client = ClientIdentity("X", "x@y.z", "", "")
    ctx = AcceptanceContext(ip="10.0.0.1", user_agent="", accepted_at=_now())
    with pytest.raises(ValueError, match="template"):
        accept_tos(client, ctx)


def test_empty_template_raises():
    client = ClientIdentity("X", "x@y.z", "", "")
    ctx = AcceptanceContext(ip="10.0.0.1", user_agent="", accepted_at=_now())
    with pytest.raises(ValueError, match="template"):
        accept_tos(client, ctx, template_bytes=b"")


def test_audit_id_format_and_uniqueness():
    """audit_id = DOC-<14-digit utc stamp>-<8 hex>; collisions are
    statistically negligible across two calls in the same second."""
    client = ClientIdentity("Test", "t@x.y", "", "")
    ctx = AcceptanceContext(ip="10.0.0.1", user_agent="", accepted_at=_now())
    _, r1 = accept_tos(client, ctx, template_bytes=TEMPLATE.read_bytes())
    _, r2 = accept_tos(client, ctx, template_bytes=TEMPLATE.read_bytes())
    assert r1.audit_id != r2.audit_id
    parts = r1.audit_id.split("-")
    assert parts[0] == "DOC"
    assert len(parts[1]) == 14 and parts[1].isdigit()
    assert len(parts[2]) == 8 and all(c in "0123456789abcdef" for c in parts[2])


def test_locked_fields_after_fill():
    """Every filled field has the /Ff ReadOnly bit (bit 1) set."""
    client = ClientIdentity("Test", "t@x.y", "", "")
    ctx = AcceptanceContext(ip="10.0.0.1", user_agent="", accepted_at=_now())
    signed, _ = accept_tos(client, ctx, template_bytes=TEMPLATE.read_bytes())

    r = PdfReader(io.BytesIO(signed))
    locked = 0
    for page in r.pages:
        annots = page.get("/Annots")
        if not annots:
            continue
        for ann in annots:
            obj = ann.get_object()
            if obj.get("/T"):
                ff = int(obj.get("/Ff", 0))
                assert ff & 1, f"Field {obj['/T']} is not ReadOnly (Ff={ff})"
                locked += 1
    assert locked == len(REQUIRED_FIELDS)


def test_filled_values_are_visible_in_pdf():
    """The /V (value) on every field equals what we wrote."""
    client = ClientIdentity(
        "Bill Tester", "bill@example.com", "BarnardHQ LLC", "Operator",
    )
    ctx = AcceptanceContext(
        ip="203.0.113.7", user_agent="pytest", accepted_at=_now(),
    )
    signed, record = accept_tos(client, ctx, template_bytes=TEMPLATE.read_bytes())

    r = PdfReader(io.BytesIO(signed))
    fields = r.get_fields() or {}
    assert fields["client_name"]["/V"] == "Bill Tester"
    assert fields["client_email"]["/V"] == "bill@example.com"
    assert fields["client_company"]["/V"] == "BarnardHQ LLC"
    assert fields["client_title"]["/V"] == "Operator"
    assert fields["client_ip"]["/V"] == "203.0.113.7"
    assert fields["audit_id"]["/V"] == record.audit_id
    # Timestamp should round-trip as ISO-8601 UTC with trailing Z.
    ts = fields["signed_at_utc"]["/V"]
    assert ts.endswith("Z") and "T" in ts


def test_verify_rejects_tampered_bytes():
    client = ClientIdentity("X", "x@y.z", "", "")
    ctx = AcceptanceContext(ip="10.0.0.1", user_agent="", accepted_at=_now())
    signed, record = accept_tos(client, ctx, template_bytes=TEMPLATE.read_bytes())
    tampered = signed[:-10] + b"TAMPERED!!"
    assert verify_signed_tos(tampered, record.signed_sha256) is False
