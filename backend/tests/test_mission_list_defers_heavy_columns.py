"""Regression guard for the 2026-06-11 audit findings P0-1 / P1-2.

``MissionFlight.flight`` is ``lazy="selectin"`` at the mapper level, so any
bare ``selectinload(Mission.flights)`` cascaded into loading every attached
``Flight`` row IN FULL — gps_track (~19k GPS points), telemetry time-series
and raw_metadata included. That hit two surfaces:

  * GET /api/financials/summary loads ALL billable missions with no limit
    and only ever reads ``mf.aircraft.model_name`` — yet it decompressed the
    entire TOASTed track history on every Financials dashboard load (P0-1).
  * GET /api/missions (list) + /api/missions/{id} (detail) serialize
    ``MissionFlightResponse``, which never includes ``flight`` — the display
    fields (track included) come from ``flight_data_cache`` (P1-2).

Under the 1 GiB uvicorn cgroup cap this is the exact OOM class ADR-0019
fixed on the flight-library list. The fix scopes loading per-query:
``raiseload(MissionFlight.flight)`` on every mission-graph query (fails
loudly instead of silently re-introducing the OOM), plus — financials only —
``defer(MissionFlight.flight_data_cache)`` and ``raiseload(Mission.images)``
since the aggregation loop reads neither.

These tests assert against the ACTUAL production loader options (both
routers build their queries from the helpers imported here) and exercise the
list route through the full ASGI stack with a stub that booby-traps
``.flight`` — no live database required, per the hermetic house pattern.
"""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("JWT_SECRET_KEY", "test_secret_key_unused_in_unit_tests")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")

import uuid
from datetime import datetime
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models.mission import Mission, MissionFlight
from app.routers.financials import _billable_mission_load_options
from app.routers.missions import _mission_graph_options


# ── Loader-option introspection ──────────────────────────────────────


def _strategy_map(options) -> dict[tuple[str, ...], dict]:
    """Flatten Load objects into {relationship/column path: strategy dict}.

    A loader path alternates Mapper → property → Mapper → property…;
    the odd slots are the properties whose ``.key`` names the hop
    (e.g. ``("flights", "flight")``).
    """
    out: dict[tuple[str, ...], dict] = {}
    for load in options:
        for element in load.context:
            keys = tuple(prop.key for prop in element.path.path[1::2])
            out[keys] = dict(element.strategy or ())
    return out


def test_mission_graph_blocks_missionflight_flight():
    """Every MissionResponse-feeding query must raiseload mf.flight —
    the serializer never emits it, and loading it drags gps_track for
    every attached flight into the worker."""
    strategies = _strategy_map(_mission_graph_options())
    assert strategies.get(("flights", "flight")) == {"lazy": "raise"}, (
        "MissionFlight.flight must be raiseload-ed on the mission graph — "
        "the mapper-level lazy='selectin' default would pull full Flight "
        "rows (gps_track/telemetry/raw_metadata) into list/detail (P1-2)."
    )


def test_mission_graph_still_loads_serialized_relationships():
    """MissionFlightResponse.aircraft + MissionResponse.images/customer
    ARE serialized — they must stay eagerly loaded."""
    strategies = _strategy_map(_mission_graph_options())
    assert strategies.get(("flights",)) == {"lazy": "selectin"}
    assert strategies.get(("flights", "aircraft")) == {"lazy": "selectin"}
    assert strategies.get(("images",)) == {"lazy": "selectin"}
    assert strategies.get(("customer",)) == {"lazy": "selectin"}


def test_financials_graph_blocks_flight_and_defers_cache():
    """financials_summary only reads mf.aircraft.model_name from the
    flights leg — it must load neither the Flight row nor the duplicated
    track in flight_data_cache, nor the mission image gallery."""
    strategies = _strategy_map(_billable_mission_load_options())
    assert strategies.get(("flights", "flight")) == {"lazy": "raise"}, (
        "P0-1: the billable-mission query must never materialize "
        "Flight.gps_track — financials only reads aircraft.model_name."
    )
    assert strategies.get(("flights", "flight_data_cache"), {}).get("deferred") is True, (
        "flight_data_cache duplicates the GPS track per attached flight and "
        "the summary never reads it — it must stay deferred."
    )
    assert strategies.get(("images",)) == {"lazy": "raise"}


def test_financials_graph_still_loads_aggregation_inputs():
    """The aggregation loop reads customer, mf.aircraft and
    invoice.line_items — those must stay eagerly loaded."""
    strategies = _strategy_map(_billable_mission_load_options())
    assert strategies.get(("customer",)) == {"lazy": "selectin"}
    assert strategies.get(("flights",)) == {"lazy": "selectin"}
    assert strategies.get(("flights", "aircraft")) == {"lazy": "selectin"}
    assert strategies.get(("invoice",)) == {"lazy": "selectin"}
    assert strategies.get(("invoice", "line_items")) == {"lazy": "selectin"}


def test_mapper_default_would_load_full_flight_rows():
    """Control: the mapper-level default on MissionFlight.flight is still
    selectin — proving the per-query raiseload above is load-bearing (it
    would silently cascade again if someone dropped the option)."""
    assert MissionFlight.flight.property.lazy == "selectin"


def test_missions_router_has_no_bare_mission_flights_selectinload():
    """Every selectinload(Mission.flights) in missions.py must go through
    _mission_graph_options() so the raiseload guard cannot be bypassed by
    a future query that re-adds the bare cascade."""
    import ast
    import inspect

    from app.routers import missions as missions_module

    tree = ast.parse(inspect.getsource(missions_module))
    helper = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_mission_graph_options"
    )

    offenders = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "selectinload"
        and node.args
        and isinstance(node.args[0], ast.Attribute)
        and node.args[0].attr == "flights"
        and isinstance(node.args[0].value, ast.Name)
        and node.args[0].value.id == "Mission"
        and not (helper.lineno <= node.lineno <= helper.end_lineno)
    ]
    assert not offenders, (
        f"selectinload(Mission.flights) outside _mission_graph_options() at "
        f"missions.py line(s) {offenders} — route queries must use the shared "
        "helper so the raiseload guard applies (audit P1-2)."
    )


# ── Behavioral: list route never touches mf.flight (full ASGI stack) ─


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _FakeSession:
    """Async session double — list_missions only calls execute()."""

    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    async def execute(self, _stmt):
        return _Result(self._rows)

    async def flush(self):  # pragma: no cover - unused on GET path
        pass

    async def commit(self):  # pragma: no cover - get_db handles commit
        pass

    async def rollback(self):  # pragma: no cover
        pass

    async def close(self):  # pragma: no cover
        pass


class _MissionFlightStub:
    """Quacks like a MissionFlight row, with ``.flight`` booby-trapped:
    if serialization (or anything else on the list path) touches the
    relationship, the test fails — mirroring what raiseload does in prod."""

    def __init__(self) -> None:
        self.id = uuid.uuid4()
        self.opendronelog_flight_id = None
        self.flight_id = uuid.uuid4()
        self.aircraft_id = None
        self.aircraft = None
        self.flight_data_cache = {"name": "Flight 1", "track": [{"lat": 44.0, "lng": -123.0}]}
        self.added_at = datetime.utcnow()

    @property
    def flight(self):
        raise AssertionError(
            "GET /api/missions touched MissionFlight.flight — with the "
            "production raiseload option this would 500 (and without it, "
            "re-introduce the gps_track OOM)."
        )


class _MissionStub:
    """Every column MissionResponse reads, defaulted to safe values."""

    def __init__(self, flights: list[Any]) -> None:
        from app.models.mission import MissionStatus, MissionType

        now = datetime.utcnow()
        self.id = uuid.uuid4()
        self.customer_id = None
        self.title = "Lean list mission"
        self.mission_type = MissionType.OTHER
        self.description = None
        self.mission_date = None
        self.location_name = None
        self.area_coordinates = None
        self.status = MissionStatus.DRAFT
        self.is_billable = False
        self.source = None
        self.source_ref = None
        self.unas_folder_path = None
        self.download_link_url = None
        self.download_link_expires_at = None
        self.client_notes = None
        self.created_at = now
        self.updated_at = now
        self.flights = flights
        self.images: list = []
        self.customer = None


def _build_app(rows: list[Any]) -> FastAPI:
    from app.auth.jwt import get_current_user
    from app.database import get_db
    from app.routers.missions import router as missions_router

    app = FastAPI()
    app.include_router(missions_router)

    fake_db = _FakeSession(rows)

    async def _get_db_override():
        yield fake_db

    async def _user_override():
        from types import SimpleNamespace

        return SimpleNamespace(username="op@test.example.com", id=uuid.uuid4())

    app.dependency_overrides[get_db] = _get_db_override
    app.dependency_overrides[get_current_user] = _user_override
    return app


def test_list_missions_serializes_without_touching_flight_relationship():
    """Proves the route contract is unchanged by the raiseload: the list
    response keeps flight_data_cache (the Hub's display source) and never
    needs MissionFlight.flight — accessing it trips the stub's trap."""
    mission = _MissionStub(flights=[_MissionFlightStub()])
    client = TestClient(_build_app([mission]))

    resp = client.get("/api/missions")

    assert resp.status_code == 200, f"got {resp.status_code} body={resp.text}"
    body = resp.json()
    assert len(body) == 1
    flights = body[0]["flights"]
    assert len(flights) == 1
    # Response shape preserved: cache + aircraft emitted, no `flight` key.
    assert flights[0]["flight_data_cache"]["track"] == [{"lat": 44.0, "lng": -123.0}]
    assert "aircraft" in flights[0]
    assert "flight" not in flights[0]
