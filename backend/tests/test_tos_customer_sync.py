"""v2.66.0 Fix 1 — TOS acceptance syncs customer name/email back to
the customer row when the no-email intake path stubbed it out.

Fix 6 piggy-back — Pydantic `EmailStr` validation already lives on
`TosAcceptanceRequest.email`; we exercise the 422 path here too so
nothing regresses if someone changes the schema later.

These tests are hermetic: they mock the AcroForm fill, signed-PDF write,
and email send. The router's customer-sync logic is the unit under test.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── Fakes ────────────────────────────────────────────────────────────
class _ScalarOneOrNone:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class FakeQueueAsyncSession:
    """Async session returning a queue of canned execute() results.

    The accept_terms route fires:
      1. SELECT Customer WHERE id=customer_id (after persisting tos row)
    plus an internal db.add + db.commit + db.refresh on the TosAcceptance.
    """

    def __init__(self, results):
        self._results = list(results)
        self.added = []
        self.commits = 0
        self.refresh_count = 0

    async def execute(self, _stmt):
        if not self._results:
            raise AssertionError("queue exhausted")
        return _ScalarOneOrNone(self._results.pop(0))

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1

    async def flush(self):
        pass

    async def refresh(self, obj):
        self.refresh_count += 1
        # Mirror SQLAlchemy: synthesise an id + accepted_at so the
        # response model serializes.
        if not getattr(obj, "id", None):
            obj.id = uuid.uuid4()
        if not getattr(obj, "accepted_at", None):
            obj.accepted_at = datetime.now(timezone.utc)


def _mk_payload(*, customer_id, email, full_name="Casey Operator"):
    return SimpleNamespace(
        full_name=full_name,
        email=email,
        company="",
        title="",
        confirm=True,
        customer_id=customer_id,
        intake_token=None,
    )


def _mk_request():
    """Construct a real Starlette Request — slowapi's limiter rejects
    SimpleNamespace stubs at runtime (`isinstance(request, Request)`
    check). Minimal ASGI scope is all that's needed for our route, which
    only reads request.client + request.headers."""
    from starlette.requests import Request

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/tos/accept",
        "headers": [
            (b"x-forwarded-for", b"203.0.113.1"),
            (b"user-agent", b"pytest"),
        ],
        "client": ("127.0.0.1", 0),
        "query_string": b"",
        "server": ("testserver", 80),
        "scheme": "http",
        "root_path": "",
        # slowapi reads request.app.state.limiter for some code paths;
        # provide a stub so the limiter wrapper has a place to look.
        "app": SimpleNamespace(state=SimpleNamespace()),
    }
    return Request(scope)


def _mk_template():
    return SimpleNamespace(
        bytes=b"%PDF-1.4 fake",
        version="rev3",
    )


def _mk_record():
    return SimpleNamespace(
        audit_id="aud_test_123",
        template_sha256="t" * 64,
        signed_sha256="s" * 64,
        field_values={
            "client_name": "Casey Operator",
            "client_email": "casey@example.com",
            "client_company": "",
            "client_title": "",
            "client_ip": "203.0.113.1",
        },
    )


# ── Fix 1: customer.email NULL → set from payload ─────────────────────
@pytest.mark.asyncio
async def test_tos_accept_syncs_email_when_customer_email_null(tmp_path):
    """No-email intake path: customer was stubbed with email=None and
    name='Pending Intake YYYY-MM-DD'. After TOS accept, both fields
    should be populated from the payload."""
    from app.routers import tos as tos_module

    cust_id = uuid.uuid4()
    customer = SimpleNamespace(
        id=cust_id,
        email=None,
        name="Pending Intake 2026-05-03",
    )

    db = FakeQueueAsyncSession(results=[customer])
    payload = _mk_payload(customer_id=cust_id, email="casey@example.com")

    record = _mk_record()
    signed_bytes = b"%PDF-1.4 signed"

    with patch.object(tos_module, "get_active_tos_template", return_value=_mk_template()), \
         patch.object(tos_module, "accept_tos", return_value=(signed_bytes, record)), \
         patch.object(tos_module, "signed_pdf_dir", return_value=tmp_path), \
         patch.object(tos_module, "send_signed_tos_to_both_parties",
                      new=AsyncMock(return_value=None)):
        resp = await tos_module.accept_terms(_mk_request(), payload, db)

    # Customer was synced
    assert customer.email == "casey@example.com"
    assert customer.name == "Casey Operator"
    # commit() was called twice: once for tos row, once for the sync.
    assert db.commits == 2
    # Response surfaces the audit_id.
    assert resp.audit_id == record.audit_id


@pytest.mark.asyncio
async def test_tos_accept_does_not_overwrite_existing_email(tmp_path):
    """If customer.email is already set, do NOT overwrite it from the
    TOS payload. Only the 'Pending Intake …' name placeholder is
    replaced. Email stays as-is."""
    from app.routers import tos as tos_module

    cust_id = uuid.uuid4()
    customer = SimpleNamespace(
        id=cust_id,
        email="prior@example.com",  # already populated
        name="Real Name",            # not the placeholder
    )

    db = FakeQueueAsyncSession(results=[customer])
    payload = _mk_payload(customer_id=cust_id, email="new@example.com")

    record = _mk_record()
    with patch.object(tos_module, "get_active_tos_template", return_value=_mk_template()), \
         patch.object(tos_module, "accept_tos", return_value=(b"x", record)), \
         patch.object(tos_module, "signed_pdf_dir", return_value=tmp_path), \
         patch.object(tos_module, "send_signed_tos_to_both_parties",
                      new=AsyncMock(return_value=None)):
        await tos_module.accept_terms(_mk_request(), payload, db)

    # Email NOT overwritten, name NOT overwritten.
    assert customer.email == "prior@example.com"
    assert customer.name == "Real Name"
    # Single commit (tos row only — no sync needed).
    assert db.commits == 1


@pytest.mark.asyncio
async def test_tos_accept_replaces_pending_intake_name_only(tmp_path):
    """Customer has a placeholder name + a real email already.
    Only the name should be synced from the typed full_name."""
    from app.routers import tos as tos_module

    cust_id = uuid.uuid4()
    customer = SimpleNamespace(
        id=cust_id,
        email="prior@example.com",
        name="Pending Intake 2026-05-03",
    )

    db = FakeQueueAsyncSession(results=[customer])
    payload = _mk_payload(
        customer_id=cust_id, email="new@example.com",
        full_name="Real Customer Name",
    )

    record = _mk_record()
    with patch.object(tos_module, "get_active_tos_template", return_value=_mk_template()), \
         patch.object(tos_module, "accept_tos", return_value=(b"x", record)), \
         patch.object(tos_module, "signed_pdf_dir", return_value=tmp_path), \
         patch.object(tos_module, "send_signed_tos_to_both_parties",
                      new=AsyncMock(return_value=None)):
        await tos_module.accept_terms(_mk_request(), payload, db)

    assert customer.name == "Real Customer Name"
    assert customer.email == "prior@example.com"  # email left alone
    assert db.commits == 2


@pytest.mark.asyncio
async def test_tos_accept_no_customer_id_skips_sync(tmp_path):
    """Cold-visit acceptance (no customer_id in payload). The customer-
    sync block must be a complete no-op."""
    from app.routers import tos as tos_module

    db = FakeQueueAsyncSession(results=[])  # no SELECT should happen
    payload = _mk_payload(customer_id=None, email="cold@example.com")

    record = _mk_record()
    with patch.object(tos_module, "get_active_tos_template", return_value=_mk_template()), \
         patch.object(tos_module, "accept_tos", return_value=(b"x", record)), \
         patch.object(tos_module, "signed_pdf_dir", return_value=tmp_path), \
         patch.object(tos_module, "send_signed_tos_to_both_parties",
                      new=AsyncMock(return_value=None)):
        await tos_module.accept_terms(_mk_request(), payload, db)

    assert db.commits == 1  # only the tos row commit


# ── Fix 6: EmailStr is enforced ───────────────────────────────────────
def test_tos_accept_request_rejects_garbage_email():
    """Pydantic EmailStr keeps obviously bad input out of the route."""
    from pydantic import ValidationError

    from app.schemas.tos_acceptance import TosAcceptanceRequest

    with pytest.raises(ValidationError):
        TosAcceptanceRequest(
            full_name="Casey Operator",
            email="not-an-email",
            confirm=True,
        )


def test_tos_accept_request_accepts_valid_email():
    from app.schemas.tos_acceptance import TosAcceptanceRequest

    obj = TosAcceptanceRequest(
        full_name="Casey Operator",
        email="casey@example.com",
        confirm=True,
    )
    assert obj.email == "casey@example.com"
