"""Audit 2026-06-11 finding #9 (P2) — flight-log upload consolidation +
streaming ingest.

``backend/app/routers/flight_library.py`` carried three near-duplicate
flight-log upload handlers — ``POST /api/flight-library/upload``,
``/device-upload`` and ``/reprocess`` — each doing ``content = await
upload.read()``: the whole uploaded log materialized as one ``bytes`` object.
That is the same OOM class that killed image uploads (missions.py v2.68.7)
and backup uploads (backup.py ``_save_upload_to_disk``). Flight-log upload is
the operator's PRIMARY field workflow, so the contract is frozen: DroneOpsSync
(the Windows companion) and the mobile flows POST these endpoints.

These tests exercise all three routes through the full FastAPI ASGI stack
(``TestClient`` + ``app.dependency_overrides``) — house pattern per
``test_missions_post_rejects_id_in_body.py`` — and assert:

* the response shape / status code of each route is unchanged;
* the per-route divergent behaviours are preserved (``/upload`` &
  ``/device-upload`` skip duplicates and return ``FlightUploadResponse``;
  ``/device-upload`` additionally accumulates ``total_bytes`` and emits its
  audit log; ``/reprocess`` UPDATES an existing flight in place, IMPORTS a new
  one, and returns the ``{updated, imported, skipped, errors}`` dict);
* the streaming guard — ``UploadFile.read`` is monkeypatched to raise, exactly
  like ``test_backup_nonblocking.py`` does, proving none of the three routes
  buffer the whole upload in RAM.

Hermetic: no Postgres, no Redis, no flight-parser service. The parser HTTP
call is faked at the ``httpx.AsyncClient`` boundary; fleet attribution and
battery tracking are stubbed; the original-file store is redirected to a tmp
dir.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.datastructures import UploadFile as StarletteUploadFile


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


# ── Fake httpx.AsyncClient standing in for the flight-parser ─────────


class _FakeParserResponse:
    def __init__(self, status_code: int, payload: dict) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict:
        return self._payload


class _FakeAsyncClient:
    """Drop-in for ``httpx.AsyncClient`` used inside ``_SpooledUpload.parse``.

    ``_response`` and ``_raise`` are injected per test via the module-level
    ``_PARSER`` holder. ``post`` consumes the ``files=`` file object so we can
    assert the upload streamed from the on-disk spool, not a whole-file dump.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        pass

    async def post(self, url: str, files: dict, headers: dict) -> _FakeParserResponse:
        if _PARSER["raise"] is not None:
            raise _PARSER["raise"]
        # The parser source must be a file-like object (the streamed spool),
        # never a bytes/str whole dump. Drain it to prove it is readable.
        _name, fileobj = files["file"]
        assert hasattr(fileobj, "read"), "parser file must be a streamed file object, not bytes"
        _PARSER["last_upload_bytes"] = fileobj.read()
        return _FakeParserResponse(_PARSER["status_code"], _PARSER["payload"])


_PARSER: dict[str, Any] = {
    "status_code": 200,
    "payload": {"flights": []},
    "raise": None,
    "last_upload_bytes": None,
}


def _set_parser(status_code: int = 200, payload: dict | None = None, raise_exc: Any = None) -> None:
    _PARSER["status_code"] = status_code
    _PARSER["payload"] = payload if payload is not None else {"flights": []}
    _PARSER["raise"] = raise_exc
    _PARSER["last_upload_bytes"] = None


# ── Fake AsyncSession ────────────────────────────────────────────────


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
    """Mimic what a real ``flush`` does: populate Python-side column defaults
    so the freshly-built ORM object serializes through ``FlightResponse``."""
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
    """Async-session double.

    ``dedup_values`` is a queue consumed by ``execute`` in the order the route
    issues its ``SELECT Flight WHERE source_file_hash`` lookups — each value is
    what ``scalar_one_or_none`` returns (``None`` = not a duplicate, or an
    existing ``Flight`` for the reprocess-update path)."""

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


# ── App builder mounting only the flight-library router ──────────────


def _build_app(monkeypatch, dedup_values: list[Any], *, with_device_key: bool = False, tmp_store=None) -> tuple[FastAPI, _FakeSession]:
    import app.routers.flight_library as fl
    from app.auth.jwt import get_current_user
    from app.auth.device import validate_device_api_key
    from app.database import get_db
    from app.models.device_api_key import DeviceApiKey

    # Redirect the original-file store to a writable tmp dir (default
    # /data/uploads is not writable in the test sandbox — the helper would
    # fail-soft, but we want the streamed copy to actually land so the
    # round-trip is exercised).
    if tmp_store is not None:
        monkeypatch.setattr(fl, "_FLIGHT_LOGS_DIR", tmp_store)

    # Parser HTTP boundary + the slow/DB collaborators in the shared builder.
    monkeypatch.setattr(fl.httpx, "AsyncClient", _FakeAsyncClient)
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


def _async_return(value: Any):
    async def _fn(*args: Any, **kwargs: Any):
        return value
    return _fn


def _forbid_whole_read(monkeypatch) -> None:
    """Make ``await upload.read()``-the-whole-upload a test failure.

    The streaming path reads from ``upload.file`` (the disk-backed spool) in a
    worker thread and must never call the async ``UploadFile.read``."""

    async def _boom(self, size: int = -1):  # pragma: no cover - failure path
        raise AssertionError(
            "UploadFile.read() called — flight-log upload must stream from "
            "upload.file, never buffer the whole log in RAM (audit #9)"
        )

    monkeypatch.setattr(StarletteUploadFile, "read", _boom)


_LOG = b"DJI flight record bytes " + b"x" * 9000  # > one spool chunk; content matters


# ── /upload ──────────────────────────────────────────────────────────


def test_upload_success_shape_and_streams(monkeypatch, tmp_path):
    store = tmp_path / "flight_logs"
    _set_parser(payload={"flights": [_parsed_flight()]})
    app, _ = _build_app(monkeypatch, dedup_values=[None, None], tmp_store=store)
    _forbid_whole_read(monkeypatch)
    client = TestClient(app)

    resp = client.post(
        "/api/flight-library/upload",
        files={"files": ("DJIFlightRecord.txt", _LOG, "text/plain")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == {"imported", "skipped", "errors", "flights"}
    assert body["imported"] == 1
    assert body["skipped"] == 0
    assert body["errors"] == []
    assert len(body["flights"]) == 1
    # Proof the parser received the streamed bytes off the spool, intact.
    assert _PARSER["last_upload_bytes"] == _LOG
    # The original log was streamed to the hash-named store, bytes intact.
    stored = list(store.iterdir())
    assert len(stored) == 1
    assert stored[0].read_bytes() == _LOG


def test_upload_skips_duplicate_before_parsing(monkeypatch):
    existing = SimpleNamespace(id=uuid.uuid4())
    _set_parser(payload={"flights": [_parsed_flight()]})
    app, _ = _build_app(monkeypatch, dedup_values=[existing])  # pre-parser dedup hits
    _forbid_whole_read(monkeypatch)
    client = TestClient(app)

    resp = client.post(
        "/api/flight-library/upload",
        files={"files": ("dup.txt", _LOG, "text/plain")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {"imported": 0, "skipped": 1, "errors": [], "flights": []}
    # Duplicate must short-circuit BEFORE the parser is ever called.
    assert _PARSER["last_upload_bytes"] is None


def test_upload_parser_non_200_records_error_with_code(monkeypatch):
    _set_parser(status_code=503, payload={})
    app, _ = _build_app(monkeypatch, dedup_values=[None])
    _forbid_whole_read(monkeypatch)
    client = TestClient(app)

    resp = client.post(
        "/api/flight-library/upload",
        files={"files": ("DJIFlightRecord.txt", _LOG, "text/plain")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["imported"] == 0
    assert body["errors"] == ["DJIFlightRecord.txt: parser returned 503"]


def test_upload_parser_unavailable_records_connect_error(monkeypatch):
    import httpx as _httpx
    _set_parser(raise_exc=_httpx.ConnectError("refused"))
    app, _ = _build_app(monkeypatch, dedup_values=[None])
    _forbid_whole_read(monkeypatch)
    client = TestClient(app)

    resp = client.post(
        "/api/flight-library/upload",
        files={"files": ("DJIFlightRecord.txt", _LOG, "text/plain")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["errors"] == ["DJIFlightRecord.txt: flight-parser service unavailable"]


# ── /device-upload ───────────────────────────────────────────────────


def test_device_upload_success_shape_and_audit_log(monkeypatch, caplog):
    import logging
    _set_parser(payload={"flights": [_parsed_flight()]})
    app, _ = _build_app(monkeypatch, dedup_values=[None, None], with_device_key=True)
    _forbid_whole_read(monkeypatch)
    client = TestClient(app)

    with caplog.at_level(logging.INFO, logger="doc.flights"):
        resp = client.post(
            "/api/flight-library/device-upload",
            files={"files": ("DJIFlightRecord.txt", _LOG, "text/plain")},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == {"imported", "skipped", "errors", "flights"}
    assert body["imported"] == 1
    # The device path emits its structured audit log with total_bytes.
    audit = [r for r in caplog.records if getattr(r, "event", None) == "device_upload"]
    assert audit, "device_upload audit log was not emitted"
    rec = audit[0]
    assert rec.total_bytes == len(_LOG)
    assert rec.file_count == 1
    assert rec.imported == 1
    assert rec.device_label == "field-rc-pro"


def test_device_upload_requires_device_key_header(monkeypatch):
    # No device-key dependency override → the Header(...) requirement is enforced.
    _set_parser(payload={"flights": []})
    app, _ = _build_app(monkeypatch, dedup_values=[], with_device_key=False)
    client = TestClient(app)
    resp = client.post(
        "/api/flight-library/device-upload",
        files={"files": ("x.txt", _LOG, "text/plain")},
    )
    assert resp.status_code == 422  # missing required X-Device-Api-Key header


# ── /reprocess ───────────────────────────────────────────────────────


def test_reprocess_updates_existing_flight_in_place(monkeypatch):
    from app.models.flight import Flight
    existing = Flight(
        id=uuid.uuid4(), name="Existing Flight", source_file_hash="abc",
        source="dji_txt", duration_secs=1.0, total_distance=1.0,
        max_altitude=1.0, max_speed=1.0, point_count=1,
        created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
    )
    _set_parser(payload={"flights": [_parsed_flight(point_count=2222)]})
    # reprocess has NO pre-parser dedup; per-parsed-flight lookup returns the
    # existing row → update branch.
    app, _ = _build_app(monkeypatch, dedup_values=[existing])
    _forbid_whole_read(monkeypatch)
    client = TestClient(app)

    resp = client.post(
        "/api/flight-library/reprocess",
        files={"files": ("DJIFlightRecord.txt", _LOG, "text/plain")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == {"updated", "imported", "skipped", "errors"}
    assert body == {"updated": 1, "imported": 0, "skipped": 0, "errors": []}
    # In-place mutation of the existing row.
    assert existing.point_count == 2222


def test_reprocess_imports_new_flight_when_no_match(monkeypatch):
    _set_parser(payload={"flights": [_parsed_flight()]})
    app, _ = _build_app(monkeypatch, dedup_values=[None])  # no existing row → import
    _forbid_whole_read(monkeypatch)
    client = TestClient(app)

    resp = client.post(
        "/api/flight-library/reprocess",
        files={"files": ("DJIFlightRecord.txt", _LOG, "text/plain")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {"updated": 0, "imported": 1, "skipped": 0, "errors": []}


def test_reprocess_parser_non_200_records_error(monkeypatch):
    _set_parser(status_code=500, payload={})
    app, _ = _build_app(monkeypatch, dedup_values=[])
    _forbid_whole_read(monkeypatch)
    client = TestClient(app)

    resp = client.post(
        "/api/flight-library/reprocess",
        files={"files": ("DJIFlightRecord.txt", _LOG, "text/plain")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {"updated": 0, "imported": 0, "skipped": 0,
                    "errors": ["DJIFlightRecord.txt: parser returned 500"]}
