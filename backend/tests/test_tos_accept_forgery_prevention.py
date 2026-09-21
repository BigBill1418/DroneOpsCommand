"""POST /api/tos/accept is no longer an unauthenticated write (ADR-0045).

Pre-fix, this route trusted `payload.customer_id` outright: any caller who
knew or enumerated a customer UUID could POST arbitrary name/email/company
here, and the handler would both write a signed TOS acceptance AND (a few
lines later) overwrite that customer's stub name/email and flip
`tos_signed=True` — an unauthenticated write against a real customer
record, using attacker-supplied identity data.

The fix mirrors `intake.py`'s own established pattern (`get_intake_form`,
`submit_intake_form`): the intake_token is the actual credential (it's
what the customer's link carries — see `TosAcceptance.tsx`), so
`customer_id` is now *resolved from* a validated, unexpired token rather
than trusted as caller input. `TosAcceptance.tsx` never sends customer_id
without the matching token — both come from the same URL — so any request
shaped otherwise is exactly the forgery vector this closes.

These are unit tests against `accept_terms` directly (same style as
`test_tos_customer_sync.py`), plus one full-ASGI 400/404/410 check via
TestClient for the exact status codes a caller sees.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient


class _ScalarOneOrNone:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeAsyncSession:
    def __init__(self, results):
        self._results = list(results)
        self.commits = 0

    async def execute(self, _stmt):
        if not self._results:
            raise AssertionError("queue exhausted — unexpected extra SELECT")
        return _ScalarOneOrNone(self._results.pop(0))

    def add(self, obj):
        pass

    async def commit(self):
        self.commits += 1

    async def flush(self):
        pass

    async def refresh(self, obj):
        # Mirror SQLAlchemy: synthesise what a real INSERT would populate,
        # same convention as test_tos_customer_sync.py's FakeQueueAsyncSession.
        if not getattr(obj, "id", None):
            obj.id = uuid.uuid4()
        if not getattr(obj, "accepted_at", None):
            obj.accepted_at = datetime.now(timezone.utc)


def _payload(*, customer_id=None, intake_token=None, email="casey@example.com"):
    return SimpleNamespace(
        full_name="Casey Operator",
        email=email,
        company="",
        title="",
        confirm=True,
        customer_id=customer_id,
        intake_token=intake_token,
    )


def _request():
    from starlette.requests import Request

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/tos/accept",
        "headers": [(b"user-agent", b"pytest")],
        "client": ("203.0.113.99", 0),
        "query_string": b"",
        "server": ("testserver", 80),
        "scheme": "http",
        "root_path": "",
        "app": SimpleNamespace(state=SimpleNamespace()),
    }
    return Request(scope)


async def test_customer_id_without_intake_token_is_rejected():
    """The exact forgery shape: a bare customer_id, no proof of the
    intake token. Must be rejected with NO database write attempted."""
    from app.routers import tos as tos_module

    db = _FakeAsyncSession(results=[])  # no SELECT permitted at all
    payload = _payload(customer_id=uuid.uuid4(), intake_token=None)

    with pytest.raises(HTTPException) as exc_info:
        await tos_module.accept_terms(_request(), payload, db)

    assert exc_info.value.status_code == 400
    assert db.commits == 0


async def test_unknown_intake_token_is_rejected():
    from app.routers import tos as tos_module

    db = _FakeAsyncSession(results=[None])  # token resolves to no customer
    payload = _payload(customer_id=uuid.uuid4(), intake_token="does-not-exist")

    with pytest.raises(HTTPException) as exc_info:
        await tos_module.accept_terms(_request(), payload, db)

    assert exc_info.value.status_code == 404
    assert db.commits == 0


async def test_expired_intake_token_is_rejected():
    from app.routers import tos as tos_module

    cust_id = uuid.uuid4()
    expired_customer = SimpleNamespace(
        id=cust_id,
        intake_token="stale-token",
        intake_token_expires_at=datetime.utcnow() - timedelta(days=1),
    )
    db = _FakeAsyncSession(results=[expired_customer])
    payload = _payload(customer_id=cust_id, intake_token="stale-token")

    with pytest.raises(HTTPException) as exc_info:
        await tos_module.accept_terms(_request(), payload, db)

    assert exc_info.value.status_code == 410
    assert db.commits == 0


async def test_customer_id_mismatched_with_token_is_rejected():
    """The forgery-with-a-valid-token-for-a-DIFFERENT-customer shape: the
    caller holds a legitimate token for customer A but claims customer_id
    B in the payload. Must not silently prefer either value."""
    from app.routers import tos as tos_module

    token_owner_id = uuid.uuid4()
    claimed_id = uuid.uuid4()
    assert token_owner_id != claimed_id
    token_customer = SimpleNamespace(
        id=token_owner_id,
        intake_token="valid-token-for-A",
        intake_token_expires_at=None,
    )
    db = _FakeAsyncSession(results=[token_customer])
    payload = _payload(customer_id=claimed_id, intake_token="valid-token-for-A")

    with pytest.raises(HTTPException) as exc_info:
        await tos_module.accept_terms(_request(), payload, db)

    assert exc_info.value.status_code == 404
    assert db.commits == 0


async def test_cold_visitor_no_token_no_customer_id_still_accepted(tmp_path):
    """The documented legitimate anonymous path (TosAcceptanceRequest's
    own docstring) must keep working: no token, no customer_id, no
    existing customer record touched."""
    from app.routers import tos as tos_module

    record = SimpleNamespace(
        audit_id="aud_cold",
        template_sha256="t" * 64,
        signed_sha256="s" * 64,
        field_values={
            "client_name": "Cold Visitor",
            "client_email": "cold@example.com",
            "client_company": "",
            "client_title": "",
            "client_ip": "203.0.113.99",
        },
    )
    db = _FakeAsyncSession(results=[])
    payload = _payload(customer_id=None, intake_token=None, email="cold@example.com")

    with patch.object(tos_module, "get_active_tos_template",
                       return_value=SimpleNamespace(bytes=b"%PDF-1.4", version="rev3")), \
         patch.object(tos_module, "accept_tos", return_value=(b"x", record)), \
         patch.object(tos_module, "signed_pdf_dir", return_value=tmp_path), \
         patch.object(tos_module, "send_signed_tos_to_both_parties",
                      new=AsyncMock(return_value=None)):
        resp = await tos_module.accept_terms(_request(), payload, db)

    assert resp.audit_id == "aud_cold"


# ── Full-ASGI status-code check (the caller's-eye view) ───────────────


def _build_minimal_app(db):
    from app.database import get_db
    from app.routers.tos import router as tos_router

    app = FastAPI()
    app.include_router(tos_router)

    async def _get_db_override():
        yield db

    app.dependency_overrides[get_db] = _get_db_override
    return app


def test_route_returns_400_for_customer_id_without_token():
    db = _FakeAsyncSession(results=[])
    app = _build_minimal_app(db)
    client = TestClient(app)

    response = client.post(
        "/api/tos/accept",
        json={
            "full_name": "Attacker",
            "email": "attacker@example.com",
            "company": "",
            "title": "",
            "confirm": True,
            "customer_id": str(uuid.uuid4()),
            "intake_token": None,
        },
    )
    assert response.status_code == 400
