"""Stage A (ADR-0023) — Redis batch-state helpers for device-upload parse jobs.

Pure helper tests: no broker, no Redis, no parser. The Redis client is faked
with an in-process dict (the fake-store idiom from ``test_backup_jobs.py``),
so we exercise the real ``write_batch_state`` / ``read_batch_state`` serialize
+ deserialize round-trip, the 0..100 progress clamp, and the ``None``-on-
absent/corrupt fallback contract that the two-tier poll depends on.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import app.tasks.device_upload_jobs as duj


# ── Fake Redis ───────────────────────────────────────────────────────


class _FakeRedis:
    """Minimal in-process stand-in for the redis client: setex + get over a dict."""

    def __init__(self, store: dict) -> None:
        self._store = store

    def setex(self, key, ttl, value):  # noqa: ARG002 - ttl unused in the fake
        self._store[key] = value

    def get(self, key):
        return self._store.get(key)


def _patch_redis(monkeypatch, store: dict) -> None:
    monkeypatch.setattr(duj, "_redis_client", lambda: _FakeRedis(store))


# ── Round-trip ───────────────────────────────────────────────────────


def test_write_then_read_round_trips_per_file(monkeypatch):
    store: dict = {}
    _patch_redis(monkeypatch, store)

    per_file = [
        {"name": "DJIFlightRecord_a.txt", "state": "imported",
         "imported": 1, "skipped": 0, "error": None},
        {"name": "DJIFlightRecord_b.txt", "state": "skipped",
         "imported": 0, "skipped": 1, "error": None},
    ]
    duj.write_batch_state(
        "batch-1", status=duj.STATUS_COMPLETE, phase="done",
        progress=100, per_file=per_file,
    )

    state = duj.read_batch_state("batch-1")
    assert state is not None
    assert state["status"] == duj.STATUS_COMPLETE
    assert state["phase"] == "done"
    assert state["progress"] == 100
    assert state["per_file"] == per_file
    assert state["error"] is None
    assert "updated_at" in state


def test_write_uses_the_batch_key_prefix(monkeypatch):
    store: dict = {}
    _patch_redis(monkeypatch, store)
    duj.write_batch_state("xyz", status=duj.STATUS_QUEUED, phase="queued", progress=0)
    assert "droneops:flightupload:job:xyz" in store


def test_per_file_defaults_to_empty_list(monkeypatch):
    store: dict = {}
    _patch_redis(monkeypatch, store)
    duj.write_batch_state("b", status=duj.STATUS_QUEUED, phase="queued", progress=0)
    state = duj.read_batch_state("b")
    assert state["per_file"] == []


def test_error_field_round_trips(monkeypatch):
    store: dict = {}
    _patch_redis(monkeypatch, store)
    duj.write_batch_state(
        "b", status=duj.STATUS_FAILED, phase="error", progress=100,
        error="flight-parser service unavailable",
    )
    state = duj.read_batch_state("b")
    assert state["status"] == duj.STATUS_FAILED
    assert state["error"] == "flight-parser service unavailable"


# ── Progress clamping ────────────────────────────────────────────────


def test_progress_is_clamped_to_0_100(monkeypatch):
    store: dict = {}
    _patch_redis(monkeypatch, store)

    duj.write_batch_state("hi", status=duj.STATUS_RUNNING, phase="parsing", progress=250)
    assert duj.read_batch_state("hi")["progress"] == 100

    duj.write_batch_state("lo", status=duj.STATUS_RUNNING, phase="parsing", progress=-7)
    assert duj.read_batch_state("lo")["progress"] == 0


# ── Absent / corrupt → None ──────────────────────────────────────────


def test_read_returns_none_for_absent_record(monkeypatch):
    _patch_redis(monkeypatch, {})
    assert duj.read_batch_state("nope") is None


def test_read_returns_none_for_corrupt_record(monkeypatch):
    store = {"droneops:flightupload:job:bad": b"{not valid json"}
    _patch_redis(monkeypatch, store)
    assert duj.read_batch_state("bad") is None


# ── Best-effort: Redis errors are swallowed ──────────────────────────


def test_write_swallows_redis_errors(monkeypatch):
    def _boom():
        raise RuntimeError("redis down")

    monkeypatch.setattr(duj, "_redis_client", _boom)
    # Must not raise — the parse must continue even if the overlay can't be written.
    duj.write_batch_state("b", status=duj.STATUS_RUNNING, phase="parsing", progress=10)


def test_read_swallows_redis_errors_and_returns_none(monkeypatch):
    def _boom():
        raise RuntimeError("redis down")

    monkeypatch.setattr(duj, "_redis_client", _boom)
    assert duj.read_batch_state("b") is None


def test_stored_payload_is_json_serializable(monkeypatch):
    """The overlay must be plain JSON so the poll route can return it as-is."""
    store: dict = {}
    _patch_redis(monkeypatch, store)
    duj.write_batch_state(
        "b", status=duj.STATUS_RUNNING, phase="parsing", progress=50,
        per_file=[{"name": "x.txt", "state": "parsing",
                   "imported": 0, "skipped": 0, "error": None}],
    )
    raw = store["droneops:flightupload:job:b"]
    # Round-trips through json without error and preserves structure.
    decoded = json.loads(raw)
    assert decoded["per_file"][0]["name"] == "x.txt"
