"""POST /api/missions/{id}/flights — the junction never copies the aircraft.

The mission editor no longer asks the operator to pick an aircraft for
each flight (or to maintain a separate "aircraft used" list). The flight
log already carries its drone — matched to the fleet at upload time by
serial/model — so the flight log is the single source of truth and the
client-sent aircraft_id is always ignored.

ADR-0035 (flight-attach unification, Phase 1) changed the write contract:
the backend NO LONGER copies ``Flight.aircraft_id`` onto the junction
(``mission_flights.aircraft_id``) for native flights. The copy was a
snapshot taken at attach time and went STALE when the fleet serial was
registered later — that is exactly the ADR-0033 Avata incident. Reports now
resolve the aircraft from the LIVE ``Flight.aircraft`` (via ``flight_id``),
which can never be stale, so the junction copy is redundant. The junction
``aircraft_id`` is therefore left NULL for every native attach (matched or
not) — never the live value, never the client value.

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
        self.drone_name = "Bird One"          # ADR-0028 L6
        self.drone_serial = "SN-TEST-1"
        self.battery_serial = "BAT-TEST-1"    # ADR-0028 L6
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


def test_attach_does_not_copy_aircraft_onto_junction():
    """Client omits aircraft_id → server leaves the junction aircraft_id NULL
    (ADR-0035). The live Flight.aircraft_id stays authoritative for reads; the
    junction never carries a copy that could go stale."""
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
    assert mf.aircraft_id is None, (
        f"native attach must NOT copy the aircraft onto the junction "
        f"(ADR-0035 — reports resolve it live); got {mf.aircraft_id!r}"
    )
    # The native flight linkage itself is preserved — that is how the report
    # joins back to the live Flight.aircraft.
    assert mf.flight_id == flight.id


def test_attach_ignores_client_supplied_aircraft_id():
    """Even if a stale client sends aircraft_id, it is discarded — the junction
    stays NULL. Single source of truth is the live flight log, not the editor
    screen and not a snapshot on the junction (ADR-0035)."""
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
    assert mf.aircraft_id is None
    assert mf.aircraft_id != stale_client_value


def test_attach_with_unmatched_flight_log_stores_null_aircraft():
    """A native flight with no fleet match also leaves the junction NULL —
    never the stale client value, never a guess. (Same NULL as the matched
    case now: the junction copy is retired for native rows, ADR-0035.)"""
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
