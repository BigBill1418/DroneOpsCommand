"""ADR-0050 — mission create/update schedules the lead `won` write-back after commit."""
import uuid
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.services import lead_source


class _Result:
    def __init__(self, v):
        self._v = v

    def scalar(self):
        return 0

    def scalar_one(self):
        return self._v

    def scalar_one_or_none(self):
        return self._v


class _FakeSession:
    def __init__(self):
        self.added: list[Any] = []
        self.events: list[str] = []

    def add(self, obj):
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()
        self.added.append(obj)

    async def flush(self):
        self.events.append("flush")

    async def commit(self):
        self.events.append("commit")

    async def refresh(self, obj):
        pass

    async def execute(self, stmt):
        # Re-query after POST: return a fully-populated stub mirroring the ORM row
        # (the fake never runs DB defaults like created_at) — same approach as
        # test_missions_post_rejects_id_in_body.py.
        obj = self.added[-1] if self.added else None
        if obj is not None and not isinstance(obj, SimpleNamespace):
            obj = _mission_obj(id=obj.id, title=obj.title, customer_id=obj.customer_id,
                               source=obj.source, source_ref=obj.source_ref)
        return _Result(obj)


def _mission_obj(**over):
    now = datetime(2026, 10, 5)
    base = dict(id=uuid.uuid4(), customer_id=None, title="T", mission_type="other", description=None,
                mission_date=None, location_name=None, area_coordinates=None, status="draft",
                is_billable=False, source="website", source_ref="web-1", unas_folder_path=None,
                download_link_url=None, download_link_expires_at=None, client_notes=None,
                lead_writeback_at=None, created_at=now, updated_at=now, flights=[], images=[])
    base.update(over)
    return SimpleNamespace(**base)


@pytest.fixture
def harness(monkeypatch):
    from app.auth.jwt import get_current_user
    from app.database import get_db
    from app.routers import missions as missions_router

    monkeypatch.setattr(lead_source.settings, "leads_api_base", "http://x")
    monkeypatch.setattr(lead_source.settings, "leads_api_token", "t")
    calls = []

    async def fake_writeback(mission_id, key, **kw):
        calls.append((mission_id, key))
        return True

    monkeypatch.setattr(missions_router, "run_lead_writeback", fake_writeback)
    app = FastAPI()
    app.include_router(missions_router.router)
    db = _FakeSession()

    async def _db():
        yield db

    async def _user():
        return SimpleNamespace(username="op@test", id=uuid.uuid4())

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user] = _user
    return TestClient(app), db, calls, missions_router


def test_create_with_lead_key_commits_then_schedules(harness):
    client, db, calls, _ = harness
    r = client.post("/api/missions", json={"title": "Roof — Acme", "source": "website", "source_ref": "web-1"})
    assert r.status_code == 201, r.text
    assert "commit" in db.events
    assert len(calls) == 1 and calls[0][1] == "web-1"


def test_create_without_lead_key_does_not_schedule(harness):
    client, db, calls, _ = harness
    r = client.post("/api/missions", json={"title": "Walk-in", "source": "phone"})
    assert r.status_code == 201, r.text
    assert calls == [] and "commit" not in db.events


def test_create_with_feature_off_does_not_schedule(harness, monkeypatch):
    client, db, calls, _ = harness
    monkeypatch.setattr(lead_source.settings, "leads_api_token", "")
    r = client.post("/api/missions", json={"title": "X", "source_ref": "web-1"})
    assert r.status_code == 201 and calls == []


def test_update_changing_source_ref_schedules_and_clears_stamp(harness):
    client, db, calls, _ = harness
    existing = _mission_obj(source_ref=None, lead_writeback_at=datetime(2026, 1, 1))
    db.added.append(existing)
    r = client.put(f"/api/missions/{existing.id}", json={"source_ref": "cold-7"})
    assert r.status_code == 200, r.text
    assert existing.lead_writeback_at is None
    assert calls == [(existing.id, "cold-7")]


def test_update_same_source_ref_does_not_reschedule(harness):
    client, db, calls, _ = harness
    existing = _mission_obj(source_ref="web-1", lead_writeback_at=datetime(2026, 1, 1))
    db.added.append(existing)
    r = client.put(f"/api/missions/{existing.id}", json={"source_ref": "web-1", "title": "Renamed"})
    assert r.status_code == 200, r.text
    assert calls == []
