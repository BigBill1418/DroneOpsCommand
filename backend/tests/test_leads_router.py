"""ADR-0050 /api/leads router."""
import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.services import lead_source
from app.services.lead_source import LeadNotFound, LeadSourceUnavailable

LEAD = {"key": "web-1", "name": "Ann", "email": "Ann@Acme.com ", "phone": "", "organization": "Acme",
        "service": "inspection", "details": "Roof", "created_at": "2026-10-01T00:00:00Z",
        "stage": "new", "is_open": True}


class _Result:
    def __init__(self, value):
        self._v = value

    def scalars(self):
        return self

    def first(self):
        return self._v


class _FakeSession:
    def __init__(self, results: list[Any]):
        self._results = list(results)
        self.statements = []

    async def execute(self, stmt):
        self.statements.append(stmt)
        return _Result(self._results.pop(0) if self._results else None)


class FakeClient:
    def __init__(self, exc=None, leads=None, lead=None):
        self.exc, self.leads, self.lead = exc, leads or [LEAD], lead or LEAD
        self.list_args = None

    async def list_leads(self, q=None, include_closed=False, limit=25):
        if self.exc:
            raise self.exc
        self.list_args = (q, include_closed, limit)
        return self.leads

    async def get_lead(self, key):
        if self.exc:
            raise self.exc
        return self.lead


def _app(monkeypatch, *, enabled=True, client=None, db_results=None, writeback=None):
    from app.auth.jwt import get_current_user
    from app.database import get_db
    from app.routers import leads as leads_router

    monkeypatch.setattr(lead_source.settings, "leads_api_base", "http://x" if enabled else "")
    monkeypatch.setattr(lead_source.settings, "leads_api_token", "t" if enabled else "")
    monkeypatch.setattr(leads_router, "get_client", lambda: client or FakeClient())
    if writeback is not None:
        monkeypatch.setattr(leads_router, "run_lead_writeback", writeback)
    app = FastAPI()
    app.include_router(leads_router.router)
    fake_db = _FakeSession(db_results or [])

    async def _db():
        yield fake_db

    async def _user():
        return SimpleNamespace(username="op@test", id=uuid.uuid4())

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user] = _user
    return TestClient(app), fake_db


def test_status_reports_enabled(monkeypatch):
    c, _ = _app(monkeypatch, enabled=False)
    assert c.get("/api/leads/status").json() == {"enabled": False}
    c, _ = _app(monkeypatch, enabled=True)
    assert c.get("/api/leads/status").json() == {"enabled": True}


@pytest.mark.parametrize("path", ["/api/leads", "/api/leads/web-1"])
def test_disabled_routes_404(monkeypatch, path):
    c, _ = _app(monkeypatch, enabled=False)
    assert c.get(path).status_code == 404


def test_list_passes_filters(monkeypatch):
    fc = FakeClient()
    c, _ = _app(monkeypatch, client=fc)
    r = c.get("/api/leads", params={"q": "acme", "include_closed": "true"})
    assert r.status_code == 200 and r.json() == {"leads": [LEAD]}
    assert fc.list_args == ("acme", True, 25)


def test_list_unavailable_is_503(monkeypatch):
    c, _ = _app(monkeypatch, client=FakeClient(exc=LeadSourceUnavailable("down")))
    r = c.get("/api/leads")
    assert r.status_code == 503 and r.json() == {"detail": "Leads unavailable"}


def test_get_lead_with_matching_customer(monkeypatch):
    cust = SimpleNamespace(id=uuid.uuid4(), name="Ann Existing")
    c, db = _app(monkeypatch, db_results=[cust])
    body = c.get("/api/leads/web-1").json()
    assert body["lead"]["key"] == "web-1"
    assert body["matching_customer"] == {"id": str(cust.id), "name": "Ann Existing"}
    # The lookup normalises case + whitespace ("Ann@Acme.com " → "ann@acme.com").
    compiled = str(db.statements[0].compile(compile_kwargs={"literal_binds": True}))
    assert "lower(customers.email)" in compiled and "'ann@acme.com'" in compiled


def test_get_lead_without_email_skips_match(monkeypatch):
    c, db = _app(monkeypatch, client=FakeClient(lead={**LEAD, "email": ""}))
    assert c.get("/api/leads/web-1").json()["matching_customer"] is None
    assert db.statements == []


def test_get_lead_bad_key_404(monkeypatch):
    c, _ = _app(monkeypatch)
    assert c.get("/api/leads/drop-table").status_code == 404


def test_get_lead_not_found_404(monkeypatch):
    c, _ = _app(monkeypatch, client=FakeClient(exc=LeadNotFound("x")))
    assert c.get("/api/leads/web-9").status_code == 404


def test_mark_won_retry(monkeypatch):
    calls = []

    async def ok(mission_id, key, **kw):
        calls.append((mission_id, key))
        return True

    c, _ = _app(monkeypatch, writeback=ok)
    mid = uuid.uuid4()
    r = c.post(f"/api/leads/web-1/mark-won", params={"mission_id": str(mid)})
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert calls == [(mid, "web-1")]


def test_mark_won_retry_failure_502(monkeypatch):
    async def fail(mission_id, key, **kw):
        return False

    c, _ = _app(monkeypatch, writeback=fail)
    r = c.post("/api/leads/web-1/mark-won", params={"mission_id": str(uuid.uuid4())})
    assert r.status_code == 502
