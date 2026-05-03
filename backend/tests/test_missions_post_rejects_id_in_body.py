"""v2.67.0 (Mission Hub redesign, spec §4) — defensive POST guard.

The duplicate-mission incident on 2026-05-03 (operator opened an
existing mission for edit, hit Save on Details, the system POST'd a
new mission instead of PUT'ing an update) is now physically blocked at
the route boundary: ``POST /api/missions`` rejects any body that
includes an ``id`` field with HTTP 400.

These tests exercise the route through the full FastAPI ASGI stack
(``TestClient`` + ``app.dependency_overrides``) — same pattern as
``test_tos_accept_route_body.py`` (per ADR-0013, no SimpleNamespace
bypass tests for customer-or-operator-data-touching routes).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient


# ── Fakes mimicking AsyncSession + ORM Mission row ───────────────────


class _ScalarOneOrNone:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def scalar_one(self):
        return self._value

    def scalar(self):
        return self._value


class _MissionStub:
    """Quacks like a SQLAlchemy ``Mission`` row for response serialization.

    Includes every column ``MissionResponse`` reads, defaulted to safe
    test values. Relationships are empty lists (the route eagerly loads
    ``flights``, ``images``, ``customer`` after ``flush``; we feed empty
    lists which Pydantic accepts).
    """

    def __init__(self, **overrides: Any) -> None:
        from app.models.mission import MissionStatus, MissionType

        now = datetime.utcnow()
        self.id = uuid.uuid4()
        self.customer_id = None
        self.title = "Test Mission"
        self.mission_type = MissionType.OTHER
        self.description = None
        self.mission_date = None
        self.location_name = None
        self.area_coordinates = None
        self.status = MissionStatus.DRAFT
        self.is_billable = False
        self.unas_folder_path = None
        self.download_link_url = None
        self.download_link_expires_at = None
        self.client_notes = None
        self.created_at = now
        self.updated_at = now
        self.flights: list = []
        self.images: list = []
        self.customer = None
        for key, value in overrides.items():
            setattr(self, key, value)


class _FakeSession:
    """Async session double for POST /api/missions tests.

    ``execute_results`` is a queue of values returned by ``execute``
    (each wrapped in ``_ScalarOneOrNone``). Tests pre-load the queue in
    the order the route will call execute:
      1. dup-check ``COUNT(*)`` query — only fires if title + customer_id
         are present
      2. Re-query with eager loads — returns the inserted Mission stub
    """

    def __init__(self, execute_results: list[Any]):
        self._queue = list(execute_results)
        self.added: list = []
        self.flushed = 0

    async def execute(self, _stmt):
        if not self._queue:
            return _ScalarOneOrNone(None)
        return _ScalarOneOrNone(self._queue.pop(0))

    def add(self, obj):
        # Mission(...) constructor on the route side; capture for inspection
        self.added.append(obj)
        # Give it a UUID like the DB would (so eager re-query works)
        if not getattr(obj, "id", None):
            obj.id = uuid.uuid4()

    async def flush(self):
        self.flushed += 1

    async def refresh(self, obj):  # pragma: no cover - unused on POST path
        pass

    async def commit(self):  # pragma: no cover - get_db handles commit
        pass

    async def rollback(self):  # pragma: no cover
        pass

    async def close(self):  # pragma: no cover
        pass

    @property
    def is_active(self) -> bool:
        return True


def _build_app(execute_results: list[Any] | None = None) -> tuple[FastAPI, _FakeSession]:
    """Mount only the missions router with a fake DB + bypassed auth.

    Mirrors the pattern in ``test_tos_accept_route_body.py``: full ASGI
    stack but no Postgres/Redis/SMTP/Sentry pulled in.
    """
    from app.auth.jwt import get_current_user
    from app.database import get_db
    from app.routers.missions import router as missions_router

    app = FastAPI()
    app.include_router(missions_router)

    fake_db = _FakeSession(execute_results or [])

    async def _get_db_override():
        yield fake_db

    async def _user_override():
        # Quacks like a User: only ``username`` is read by the route's
        # log lines.
        from types import SimpleNamespace
        return SimpleNamespace(username="op@test.example.com", id=uuid.uuid4())

    app.dependency_overrides[get_db] = _get_db_override
    app.dependency_overrides[get_current_user] = _user_override
    return app, fake_db


# ── Tests ────────────────────────────────────────────────────────────


def test_post_with_id_field_in_body_returns_400():
    """A request that smuggles ``id`` is the exact bug class the Hub
    redesign was triggered by — must 400 at the route boundary.
    """
    app, _db = _build_app()
    client = TestClient(app)

    body = {
        "id": str(uuid.uuid4()),  # <- forbidden
        "title": "A duplicate-class POST",
        "mission_type": "other",
    }
    resp = client.post("/api/missions", json=body)

    assert resp.status_code == 400, (
        f"Expected 400 (id-in-body rejected); got {resp.status_code} body={resp.text}"
    )
    assert "must not include 'id' in body" in resp.json()["detail"]


def test_post_with_id_field_set_to_null_still_rejected():
    """Even ``id=null`` is rejected — any presence of the key indicates
    the client thinks they should be sending an id, which is wrong.
    """
    app, _db = _build_app()
    client = TestClient(app)

    body = {
        "id": None,
        "title": "Edge case: id=null",
        "mission_type": "other",
    }
    resp = client.post("/api/missions", json=body)

    assert resp.status_code == 400, resp.text
    assert "must not include 'id'" in resp.json()["detail"]


def test_post_without_id_returns_201():
    """Sanity: a normal Hub-modal POST (no id field) succeeds.

    No customer_id in body → dup-check is skipped → only the eager
    re-query fires. Pre-load just the stub.
    """
    stub = _MissionStub(title="Hub modal POST")
    app, _db = _build_app(execute_results=[stub])
    client = TestClient(app)

    # Patch the auto-portal-email helper so we don't need SMTP/Brevo.
    with patch("app.routers.missions._send_portal_email_for_mission", new=_no_op_async):
        resp = client.post(
            "/api/missions",
            json={"title": "Hub modal POST", "mission_type": "other"},
        )

    assert resp.status_code == 201, f"got {resp.status_code} body={resp.text}"
    body = resp.json()
    assert body["title"] == "Hub modal POST"
    assert body["status"] == "draft"
    assert body["id"]  # server-assigned id


def test_post_with_invalid_json_returns_400():
    """A malformed body must 400 (the route reads the raw body BEFORE
    Pydantic validation; un-parseable JSON would otherwise crash with 500)."""
    app, _db = _build_app()
    client = TestClient(app)

    resp = client.post(
        "/api/missions",
        data="not json at all",
        headers={"Content-Type": "application/json"},
    )

    assert resp.status_code == 400, resp.text


def test_post_with_non_object_body_returns_400():
    """JSON arrays / strings / numbers are not valid mission bodies."""
    app, _db = _build_app()
    client = TestClient(app)

    resp = client.post("/api/missions", json=[1, 2, 3])

    assert resp.status_code == 400, resp.text


async def _no_op_async(*_args, **_kwargs):  # pragma: no cover - helper
    return None
