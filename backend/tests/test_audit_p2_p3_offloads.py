"""Audit 2026-06-11 — P2-3 (maintenance DISTINCT ON), P3-5a (backup history
checksum sidecar), P3-5b (email PDF read offloaded).

Hermetic: no Postgres, no Redis, no SMTP, no pg binaries. Each test isolates
the unit under change.
"""

from __future__ import annotations

import asyncio
import hashlib
import os

import pytest


# ── P2-3: maintenance_status uses Postgres DISTINCT ON, not a Python dedup ──


def test_maintenance_last_records_query_uses_distinct_on():
    """The latest-per-(aircraft_id, maintenance_type) query must compile to a
    Postgres ``DISTINCT ON`` with the matching ORDER BY, so the de-dup runs in
    PG and only one row per group crosses the wire (replaces the load-all +
    Python-dedup path).
    """
    from sqlalchemy import select
    from sqlalchemy.dialects import postgresql

    from app.models.maintenance import MaintenanceRecord

    # Mirror the query built in maintenance_status.
    query = (
        select(MaintenanceRecord)
        .distinct(MaintenanceRecord.aircraft_id, MaintenanceRecord.maintenance_type)
        .order_by(
            MaintenanceRecord.aircraft_id,
            MaintenanceRecord.maintenance_type,
            MaintenanceRecord.performed_at.desc(),
        )
    )
    sql = str(query.compile(dialect=postgresql.dialect()))
    assert "DISTINCT ON" in sql
    # The DISTINCT ON columns must lead the ORDER BY, with performed_at DESC last.
    assert "ORDER BY" in sql
    order_clause = sql.split("ORDER BY", 1)[1]
    assert "aircraft_id" in order_clause
    assert "maintenance_type" in order_clause
    assert "performed_at" in order_clause
    assert "DESC" in order_clause


def test_maintenance_status_source_has_no_python_first_wins_dedup():
    """Regression guard: the old ``if key not in last_record_map`` Python
    de-dup must be gone — the dedup is now PG's job."""
    import inspect

    import app.routers.maintenance as maint

    src = inspect.getsource(maint.maintenance_status)
    assert "if key not in last_record_map" not in src
    assert ".distinct(" in src


# ── P3-5a: backup history reads a checksum sidecar, hashes only on a miss ──


def test_checksum_sidecar_roundtrip(tmp_path):
    import app.routers.backup as backup_mod

    dump = tmp_path / "doc_backup_x.dump"
    payload = b"PGDMP" + os.urandom(1024)
    dump.write_bytes(payload)
    expected = hashlib.sha256(payload).hexdigest()

    # Write then read the sidecar — value matches the file hash
    backup_mod._write_sidecar(str(dump), expected)
    assert (tmp_path / "doc_backup_x.dump.sha256").read_text() == expected
    assert backup_mod._read_sidecar(str(dump)) == expected


def test_read_sidecar_rejects_stale_and_corrupt(tmp_path):
    import app.routers.backup as backup_mod

    dump = tmp_path / "doc_backup_y.dump"
    dump.write_bytes(b"PGDMP")
    sidecar = tmp_path / "doc_backup_y.dump.sha256"

    # Corrupt (not 64-hex) → None
    sidecar.write_text("not-a-valid-digest")
    assert backup_mod._read_sidecar(str(dump)) is None

    # Stale (sidecar older than dump) → None. Make the sidecar look older.
    sidecar.write_text("a" * 64)
    old = dump.stat().st_mtime - 100
    os.utime(sidecar, (old, old))
    assert backup_mod._read_sidecar(str(dump)) is None

    # Missing → None
    sidecar.unlink()
    assert backup_mod._read_sidecar(str(dump)) is None


def test_checksum_with_sidecar_hashes_on_miss_and_backfills(tmp_path):
    import app.routers.backup as backup_mod

    dump = tmp_path / "doc_backup_z.dump"
    payload = b"PGDMP" + os.urandom(2048)
    dump.write_bytes(payload)
    expected = hashlib.sha256(payload).hexdigest()
    sidecar = tmp_path / "doc_backup_z.dump.sha256"
    assert not sidecar.exists()

    # First call: no sidecar → hash + backfill
    got = asyncio.run(backup_mod._checksum_with_sidecar(str(dump)))
    assert got == expected
    assert sidecar.exists()
    assert sidecar.read_text() == expected

    # Second call: must read the sidecar, not re-hash. Sabotage the hasher to
    # prove the cached path is taken.
    def _boom(_fp):
        raise AssertionError("re-hashed despite a valid sidecar")

    orig = backup_mod._compute_sha256
    backup_mod._compute_sha256 = _boom
    try:
        again = asyncio.run(backup_mod._checksum_with_sidecar(str(dump)))
    finally:
        backup_mod._compute_sha256 = orig
    assert again == expected


# ── P3-5b: email PDF attachment read is offloaded to an executor ──────


def test_email_pdf_attachment_read_offloaded(monkeypatch, tmp_path):
    """The PDF read must happen via ``run_in_executor`` (off the event loop),
    not a bare synchronous ``open().read()`` on the loop thread."""
    import app.services.email_service as email_mod

    pdf = tmp_path / "report.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")

    # Record whether the file read happened on the event loop thread.
    on_loop: list[bool] = []
    real_open = open

    def tracking_open(path, *a, **k):
        if str(path) == str(pdf):
            try:
                asyncio.get_running_loop()
                on_loop.append(True)  # bad — running on the loop thread
            except RuntimeError:
                on_loop.append(False)  # good — executor worker thread
        return real_open(path, *a, **k)

    # Build the minimal coroutine that mirrors the attach block: read the PDF
    # off-loop exactly as send_report_email now does.
    async def _attach_like_service():
        def _read_pdf():
            with tracking_open(str(pdf), "rb") as f:
                return f.read()
        return await asyncio.get_running_loop().run_in_executor(None, _read_pdf)

    data = asyncio.run(_attach_like_service())
    assert data == b"%PDF-1.4 fake"
    assert on_loop == [False], "PDF read must run in an executor thread, not on the event loop"

    # And the real source must route the attachment read through run_in_executor
    # (the read is allowed inside the offloaded ``_read_pdf`` helper, but it must
    # not be awaited/called directly on the event-loop thread).
    import inspect
    src = inspect.getsource(email_mod.send_report_email)
    attach_block = src.split("Attach PDF", 1)[1].split("smtp_port", 1)[0]
    assert "run_in_executor" in attach_block
