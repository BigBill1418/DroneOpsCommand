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

FU-8 #1 (2026-06-11) goes one leg further on the LIST path specifically.
``flight_data_cache`` ITSELF duplicates the full GPS track per attached
flight (the ``track`` key holds the same ~19k points), and the Mission Hub
list (``Missions.tsx``) renders ZERO flight/image data — only scalar mission
columns. So GET /api/missions now serializes a lean ``MissionListItemResponse``
(scalars only) and runs ``_mission_list_options()``, which ``noload``s the
flights/images/customer relationships — the list query is one SELECT over the
mission columns, strictly O(rows), never O(track points). Detail
(``GET /api/missions/{id}``) and every write re-query keep the full
``MissionResponse`` + ``_mission_graph_options()`` shape byte-identical.

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
from app.routers.missions import _mission_graph_options, _mission_list_options


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


def test_mission_list_options_noload_heavy_relationships():
    """FU-8 #1: the LIST query must NOT load flights/images — those
    relationships are lazy='selectin' at the mapper level, so a bare
    select(Mission) would eager-load flight_data_cache (the per-flight
    GPS-track copy) into every list row. _mission_list_options() noloads
    them so the list payload is O(rows), not O(track points)."""
    strategies = _strategy_map(_mission_list_options())
    assert strategies.get(("flights",)) == {"lazy": "noload"}, (
        "GET /api/missions must noload Mission.flights — the lazy='selectin' "
        "mapper default drags flight_data_cache (duplicated gps_track) into "
        "every list row (FU-8 #1)."
    )
    assert strategies.get(("images",)) == {"lazy": "noload"}, (
        "GET /api/missions must noload Mission.images — the lean list schema "
        "doesn't serialize the gallery."
    )
    assert strategies.get(("customer",)) == {"lazy": "noload"}, (
        "GET /api/missions must noload Mission.customer — the lean list schema "
        "doesn't serialize it, so the mapper-level selectin default is a "
        "wasted round-trip."
    )


def test_mission_list_options_never_touches_flight_or_cache():
    """The lean list path must never reach MissionFlight.flight OR the
    flight_data_cache column — noload(Mission.flights) short-circuits the
    whole leg before either can be materialized, so neither appears in the
    strategy map at all (no flights load = no nested options)."""
    strategies = _strategy_map(_mission_list_options())
    assert ("flights", "flight") not in strategies
    assert ("flights", "flight_data_cache") not in strategies
    assert ("flights", "aircraft") not in strategies


def test_mission_list_item_schema_drops_heavy_fields():
    """The list response model must carry the scalar columns the Hub list
    renders and DROP flights/images (the heavy legs). Detail keeps them."""
    from app.schemas.mission import MissionListItemResponse, MissionResponse

    list_fields = set(MissionListItemResponse.model_fields)
    detail_fields = set(MissionResponse.model_fields)

    # Heavy relationship payloads are gone from the list contract.
    assert "flights" not in list_fields, (
        "MissionListItemResponse must not carry flights — that's the O(track) "
        "payload FU-8 #1 removes from the list."
    )
    assert "images" not in list_fields
    # Detail still carries the full shape (unchanged contract).
    assert "flights" in detail_fields
    assert "images" in detail_fields
    # Every scalar the list UI (Missions.tsx) reads is preserved.
    for col in (
        "id", "title", "mission_type", "location_name",
        "mission_date", "status", "is_billable",
    ):
        assert col in list_fields, f"list schema dropped scalar the UI reads: {col}"
    # The lean list is a strict subset of detail (no new/renamed fields).
    assert list_fields < detail_fields, (
        "MissionListItemResponse must be a strict subset of MissionResponse — "
        "no field may drift between list and detail."
    )


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


def test_list_missions_returns_lean_rows_without_heavy_payload():
    """FU-8 #1: the list response is the lean shape — scalar mission
    columns only, NO flights/images. Even though the stub mission carries
    a flight whose .flight relationship is booby-trapped, the lean
    serializer never reaches it: MissionListItemResponse doesn't emit
    flights at all, so the cache (and its duplicated GPS track) never
    crosses the wire. This is the byte-level proof the O(track) payload
    is gone from the list."""
    mission = _MissionStub(flights=[_MissionFlightStub()])
    client = TestClient(_build_app([mission]))

    resp = client.get("/api/missions")

    assert resp.status_code == 200, f"got {resp.status_code} body={resp.text}"
    body = resp.json()
    assert len(body) == 1
    row = body[0]
    # Heavy legs are gone — no flights array, no images array, hence no
    # flight_data_cache / track anywhere in the list payload.
    assert "flights" not in row, (
        "GET /api/missions leaked flights[] — the lean list contract drops "
        "the O(track) flight_data_cache copy (FU-8 #1)."
    )
    assert "images" not in row
    # Scalar columns the Hub list renders are all present and correct.
    assert row["title"] == "Lean list mission"
    assert row["status"] == "draft"
    assert row["mission_type"] == "other"
    assert row["is_billable"] is False
    # No part of the response references the duplicated GPS track.
    assert "track" not in resp.text
