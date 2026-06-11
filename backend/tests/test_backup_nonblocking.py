"""Audit 2026-06-11 P0-2 + P0-3 — backup router must not block the event loop.

P0-2: ``pg_dump`` / ``pg_restore`` / ``psql`` subprocess calls (up to 600 s)
previously ran synchronously inside ``async def`` handlers, freezing the
single uvicorn event loop — healthchecks failed mid-backup and the container
was restarted mid-restore. They now run via ``run_in_executor``.

P0-3: the upload endpoints previously did ``await file.read()`` of an entire
DB dump into one bytes object — an OOM-kill class under the 1 GiB cgroup cap.
They now stream the (disk-spooled) multipart temp file to disk in 1 MiB
chunks, hashing incrementally, in a worker thread.

Hermetic pattern per ``test_missions_post_rejects_id_in_body.py``: minimal
FastAPI app + fakes + ``TestClient``, no Postgres / Redis / real pg binaries.

The off-loop proof: each subprocess fake calls ``asyncio.get_running_loop()``
— inside an executor worker thread that raises ``RuntimeError`` (correct);
on the event-loop thread it succeeds (regression), which we record and fail.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import subprocess
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.datastructures import UploadFile as StarletteUploadFile


# ── Fakes ────────────────────────────────────────────────────────────


_FAKE_CONN = {
    "user": "doc",
    "password": "secret",
    "host": "db.test",
    "port": "5432",
    "dbname": "droneops",
}

# pg_restore --list output: 2 comment lines + 3 TOC entries
_FAKE_TOC = (
    ";\n"
    "; Archive created at 2026-06-11\n"
    "1; 0 0 TABLE public missions doc\n"
    "2; 0 0 TABLE public flights doc\n"
    "3; 0 0 TABLE public invoices doc\n"
)


class _LoopViolationRecorder:
    """Collects evidence that a blocking call ran ON the event loop."""

    def __init__(self) -> None:
        self.violations: list[str] = []

    def check(self, label: str) -> None:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return  # executor worker thread — correct
        self.violations.append(label)


def _make_fake_run_pg(recorder: _LoopViolationRecorder, *, dump_payload: bytes = b"PGDMP fake archive bytes"):
    """A ``_run_pg_command`` stand-in that mimics pg_dump/pg_restore/psql."""

    def fake_run_pg(cmd: list[str], env: dict, timeout: int = 300) -> subprocess.CompletedProcess:
        recorder.check(cmd[0])
        if cmd[0] == "pg_dump":
            # Honors -f <path>: write the archive like the real binary would
            out_path = cmd[cmd.index("-f") + 1]
            with open(out_path, "wb") as f:
                f.write(dump_payload)
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[0] == "pg_restore" and "--list" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout=_FAKE_TOC, stderr="")
        if cmd[0] == "pg_restore":
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[0] == "psql":
            return subprocess.CompletedProcess(cmd, 0, stdout="12\n", stderr="")
        raise AssertionError(f"unexpected command: {cmd}")

    return fake_run_pg


class _ScalarOneOrNone:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeSession:
    """Minimal async-session double — backup settings lookups return None
    so ``_get_setting`` falls back to its defaults (retention 30 d)."""

    async def execute(self, _stmt):
        return _ScalarOneOrNone(None)

    def add(self, obj):  # pragma: no cover - unused here
        pass

    async def commit(self):  # pragma: no cover - unused here
        pass


def _build_app(monkeypatch, recorder: _LoopViolationRecorder, **fake_kwargs) -> FastAPI:
    """Mount only the backup router with fakes for auth, DB, and pg binaries."""
    import app.routers.backup as backup_mod
    from app.auth.jwt import get_current_user
    from app.database import get_db
    from app.routers.backup import router as backup_router

    monkeypatch.setattr(backup_mod, "_pg_conn_args", lambda: dict(_FAKE_CONN))
    monkeypatch.setattr(backup_mod, "_run_pg_command", _make_fake_run_pg(recorder, **fake_kwargs))

    app = FastAPI()
    app.include_router(backup_router)

    async def _get_db_override():
        yield _FakeSession()

    async def _user_override():
        from types import SimpleNamespace
        import uuid
        return SimpleNamespace(username="op@test.example.com", id=uuid.uuid4())

    app.dependency_overrides[get_db] = _get_db_override
    app.dependency_overrides[get_current_user] = _user_override
    return app


def _forbid_whole_read(monkeypatch) -> None:
    """Make ``await file.read()``-the-whole-upload a test failure.

    The streaming path reads from ``file.file`` (the disk-backed spool)
    directly and must never call the async ``UploadFile.read``.
    """

    async def _boom(self, size: int = -1):  # pragma: no cover - failure path
        raise AssertionError(
            "UploadFile.read() called — upload must stream from file.file, "
            "never buffer the whole dump in RAM (P0-3)"
        )

    monkeypatch.setattr(StarletteUploadFile, "read", _boom)


# ── P0-3: upload endpoints stream to disk, never whole-dump-in-RAM ───


def test_validate_upload_streams_to_disk_and_hashes_incrementally(monkeypatch):
    recorder = _LoopViolationRecorder()
    app = _build_app(monkeypatch, recorder)
    _forbid_whole_read(monkeypatch)
    client = TestClient(app)

    payload = b"PGDMP" + os.urandom(4096)  # > one 8 KiB-chunk boundary is irrelevant; content matters
    resp = client.post(
        "/api/backup/validate-upload",
        files={"file": ("my_backup.dump", payload, "application/octet-stream")},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Response contract unchanged (frontend depends on these keys)
    assert set(body) == {"valid", "filename", "temp_path", "sha256", "size_bytes", "toc_entries"}
    assert body["valid"] is True
    assert body["filename"] == "my_backup.dump"
    assert body["sha256"] == hashlib.sha256(payload).hexdigest()
    assert body["size_bytes"] == len(payload)
    assert body["toc_entries"] == 3
    # The temp file is kept for the subsequent restore call — bytes intact
    with open(body["temp_path"], "rb") as f:
        assert f.read() == payload
    assert recorder.violations == [], f"blocking calls ran on the event loop: {recorder.violations}"
    # cleanup the temp spool the endpoint intentionally leaves behind
    os.unlink(body["temp_path"])
    os.rmdir(os.path.dirname(body["temp_path"]))


def test_validate_upload_rejects_corrupt_archive_with_400(monkeypatch):
    recorder = _LoopViolationRecorder()
    app = _build_app(monkeypatch, recorder)

    # pg_restore --list fails → archive invalid
    import app.routers.backup as backup_mod

    def fake_run_pg(cmd, env, timeout=300):
        recorder.check(cmd[0])
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="pg_restore: error: not an archive")

    monkeypatch.setattr(backup_mod, "_run_pg_command", fake_run_pg)
    client = TestClient(app)

    resp = client.post(
        "/api/backup/validate-upload",
        files={"file": ("garbage.dump", b"not a dump", "application/octet-stream")},
    )
    assert resp.status_code == 400, resp.text
    assert "Invalid backup file" in resp.json()["detail"]
    assert recorder.violations == []


def test_restore_from_upload_streams_and_offloads_pg_restore(monkeypatch):
    recorder = _LoopViolationRecorder()
    app = _build_app(monkeypatch, recorder)
    _forbid_whole_read(monkeypatch)
    client = TestClient(app)

    payload = b"PGDMP" + os.urandom(2 * 1024 * 1024 + 17)  # spans multiple 1 MiB copy chunks
    resp = client.post(
        "/api/backup/restore-from-upload",
        files={"file": ("restore_me.dump", payload, "application/octet-stream")},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Response contract unchanged
    assert set(body) == {
        "restored", "filename", "sha256", "size_bytes",
        "toc_entries", "table_count", "warnings",
    }
    assert body["restored"] is True
    assert body["filename"] == "restore_me.dump"
    assert body["sha256"] == hashlib.sha256(payload).hexdigest()
    assert body["size_bytes"] == len(payload)
    assert body["toc_entries"] == 3
    assert body["table_count"] == 12  # from the psql verify fake
    assert body["warnings"] is None
    # pg_restore --list, pg_restore, and psql all ran — none on the loop
    assert recorder.violations == [], f"blocking calls ran on the event loop: {recorder.violations}"


# ── P0-2: dump/validate/hash offloaded in the download + scheduled paths ──


def test_create_and_download_offloads_pg_dump_and_streams_archive(monkeypatch):
    recorder = _LoopViolationRecorder()
    dump_payload = b"PGDMP" + os.urandom(8192)
    app = _build_app(monkeypatch, recorder, dump_payload=dump_payload)
    client = TestClient(app)

    resp = client.post("/api/backup/create-and-download")

    assert resp.status_code == 200, resp.text
    assert resp.content == dump_payload
    assert resp.headers["X-Backup-SHA256"] == hashlib.sha256(dump_payload).hexdigest()
    assert resp.headers["X-Backup-Objects"] == "3"
    assert resp.headers["Content-Length"] == str(len(dump_payload))
    assert "attachment" in resp.headers["Content-Disposition"]
    assert recorder.violations == [], f"blocking calls ran on the event loop: {recorder.violations}"


def test_run_backup_now_offloads_dump_validate_and_hash(monkeypatch, tmp_path):
    recorder = _LoopViolationRecorder()
    dump_payload = b"PGDMP scheduled backup"
    app = _build_app(monkeypatch, recorder, dump_payload=dump_payload)

    import app.routers.backup as backup_mod
    backup_dir = tmp_path / "backups"
    monkeypatch.setattr(backup_mod, "BACKUP_DIR", str(backup_dir))
    client = TestClient(app)

    resp = client.post("/api/backup/run-now")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Response contract unchanged
    assert set(body) == {
        "filename", "filepath", "size_bytes", "sha256",
        "toc_entries", "retention_days", "removed_old_backups",
    }
    assert body["sha256"] == hashlib.sha256(dump_payload).hexdigest()
    assert body["size_bytes"] == len(dump_payload)
    assert body["retention_days"] == 30  # _FakeSession → default
    assert body["removed_old_backups"] == []
    assert (backup_dir / body["filename"]).read_bytes() == dump_payload
    assert recorder.violations == [], f"blocking calls ran on the event loop: {recorder.violations}"


def test_create_and_download_timeout_still_maps_to_504(monkeypatch):
    """TimeoutExpired raised inside the executor must surface exactly as
    before — HTTP 504, not an unhandled 500."""
    recorder = _LoopViolationRecorder()
    app = _build_app(monkeypatch, recorder)

    import app.routers.backup as backup_mod

    def fake_run_pg(cmd, env, timeout=300):
        recorder.check(cmd[0])
        raise subprocess.TimeoutExpired(cmd, timeout)

    monkeypatch.setattr(backup_mod, "_run_pg_command", fake_run_pg)
    client = TestClient(app)

    resp = client.post("/api/backup/create-and-download")
    assert resp.status_code == 504, resp.text
    assert resp.json()["detail"] == "Backup timed out"
    assert recorder.violations == []
