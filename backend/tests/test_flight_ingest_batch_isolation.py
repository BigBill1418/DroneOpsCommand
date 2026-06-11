"""Audit 2026-06-11 P2-2 (conservative leg) — per-file isolation in the
multi-file flight-log upload batch loops.

The full P2-2 fix (decouple parse into a Celery task + status poll) needs a
DroneOpsSync client release and is explicitly OUT of scope. The conservative
leg, locked in here, is the in-request contract that the operator's PRIMARY
field workflow depends on:

    A parser timeout or parse error on file N must record an error for file N
    and CONTINUE to file N+1 — a single bad log must never abort the rest of
    the batch.

``/upload``, ``/device-upload`` and ``/reprocess`` each wrap their per-file
work in a ``try/except`` *inside* the loop, so:

* ``httpx.ReadTimeout`` (parser slow on one file) — NOT a subclass of
  ``ConnectError`` — is caught by the per-file ``except Exception`` → error
  recorded, loop continues. (Verified: ``ReadTimeout`` MRO is
  ``TimeoutException → TransportError → RequestError → HTTPError → Exception``;
  ``ConnectError`` is a sibling under ``NetworkError``.)
* a generic parse/value error on one file is likewise caught and the batch
  continues.
* ``httpx.ConnectError`` (parser process DOWN) is the deliberate per-file
  error string ``"<name>: flight-parser service unavailable"`` — and in these
  three upload routes it is ALSO caught per-file and the batch continues (only
  ``/reprocess/all``, the stored-file batch, deliberately ``break``s on
  ConnectError as a fast-fail; that route is not exercised here and its
  contract is preserved).

These tests post a 2-file batch where the FIRST file fails at the parser
boundary and assert the SECOND file still imports — proving continuation, not
abort. Same hermetic harness as ``test_flight_ingest_consolidated.py``
(TestClient + dependency_overrides; no Postgres / Redis / parser service).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


# ── A parsed-flight payload as the flight-parser would emit it ───────


def _parsed_flight(**overrides: Any) -> dict:
    base = {
        "duration_secs": 312.0,
        "total_distance": 1840.0,
        "max_altitude": 118.0,
        "max_speed": 14.2,
        "home_lat": 44.05,
        "home_lon": -123.09,
        "point_count": 1903,
        "gps_track": [{"lat": 44.05, "lng": -123.09, "alt": 0}],
        "telemetry": {"altitude": [0, 10]},
        "raw_metadata": {"app": "DJI Fly"},
        "source": "dji_txt",
        "drone_model": "mavic3thermal",
        "drone_serial": "SN-TEST-001",
        "battery_serial": None,
        "start_time": "2026-06-10T18:30:00Z",
        "original_filename": "DJIFlightRecord.txt",
    }
    base.update(overrides)
    return base


# ── Sequenced fake parser: one behavior per POST, in call order ──────


class _FakeParserResponse:
    def __init__(self, status_code: int, payload: dict) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict:
        return self._payload


class _SequencedAsyncClient:
    """Drop-in for ``httpx.AsyncClient``; pops one behavior per ``post`` from
    the module-level ``_SEQ`` queue so a multi-file batch can fail file N and
    succeed file N+1.

    Each queue item is either an ``Exception`` instance (raised) or a
    ``(status_code, payload)`` tuple (returned)."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def __aenter__(self) -> "_SequencedAsyncClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        pass

    async def post(self, url: str, files: dict, headers: dict) -> _FakeParserResponse:
        # Drain the streamed spool so the read path is exercised, then act.
        _name, fileobj = files["file"]
        fileobj.read()
        behavior = _SEQ.pop(0) if _SEQ else (200, {"flights": []})
        if isinstance(behavior, Exception):
            raise behavior
        status_code, payload = behavior
        return _FakeParserResponse(status_code, payload)


_SEQ: list[Any] = []


def _set_seq(*behaviors: Any) -> None:
    _SEQ.clear()
    _SEQ.extend(behaviors)


# ── Fake AsyncSession (dedup queue + Python-side default backfill) ───


class _ScalarResult:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _NestedTxn:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _apply_defaults(flight) -> None:
    now = datetime.utcnow()
    if flight.id is None:
        flight.id = uuid.uuid4()
    for attr, default in (
        ("duration_secs", 0.0), ("total_distance", 0.0), ("max_altitude", 0.0),
        ("max_speed", 0.0), ("point_count", 0), ("source", "manual"),
    ):
        if getattr(flight, attr, None) is None:
            setattr(flight, attr, default)
    if getattr(flight, "created_at", None) is None:
        flight.created_at = now
    if getattr(flight, "updated_at", None) is None:
        flight.updated_at = now
    if getattr(flight, "drone_name", None) is None:
        flight.drone_name = None


class _FakeSession:
    def __init__(self, dedup_values: list[Any]) -> None:
        self._dedup = list(dedup_values)
        self.added: list[Any] = []

    async def execute(self, _stmt):
        value = self._dedup.pop(0) if self._dedup else None
        return _ScalarResult(value)

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        for obj in self.added:
            _apply_defaults(obj)

    async def refresh(self, obj):
        _apply_defaults(obj)

    def begin_nested(self):
        return _NestedTxn()


# ── App builder (only the flight-library router) ─────────────────────


def _async_return(value: Any):
    async def _fn(*args: Any, **kwargs: Any):
        return value
    return _fn


def _build_app(monkeypatch, dedup_values: list[Any], *, with_device_key: bool = False, tmp_store=None):
    import app.routers.flight_library as fl
    from app.auth.jwt import get_current_user
    from app.auth.device import validate_device_api_key
    from app.database import get_db
    from app.models.device_api_key import DeviceApiKey

    if tmp_store is not None:
        monkeypatch.setattr(fl, "_FLIGHT_LOGS_DIR", tmp_store)

    monkeypatch.setattr(fl.httpx, "AsyncClient", _SequencedAsyncClient)
    monkeypatch.setattr(fl, "_get_dji_api_key", _async_return(None))
    monkeypatch.setattr(fl, "_match_fleet_aircraft", _async_return(None))
    monkeypatch.setattr(fl, "_generate_flight_name", _async_return("Flight 2026-06-10"))

    async def _no_battery(db, flight, battery_data):  # pragma: no cover - not hit without serial
        return None

    monkeypatch.setattr(fl, "_track_battery", _no_battery)

    session = _FakeSession(dedup_values)
    app = FastAPI()
    app.include_router(fl.router)

    async def _get_db_override():
        yield session

    async def _user_override():
        return SimpleNamespace(username="op@test.example.com", id=uuid.uuid4())

    app.dependency_overrides[get_db] = _get_db_override
    app.dependency_overrides[get_current_user] = _user_override
    if with_device_key:
        async def _device_override():
            return DeviceApiKey(id=uuid.uuid4(), label="field-rc-pro")
        app.dependency_overrides[validate_device_api_key] = _device_override
    return app, session


_LOG_A = b"DJI flight record A " + b"a" * 9000
_LOG_B = b"DJI flight record B " + b"b" * 9000

# httpx requires multiple files under the SAME field name; TestClient/requests
# encodes a list of (field, (filename, content, type)) tuples as repeated parts.
def _two_files():
    return [
        ("files", ("flightA.txt", _LOG_A, "text/plain")),
        ("files", ("flightB.txt", _LOG_B, "text/plain")),
    ]


# ── /upload — per-file isolation ─────────────────────────────────────


def test_upload_read_timeout_on_file_one_continues_to_file_two(monkeypatch):
    # File A: parser ReadTimeout (slow parse). File B: clean 200 with a flight.
    _set_seq(httpx.ReadTimeout("parse timed out"), (200, {"flights": [_parsed_flight()]}))
    # dedup lookups: A pre-parser (None) [then A raises before its per-flight
    # lookup], B pre-parser (None), B per-flight (None) → import.
    app, _ = _build_app(monkeypatch, dedup_values=[None, None, None])
    client = TestClient(app)

    resp = client.post("/api/flight-library/upload", files=_two_files())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # File A errored, File B still imported — batch did NOT abort.
    assert body["imported"] == 1
    assert len(body["flights"]) == 1
    assert any("flightA.txt" in e for e in body["errors"]), body["errors"]
    assert _SEQ == [], "both files must have been attempted (queue drained)"


def test_upload_parser_500_on_file_one_continues_to_file_two(monkeypatch):
    _set_seq((500, {}), (200, {"flights": [_parsed_flight()]}))
    app, _ = _build_app(monkeypatch, dedup_values=[None, None, None])
    client = TestClient(app)

    resp = client.post("/api/flight-library/upload", files=_two_files())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["imported"] == 1
    assert body["errors"] == ["flightA.txt: parser returned 500"]


def test_upload_connect_error_on_file_one_still_continues_to_file_two(monkeypatch):
    # In the THREE upload routes (unlike /reprocess/all), ConnectError is caught
    # per-file and the batch continues — lock that contract in.
    _set_seq(httpx.ConnectError("refused"), (200, {"flights": [_parsed_flight()]}))
    app, _ = _build_app(monkeypatch, dedup_values=[None, None, None])
    client = TestClient(app)

    resp = client.post("/api/flight-library/upload", files=_two_files())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["imported"] == 1
    assert "flightA.txt: flight-parser service unavailable" in body["errors"]


# ── /device-upload — per-file isolation ──────────────────────────────


def test_device_upload_read_timeout_on_file_one_continues(monkeypatch):
    _set_seq(httpx.ReadTimeout("parse timed out"), (200, {"flights": [_parsed_flight()]}))
    app, _ = _build_app(monkeypatch, dedup_values=[None, None, None], with_device_key=True)
    client = TestClient(app)

    resp = client.post("/api/flight-library/device-upload", files=_two_files())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["imported"] == 1
    assert any("flightA.txt" in e for e in body["errors"]), body["errors"]


# ── /reprocess — per-file isolation ──────────────────────────────────


def test_reprocess_read_timeout_on_file_one_continues_to_import_file_two(monkeypatch):
    # reprocess has NO pre-parser dedup. File A raises at parse; File B parses
    # and its per-flight lookup returns None → import.
    _set_seq(httpx.ReadTimeout("parse timed out"), (200, {"flights": [_parsed_flight()]}))
    app, _ = _build_app(monkeypatch, dedup_values=[None])  # only B's per-flight lookup
    client = TestClient(app)

    resp = client.post("/api/flight-library/reprocess", files=_two_files())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == {"updated", "imported", "skipped", "errors"}
    assert body["imported"] == 1
    assert body["updated"] == 0
    assert any("flightA.txt" in e for e in body["errors"]), body["errors"]
