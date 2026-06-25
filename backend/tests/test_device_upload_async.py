"""Stage B (ADR-0023) — async device-upload route + Celery parse task + poll.

Hermetic pattern per ``test_backup_jobs.py`` / ``test_backup_nonblocking.py``:
mount ONLY the ``flight_library`` router on a bare ``FastAPI()``; override the
device-key auth dependency with a fake; fake the Celery ``.delay`` /
``AsyncResult`` and the Redis batch-state helpers with in-process fakes; fake
the parser HTTP call. No broker, no worker, no Redis, no parser sidecar, no DB
for the route tests. The Celery task body is exercised directly via ``.run(...)``
against the parser fake to prove it reuses ``_build_flight_from_parsed``,
dedups, and writes monotonic per-file progress ending ``complete``.
"""

from __future__ import annotations

import io
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient


# ── Fakes ────────────────────────────────────────────────────────────


class _FakeAsyncResult:
    def __init__(self, state: str, result: Any = None) -> None:
        self.state = state
        self.result = result


class _FakeCeleryApp:
    def __init__(self, results: dict[str, _FakeAsyncResult]) -> None:
        self._results = results

    def AsyncResult(self, batch_id: str) -> _FakeAsyncResult:  # noqa: N802 - Celery API name
        return self._results.get(batch_id, _FakeAsyncResult("PENDING"))


class _FakeSpooled:
    """Stand-in for ``_SpooledUpload`` — no disk, no parser, just the hash."""

    def __init__(self, file_hash: str, size_bytes: int = 10) -> None:
        self.tmp_path = f"/tmp/fake_spool_{file_hash}"
        self.file_hash = file_hash
        self.size_bytes = size_bytes
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _build_app(monkeypatch, *, captured: dict, redis_store: dict,
               existing_hashes: set | None = None,
               spool_hash: str = "hash-new") -> FastAPI:
    """Mount the flight_library router with Celery dispatch + Redis + spool faked."""
    import app.routers.flight_library as fl
    import app.tasks.celery_tasks as celery_tasks
    import app.tasks.device_upload_jobs as duj
    from app.auth.device import validate_device_api_key
    from app.routers.flight_library import router as fl_router

    existing = existing_hashes if existing_hashes is not None else set()

    # Fake the DJI key read so no DB session is needed in the route.
    async def fake_dji_key(db):  # noqa: ARG001
        return "test-dji-key"

    monkeypatch.setattr(fl, "_get_dji_api_key", fake_dji_key)

    # Fake _spool_upload — returns a fake spooled object with a deterministic hash.
    async def fake_spool(upload):
        return _FakeSpooled(spool_hash)

    monkeypatch.setattr(fl, "_spool_upload", fake_spool)

    # Fake the dedup DB query: db.execute(...) → result with scalar_one_or_none.
    class _FakeResult:
        def __init__(self, hit: bool) -> None:
            self._hit = hit

        def scalar_one_or_none(self):
            return object() if self._hit else None

    class _FakeDB:
        async def execute(self, stmt):  # noqa: ARG002
            # The route's only query is the source_file_hash dedup; decide by set.
            return _FakeResult(spool_hash in existing)

    # Override get_db so the route's `db` is our fake (the route also depends on
    # get_db for _get_dji_api_key, which we've faked above to ignore it).
    from app.database import get_db

    async def _db_override():
        yield _FakeDB()

    # Fake the task .delay — capture args, return SimpleNamespace(id=...).
    def fake_delay(batch_id, tmp_path, filename, file_hash, parser_headers):
        captured.setdefault("delays", []).append(
            {"batch_id": batch_id, "tmp_path": tmp_path, "filename": filename,
             "file_hash": file_hash, "parser_headers": parser_headers}
        )
        return SimpleNamespace(id=str(uuid.uuid4()))

    monkeypatch.setattr(celery_tasks.parse_device_flight_task, "delay", fake_delay)

    # Fake the Redis batch-state with an in-process dict.
    def fake_write(batch_id, *, status, phase, progress, per_file=None, error=None):
        redis_store[batch_id] = {
            "status": status, "phase": phase, "progress": progress,
            "per_file": per_file if per_file is not None else [], "error": error,
        }

    def fake_read(batch_id):
        return redis_store.get(batch_id)

    monkeypatch.setattr(duj, "write_batch_state", fake_write)
    monkeypatch.setattr(duj, "read_batch_state", fake_read)

    app = FastAPI()
    app.include_router(fl_router)

    async def _device_override():
        return SimpleNamespace(label="field-rc-1", id=uuid.uuid4())

    app.dependency_overrides[validate_device_api_key] = _device_override
    app.dependency_overrides[get_db] = _db_override
    return app


def _file_part(name: str = "DJIFlightRecord_x.txt"):
    return {"files": (name, io.BytesIO(b"flight bytes"), "text/plain")}


# ── POST /device-upload/async ────────────────────────────────────────


def test_async_upload_returns_202_and_seeds_queued(monkeypatch):
    captured: dict = {}
    redis_store: dict = {}
    app = _build_app(monkeypatch, captured=captured, redis_store=redis_store)
    client = TestClient(app)

    resp = client.post("/api/flight-library/device-upload/async", files=_file_part())

    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert "batch_id" in body
    assert body["files"] == [{"name": "DJIFlightRecord_x.txt", "state": "pending"}]
    # A job was enqueued for the (non-duplicate) file.
    assert len(captured["delays"]) == 1
    assert captured["delays"][0]["batch_id"] == body["batch_id"]
    assert captured["delays"][0]["parser_headers"] == {"X-DJI-Api-Key": "test-dji-key"}
    # The queued state was seeded so an immediate poll is meaningful.
    seeded = redis_store[body["batch_id"]]
    assert seeded["status"] == "queued"
    assert seeded["per_file"] == [{"name": "DJIFlightRecord_x.txt", "state": "pending"}]


def test_async_upload_duplicate_short_circuits_to_skipped_no_delay(monkeypatch):
    captured: dict = {}
    redis_store: dict = {}
    app = _build_app(
        monkeypatch, captured=captured, redis_store=redis_store,
        existing_hashes={"hash-new"}, spool_hash="hash-new",
    )
    client = TestClient(app)

    resp = client.post("/api/flight-library/device-upload/async", files=_file_part())

    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["files"] == [{"name": "DJIFlightRecord_x.txt", "state": "skipped"}]
    # No job enqueued for the already-present file (pre-parse dedup short-circuit).
    assert captured.get("delays", []) == []
    assert redis_store[body["batch_id"]]["status"] == "queued"


# ── GET /device-upload/status/{batch_id} ─────────────────────────────


def test_status_returns_redis_overlay_when_present(monkeypatch):
    redis_store: dict = {}
    app = _build_app(monkeypatch, captured={}, redis_store=redis_store)
    client = TestClient(app)

    bid = str(uuid.uuid4())
    redis_store[bid] = {
        "status": "running", "phase": "parsing", "progress": 10,
        "per_file": [{"name": "a.txt", "state": "parsing",
                      "imported": 0, "skipped": 0, "error": None}],
        "error": None,
    }
    resp = client.get(f"/api/flight-library/device-upload/status/{bid}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["batch_id"] == bid
    assert body["status"] == "running"
    assert body["phase"] == "parsing"
    assert body["progress"] == 10
    assert body["per_file"][0]["name"] == "a.txt"


def test_status_falls_back_to_celery_success(monkeypatch):
    redis_store: dict = {}
    app = _build_app(monkeypatch, captured={}, redis_store=redis_store)

    import app.tasks.celery_tasks as celery_tasks
    bid = str(uuid.uuid4())
    payload = {"batch_id": bid, "per_file": [{"name": "a.txt", "state": "imported",
                                              "imported": 1, "skipped": 0, "error": None}]}
    monkeypatch.setattr(
        celery_tasks, "celery_app",
        _FakeCeleryApp({bid: _FakeAsyncResult("SUCCESS", payload)}),
    )

    client = TestClient(app)
    resp = client.get(f"/api/flight-library/device-upload/status/{bid}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "complete"
    assert body["progress"] == 100
    assert body["per_file"][0]["state"] == "imported"


def test_status_falls_back_to_celery_failure(monkeypatch):
    redis_store: dict = {}
    app = _build_app(monkeypatch, captured={}, redis_store=redis_store)

    import app.tasks.celery_tasks as celery_tasks
    bid = str(uuid.uuid4())
    monkeypatch.setattr(
        celery_tasks, "celery_app",
        _FakeCeleryApp({bid: _FakeAsyncResult("FAILURE", RuntimeError("boom"))}),
    )

    client = TestClient(app)
    resp = client.get(f"/api/flight-library/device-upload/status/{bid}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "failed"
    assert body["progress"] == 100


def test_status_pending_reports_queued(monkeypatch):
    redis_store: dict = {}
    app = _build_app(monkeypatch, captured={}, redis_store=redis_store)
    import app.tasks.celery_tasks as celery_tasks
    monkeypatch.setattr(celery_tasks, "celery_app", _FakeCeleryApp({}))
    client = TestClient(app)
    resp = client.get(f"/api/flight-library/device-upload/status/{uuid.uuid4()}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "queued"


# ── Celery task body — reuses _build_flight_from_parsed, dedups, progress ──


class _FakeHttpResponse:
    def __init__(self, status_code: int, payload: dict | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


class _FakeHttpClient:
    """Async-context-manager stand-in for httpx.AsyncClient with a canned POST."""

    def __init__(self, response: _FakeHttpResponse | Exception) -> None:
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, files=None, headers=None):  # noqa: ARG002
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


def _run_task(monkeypatch, *, http_response, build_calls, dup_hashes,
              tmp_exists=True, use_shared_store=False) -> tuple[list[dict], dict]:
    """Drive parse_device_flight_task.run(...) with the parser + DB + spool faked.

    Returns (states, result) where states is the list of write_batch_state kwargs.

    ``tmp_exists`` / ``use_shared_store`` reproduce the production *file
    topology*, which the default same-process harness hides. In prod the
    backend container spools the upload to its private ``/tmp`` AND persists the
    original to the hash store at ``/data/uploads/flight_logs`` (on the
    ``app_data`` volume both containers mount). The Celery worker runs in a
    *different* container, so the ``/tmp`` spool is NOT visible to it — only the
    shared store is. ``tmp_exists=False`` makes the handed ``tmp_path`` point at
    a file that does not exist on this container (the cross-container symptom);
    ``use_shared_store=True`` materializes the file in the shared hash store
    instead.
    """
    import app.routers.flight_library as fl
    import app.tasks.celery_tasks as celery_tasks
    import app.tasks.device_upload_jobs as duj
    import httpx

    # Parser HTTP call.
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: _FakeHttpClient(http_response))

    # Fake the builder — record calls, return a flight-ish object.
    async def fake_build(db, parsed, file_hash, filename, **kw):  # noqa: ARG001
        build_calls.append({"file_hash": parsed.get("file_hash", file_hash)})
        return SimpleNamespace(id=uuid.uuid4())

    monkeypatch.setattr(fl, "_build_flight_from_parsed", fake_build)

    # Fake the per-flight dedup query + the task-local session.
    class _FakeResult:
        def __init__(self, hit: bool) -> None:
            self._hit = hit

        def scalar_one_or_none(self):
            return object() if self._hit else None

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def execute(self, stmt):  # noqa: ARG002
            # Inspect the bound hash from the statement is overkill for a fake;
            # decide by a counter against dup_hashes via the build_calls/parsed.
            # We approximate: a hash is a dup if it's in dup_hashes.
            return _FakeResult(self._next_is_dup)

        _next_is_dup = False

        async def commit(self):
            pass

    session = _FakeSession()

    # task_event_loop yields (loop, make_session). We give a real loop + a
    # make_session that returns our fake session, and set the dup flag per call.
    import asyncio
    from contextlib import contextmanager

    @contextmanager
    def fake_loop():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        def make_session():
            return session

        try:
            yield loop, make_session
        finally:
            loop.close()
            asyncio.set_event_loop(None)

    # The task imports task_event_loop locally from async_db, so patch it there.
    import app.tasks.async_db as async_db
    monkeypatch.setattr(async_db, "task_event_loop", fake_loop)

    # Capture state writes.
    states: list[dict] = []

    def fake_write(batch_id, *, status, phase, progress, per_file=None, error=None):  # noqa: ARG001
        states.append({"status": status, "phase": phase, "progress": progress,
                       "per_file": per_file, "error": error})

    monkeypatch.setattr(duj, "write_batch_state", fake_write)

    # Drive dedup decisions: set _next_is_dup before each run via the session.
    session._next_is_dup = dup_hashes

    # Materialize the file topology the worker will resolve against.
    import os
    import tempfile
    cleanup_paths: list[str] = []

    if use_shared_store:
        # The shared hash store (/data/uploads/flight_logs) — visible to the
        # worker container because app_data is mounted in both.
        store_dir = tempfile.mkdtemp(prefix="test_share_")
        monkeypatch.setattr(fl, "_FLIGHT_LOGS_DIR", Path(store_dir))
        stored = Path(store_dir) / "hash-x.txt"
        stored.write_bytes(b"flight bytes")
        cleanup_paths.append(str(stored))

    if tmp_exists:
        # The backend container's /tmp spool — present only in the same-process
        # / same-container case.
        fd, spool_path = tempfile.mkstemp(prefix="test_spool_")
        with os.fdopen(fd, "wb") as f:
            f.write(b"flight bytes")
        cleanup_paths.append(spool_path)
    else:
        # Cross-container symptom: the handed /tmp path does NOT exist here.
        spool_path = os.path.join(
            tempfile.gettempdir(), f"nonexistent_spool_{uuid.uuid4().hex}"
        )

    try:
        result = celery_tasks.parse_device_flight_task.run(
            "batch-T", spool_path, "DJIFlightRecord_x.txt", "hash-x",
            {"X-DJI-Api-Key": "k"},
        )
    finally:
        for _p in cleanup_paths:
            if os.path.exists(_p):
                os.unlink(_p)
    return states, result


def test_task_imports_new_flight_and_completes(monkeypatch):
    build_calls: list[dict] = []
    resp = _FakeHttpResponse(200, {"flights": [{"file_hash": "hash-x"}], "errors": []})
    states, result = _run_task(
        monkeypatch, http_response=resp, build_calls=build_calls,
        dup_hashes=False,
    )

    # Reused the shared builder for the new flight.
    assert build_calls == [{"file_hash": "hash-x"}]
    # Monotonic progress ending complete/100 with the file imported.
    progresses = [s["progress"] for s in states]
    assert progresses == sorted(progresses)
    assert states[-1]["status"] == "complete"
    assert states[-1]["progress"] == 100
    entry = states[-1]["per_file"][0]
    assert entry["state"] == "imported"
    assert entry["imported"] == 1
    assert result["per_file"][0]["state"] == "imported"


def test_task_dedups_existing_flight_to_skipped(monkeypatch):
    build_calls: list[dict] = []
    resp = _FakeHttpResponse(200, {"flights": [{"file_hash": "hash-x"}], "errors": []})
    states, result = _run_task(
        monkeypatch, http_response=resp, build_calls=build_calls,
        dup_hashes=True,
    )
    # The per-flight dedup fired — builder never called.
    assert build_calls == []
    entry = states[-1]["per_file"][0]
    assert entry["state"] == "skipped"
    assert entry["skipped"] == 1
    assert states[-1]["status"] == "complete"


def test_task_parser_non_200_records_error_but_completes(monkeypatch):
    build_calls: list[dict] = []
    resp = _FakeHttpResponse(503, None)
    states, result = _run_task(
        monkeypatch, http_response=resp, build_calls=build_calls,
        dup_hashes=False,
    )
    entry = states[-1]["per_file"][0]
    assert entry["state"] == "error"
    assert "parser returned 503" in entry["error"]
    # Batch still completes (per-file error inside a complete batch, ADR §2.4).
    assert states[-1]["status"] == "complete"


def test_task_parser_connecterror_records_service_unavailable(monkeypatch):
    import httpx
    build_calls: list[dict] = []
    states, result = _run_task(
        monkeypatch, http_response=httpx.ConnectError("down"),
        build_calls=build_calls, dup_hashes=False,
    )
    entry = states[-1]["per_file"][0]
    assert entry["state"] == "error"
    assert "flight-parser service unavailable" in entry["error"]
    assert states[-1]["status"] == "complete"


def test_task_reads_shared_store_when_tmp_spool_absent(monkeypatch):
    """Regression (ADR-0023 cross-container temp-handoff): the worker runs in a
    different container than the API, so the API's /tmp spool is invisible. The
    worker MUST resolve the upload from the shared hash store instead. With the
    /tmp path absent but the file present in the store, the parse must succeed —
    exactly the M4P field failure (2026-06-24, `/tmp/flight_upload_*` ENOENT)."""
    build_calls: list[dict] = []
    resp = _FakeHttpResponse(200, {"flights": [{"file_hash": "hash-x"}], "errors": []})
    states, result = _run_task(
        monkeypatch, http_response=resp, build_calls=build_calls,
        dup_hashes=False, tmp_exists=False, use_shared_store=True,
    )
    # The worker read the shared store, parsed, and imported — no ENOENT error.
    assert build_calls == [{"file_hash": "hash-x"}]
    entry = states[-1]["per_file"][0]
    assert entry["state"] == "imported", f"unexpected error: {entry.get('error')!r}"
    assert entry["imported"] == 1
    assert states[-1]["status"] == "complete"
    assert states[-1]["progress"] == 100


def test_task_errors_clearly_when_artifact_missing_everywhere(monkeypatch):
    """If neither the /tmp spool nor the shared store has the file, the worker
    must record a clear, diagnosable per-file error and still complete the batch
    (ADR-0023 §2.4 failed-vs-complete-with-error split) — never a bare ENOENT."""
    build_calls: list[dict] = []
    resp = _FakeHttpResponse(200, {"flights": [{"file_hash": "hash-x"}], "errors": []})
    states, result = _run_task(
        monkeypatch, http_response=resp, build_calls=build_calls,
        dup_hashes=False, tmp_exists=False, use_shared_store=False,
    )
    entry = states[-1]["per_file"][0]
    assert entry["state"] == "error"
    assert "not found" in entry["error"].lower()
    assert build_calls == []  # never reached the parser
    assert states[-1]["status"] == "complete"
