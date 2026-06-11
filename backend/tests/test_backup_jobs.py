"""FU-8 #4 — backup create/restore as Celery jobs with progress polling.

ADDITIVE contract: ``POST /api/backup/jobs`` (kind=create|restore) → 202 +
``{job_id}``; ``GET /api/backup/jobs/{job_id}`` → ``{status, phase, progress,
result, error}``. Job state lives in Redis (progress overlay) with the Celery
``AsyncResult`` as terminal fallback.

Hermetic pattern per ``test_backup_nonblocking.py``: a minimal FastAPI app
mounting only the backup router, with the Celery ``.delay`` / ``AsyncResult``
and the Redis state helpers faked — no broker, no worker, no Redis, no pg
binaries. We also exercise the Celery task body directly against the same
sync pg-command fake the in-request tests use, proving the task reuses the
existing thread-safe helpers and writes the expected progress states.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


# ── Fakes ────────────────────────────────────────────────────────────

_FAKE_CONN = {
    "user": "doc", "password": "secret", "host": "db.test",
    "port": "5432", "dbname": "droneops",
}

_FAKE_TOC = (
    ";\n"
    "1; 0 0 TABLE public missions doc\n"
    "2; 0 0 TABLE public flights doc\n"
    "3; 0 0 TABLE public invoices doc\n"
)


class _FakeAsyncResult:
    def __init__(self, state: str, result: Any = None) -> None:
        self.state = state
        self.result = result


class _FakeCeleryApp:
    def __init__(self, results: dict[str, _FakeAsyncResult]) -> None:
        self._results = results

    def AsyncResult(self, job_id: str) -> _FakeAsyncResult:  # noqa: N802 - Celery API name
        return self._results.get(job_id, _FakeAsyncResult("PENDING"))


def _build_app(monkeypatch, *, captured: dict, redis_store: dict) -> FastAPI:
    """Mount the backup router with the Celery dispatch + Redis state faked."""
    import app.routers.backup as backup_mod
    import app.tasks.backup_jobs as backup_jobs
    import app.tasks.celery_tasks as celery_tasks
    from app.auth.jwt import get_current_user
    from app.routers.backup import router as backup_router

    monkeypatch.setattr(backup_mod, "_pg_conn_args", lambda: dict(_FAKE_CONN))

    # Fake the task .delay — record args, return an object with a stable .id
    def fake_delay(kind, temp_path):
        job_id = str(uuid.uuid4())
        captured["delay"] = {"kind": kind, "temp_path": temp_path, "id": job_id}
        return SimpleNamespace(id=job_id)

    monkeypatch.setattr(celery_tasks.run_backup_job_task, "delay", fake_delay)

    # Fake the Redis-backed job state with an in-process dict
    def fake_write(job_id, *, status, phase, progress, result=None, error=None):
        redis_store[job_id] = {
            "status": status, "phase": phase, "progress": progress,
            "result": result, "error": error,
        }

    def fake_read(job_id):
        return redis_store.get(job_id)

    monkeypatch.setattr(backup_jobs, "write_job_state", fake_write)
    monkeypatch.setattr(backup_jobs, "read_job_state", fake_read)

    app = FastAPI()
    app.include_router(backup_router)

    async def _user_override():
        return SimpleNamespace(username="op@test.example.com", id=uuid.uuid4())

    app.dependency_overrides[get_current_user] = _user_override
    return app


# ── POST /api/backup/jobs ────────────────────────────────────────────


def test_create_job_returns_202_and_seeds_queued_state(monkeypatch):
    captured: dict = {}
    redis_store: dict = {}
    app = _build_app(monkeypatch, captured=captured, redis_store=redis_store)
    client = TestClient(app)

    resp = client.post("/api/backup/jobs", json={"kind": "create"})

    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert set(body) == {"job_id", "kind", "status"}
    assert body["kind"] == "create"
    assert body["status"] == "queued"
    assert captured["delay"]["kind"] == "create"
    assert captured["delay"]["temp_path"] is None
    # The queued state is seeded so an immediate poll is meaningful
    assert redis_store[body["job_id"]]["status"] == "queued"


def test_create_job_rejects_bad_kind(monkeypatch):
    app = _build_app(monkeypatch, captured={}, redis_store={})
    client = TestClient(app)
    resp = client.post("/api/backup/jobs", json={"kind": "explode"})
    assert resp.status_code == 400
    assert "create" in resp.json()["detail"]


def test_restore_job_requires_valid_spool_temp_path(monkeypatch):
    app = _build_app(monkeypatch, captured={}, redis_store={})
    client = TestClient(app)

    # Missing temp_path → 400
    resp = client.post("/api/backup/jobs", json={"kind": "restore"})
    assert resp.status_code == 400

    # Path outside the restore-spool root → 400 (anti path-traversal)
    resp = client.post("/api/backup/jobs", json={"kind": "restore", "temp_path": "/etc/passwd"})
    assert resp.status_code == 400
    assert "Invalid temp_path" in resp.json()["detail"]


def test_restore_job_accepts_real_spool_file(monkeypatch):
    captured: dict = {}
    redis_store: dict = {}
    app = _build_app(monkeypatch, captured=captured, redis_store=redis_store)
    client = TestClient(app)

    # A real file under a doc_restore_* mkdtemp dir is accepted
    spool_dir = tempfile.mkdtemp(prefix="doc_restore_")
    spool_file = os.path.join(spool_dir, "uploaded.dump")
    with open(spool_file, "wb") as f:
        f.write(b"PGDMP")
    try:
        resp = client.post("/api/backup/jobs", json={"kind": "restore", "temp_path": spool_file})
        assert resp.status_code == 202, resp.text
        assert captured["delay"]["kind"] == "restore"
        # The router resolves the realpath before enqueue
        assert os.path.realpath(captured["delay"]["temp_path"]) == os.path.realpath(spool_file)
    finally:
        os.unlink(spool_file)
        os.rmdir(spool_dir)


# ── GET /api/backup/jobs/{job_id} ────────────────────────────────────


def test_poll_returns_redis_overlay_when_present(monkeypatch):
    redis_store: dict = {}
    app = _build_app(monkeypatch, captured={}, redis_store=redis_store)
    client = TestClient(app)

    jid = str(uuid.uuid4())
    redis_store[jid] = {
        "status": "running", "phase": "dumping", "progress": 20,
        "result": None, "error": None,
    }
    resp = client.get(f"/api/backup/jobs/{jid}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "running"
    assert body["phase"] == "dumping"
    assert body["progress"] == 20
    assert body["result"] is None


def test_poll_falls_back_to_celery_success(monkeypatch):
    # No Redis record → derive from the Celery result backend
    redis_store: dict = {}
    app = _build_app(monkeypatch, captured={}, redis_store=redis_store)

    import app.tasks.celery_tasks as celery_tasks
    jid = str(uuid.uuid4())
    payload = {"kind": "create", "filename": "doc_backup_x.dump"}
    monkeypatch.setattr(
        celery_tasks, "celery_app",
        _FakeCeleryApp({jid: _FakeAsyncResult("SUCCESS", payload)}),
    )

    client = TestClient(app)
    resp = client.get(f"/api/backup/jobs/{jid}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "complete"
    assert body["progress"] == 100
    assert body["result"] == payload


def test_poll_falls_back_to_celery_failure(monkeypatch):
    redis_store: dict = {}
    app = _build_app(monkeypatch, captured={}, redis_store=redis_store)

    import app.tasks.celery_tasks as celery_tasks
    jid = str(uuid.uuid4())
    monkeypatch.setattr(
        celery_tasks, "celery_app",
        _FakeCeleryApp({jid: _FakeAsyncResult("FAILURE", RuntimeError("boom"))}),
    )

    client = TestClient(app)
    resp = client.get(f"/api/backup/jobs/{jid}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "failed"
    assert "boom" in body["error"]


# ── Celery task body — reuses the sync helpers, writes progress ──────


def _patch_pg(monkeypatch, *, dump_payload=b"PGDMP archive"):
    """Patch the sync pg helpers used by the task to a deterministic fake."""
    import app.routers.backup as backup_mod

    def fake_run_pg(cmd, env, timeout=300):
        if cmd[0] == "pg_dump":
            out_path = cmd[cmd.index("-f") + 1]
            with open(out_path, "wb") as f:
                f.write(dump_payload)
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[0] == "pg_restore" and "--list" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout=_FAKE_TOC, stderr="")
        if cmd[0] == "pg_restore":
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[0] == "psql":
            return subprocess.CompletedProcess(cmd, 0, stdout="9\n", stderr="")
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(backup_mod, "_pg_conn_args", lambda: dict(_FAKE_CONN))
    monkeypatch.setattr(backup_mod, "_run_pg_command", fake_run_pg)


def test_task_create_runs_dump_validate_hash_and_completes(monkeypatch, tmp_path):
    import app.routers.backup as backup_mod
    import app.tasks.celery_tasks as celery_tasks_mod
    _patch_pg(monkeypatch, dump_payload=b"PGDMP create payload")
    backup_dir = tmp_path / "backups"
    monkeypatch.setattr(backup_mod, "BACKUP_DIR", str(backup_dir))
    # Neutralize the retention DB read — return empty (default 30d applies)
    monkeypatch.setattr(celery_tasks_mod, "_prune_backups", lambda backup_mod, *, keep: [])

    states: list[dict] = []
    import app.tasks.backup_jobs as backup_jobs
    orig = backup_jobs.write_job_state
    backup_jobs.write_job_state = lambda job_id, **kw: states.append(kw)
    try:
        result = celery_tasks_mod.run_backup_job_task.run("create", None)
    finally:
        backup_jobs.write_job_state = orig

    assert result["kind"] == "create"
    assert result["toc_entries"] == 3
    assert result["removed_old_backups"] == []
    # The dump landed in BACKUP_DIR with a sidecar
    dump = backup_dir / result["filename"]
    assert dump.read_bytes() == b"PGDMP create payload"
    assert (backup_dir / (result["filename"] + ".sha256")).exists()
    # Progress climbed monotonically and ended at 100/complete
    progresses = [s["progress"] for s in states]
    assert progresses == sorted(progresses)
    assert states[-1]["status"] == "complete"
    assert states[-1]["progress"] == 100


def test_task_restore_validates_and_completes(monkeypatch):
    _patch_pg(monkeypatch)
    import app.tasks.celery_tasks as celery_tasks

    spool_dir = tempfile.mkdtemp(prefix="doc_restore_")
    spool_file = os.path.join(spool_dir, "restore_me.dump")
    with open(spool_file, "wb") as f:
        f.write(b"PGDMP restore payload")

    states: list[dict] = []
    import app.tasks.backup_jobs as backup_jobs
    orig = backup_jobs.write_job_state
    backup_jobs.write_job_state = lambda job_id, **kw: states.append(kw)
    try:
        result = celery_tasks.run_backup_job_task.run("restore", spool_file)
    finally:
        backup_jobs.write_job_state = orig
        # task cleans up the spool on success; tolerate either state
        if os.path.isdir(spool_dir):
            for p in os.listdir(spool_dir):
                os.unlink(os.path.join(spool_dir, p))
            os.rmdir(spool_dir)

    assert result["kind"] == "restore"
    assert result["restored"] is True
    assert result["table_count"] == 9
    assert states[-1]["status"] == "complete"
    # The consumed spool file is removed by the task
    assert not os.path.exists(spool_file)


def test_task_create_failure_records_failed_state(monkeypatch, tmp_path):
    import app.routers.backup as backup_mod
    import app.tasks.celery_tasks as celery_tasks

    def failing_pg(cmd, env, timeout=300):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="pg_dump: connection refused")

    monkeypatch.setattr(backup_mod, "_pg_conn_args", lambda: dict(_FAKE_CONN))
    monkeypatch.setattr(backup_mod, "_run_pg_command", failing_pg)
    monkeypatch.setattr(backup_mod, "BACKUP_DIR", str(tmp_path / "backups"))

    states: list[dict] = []
    import app.tasks.backup_jobs as backup_jobs
    orig = backup_jobs.write_job_state
    backup_jobs.write_job_state = lambda job_id, **kw: states.append(kw)
    try:
        with pytest.raises(RuntimeError):
            celery_tasks.run_backup_job_task.run("create", None)
    finally:
        backup_jobs.write_job_state = orig

    assert states[-1]["status"] == "failed"
    assert "connection refused" in states[-1]["error"]
