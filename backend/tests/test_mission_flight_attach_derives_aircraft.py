"""POST /api/missions/{id}/flights — aircraft_id is derived server-side.

The mission editor no longer asks the operator to pick an aircraft for
each flight (or to maintain a separate "aircraft used" list). The flight
log already carries its drone — matched to the fleet at upload time by
serial/model — so on attach the backend reads ``Flight.aircraft_id`` and
ignores whatever the client sends (or doesn't send).

These tests pin that contract end-to-end through the FastAPI ASGI stack,
mirroring the fake-session pattern in
``test_missions_post_rejects_id_in_body.py``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient


class _ScalarOneOrNone:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def scalar_one(self):
        return self._value

    def scalar(self):
        return self._value

    # ADR-0026: the idempotent-attach guard reads with .scalars().first().
    def scalars(self):
        return self

    def first(self):
        return self._value

    def all(self):
        return [] if self._value is None else [self._value]


class _MissionStub:
    def __init__(self) -> None:
        self.id = uuid.uuid4()


class _FlightStub:
    """Quacks like a ``Flight`` ORM row for the add_flight handler."""

    def __init__(self, aircraft_id: uuid.UUID | None) -> None:
        self.id = uuid.uuid4()
        self.aircraft_id = aircraft_id
        self.name = "Test Flight"
        self.drone_model = "Mavic 3 Pro"
        self.drone_serial = "SN-TEST-1"
        self.start_time = datetime.utcnow()
        self.duration_secs = 600.0
        self.total_distance = 1200.0
        self.max_altitude = 100.0
        self.max_speed = 15.0
        self.home_lat = 42.3
        self.home_lon = -71.1
        self.point_count = 600
        self.gps_track = []


class _FakeSession:
    """Async session double — feeds a queue of execute() results."""

    def __init__(self, execute_results: list[Any]):
        self._queue = list(execute_results)
        self.added: list = []
        self.flushed = 0

    async def execute(self, _stmt):
        if not self._queue:
            return _ScalarOneOrNone(None)
        return _ScalarOneOrNone(self._queue.pop(0))

    def add(self, obj):
        self.added.append(obj)
        if not getattr(obj, "id", None):
            obj.id = uuid.uuid4()
        if not hasattr(obj, "added_at") or obj.added_at is None:
            obj.added_at = datetime.utcnow()

    async def flush(self):
        self.flushed += 1

    async def refresh(self, obj):
        # Production refresh would re-fetch from the DB; the test stub
        # already has everything the response serializer needs.
        if not hasattr(obj, "aircraft"):
            obj.aircraft = None

    async def commit(self):  # pragma: no cover
        pass

    async def rollback(self):  # pragma: no cover
        pass

    async def close(self):  # pragma: no cover
        pass

    @property
    def is_active(self) -> bool:
        return True


def _build_app(execute_results: list[Any]) -> tuple[FastAPI, _FakeSession]:
    from app.auth.jwt import get_current_user
    from app.database import get_db
    from app.routers.missions import router as missions_router

    app = FastAPI()
    app.include_router(missions_router)

    fake_db = _FakeSession(execute_results)

    async def _get_db_override():
        yield fake_db

    async def _user_override():
        from types import SimpleNamespace
        return SimpleNamespace(username="op@test.example.com", id=uuid.uuid4())

    app.dependency_overrides[get_db] = _get_db_override
    app.dependency_overrides[get_current_user] = _user_override
    return app, fake_db


def test_attach_derives_aircraft_from_flight_log():
    """Client omits aircraft_id → server fills it from Flight.aircraft_id."""
    fleet_aircraft_id = uuid.uuid4()
    mission = _MissionStub()
    flight = _FlightStub(aircraft_id=fleet_aircraft_id)

    app, db = _build_app(execute_results=[mission, None, flight])
    client = TestClient(app)

    resp = client.post(
        f"/api/missions/{mission.id}/flights",
        json={
            "flight_id": str(flight.id),
            "opendronelog_flight_id": None,
            "flight_data_cache": {},
            # NB: no aircraft_id in body — the operator never picks it
        },
    )

    assert resp.status_code == 201, resp.text
    assert len(db.added) == 1
    mf = db.added[0]
    assert mf.aircraft_id == fleet_aircraft_id, (
        f"server must derive aircraft_id from Flight.aircraft_id; "
        f"got {mf.aircraft_id!r}"
    )


def test_attach_overrides_client_supplied_aircraft_id():
    """Even if a stale client sends aircraft_id, the server's derived
    value from the flight log wins. Single source of truth — the flight
    log is authoritative, not the editor screen."""
    fleet_aircraft_id = uuid.uuid4()
    stale_client_value = uuid.uuid4()
    mission = _MissionStub()
    flight = _FlightStub(aircraft_id=fleet_aircraft_id)

    app, db = _build_app(execute_results=[mission, None, flight])
    client = TestClient(app)

    resp = client.post(
        f"/api/missions/{mission.id}/flights",
        json={
            "flight_id": str(flight.id),
            "opendronelog_flight_id": None,
            "aircraft_id": str(stale_client_value),
            "flight_data_cache": {},
        },
    )

    assert resp.status_code == 201, resp.text
    mf = db.added[0]
    assert mf.aircraft_id == fleet_aircraft_id
    assert mf.aircraft_id != stale_client_value


def test_attach_with_unmatched_flight_log_stores_null_aircraft():
    """If the flight log itself has no fleet match, aircraft_id is null
    — never the stale client value, never a guess."""
    mission = _MissionStub()
    flight = _FlightStub(aircraft_id=None)  # unmatched

    app, db = _build_app(execute_results=[mission, None, flight])
    client = TestClient(app)

    resp = client.post(
        f"/api/missions/{mission.id}/flights",
        json={
            "flight_id": str(flight.id),
            "opendronelog_flight_id": None,
            "aircraft_id": str(uuid.uuid4()),  # stale, must be ignored
            "flight_data_cache": {},
        },
    )

    assert resp.status_code == 201, resp.text
    mf = db.added[0]
    assert mf.aircraft_id is None
