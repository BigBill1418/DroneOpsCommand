"""ADR-0049 — the EyesOn flight-record read API.

Hermetic (no DB): the route coroutines are driven with a stub session that
returns queued results in issue order, the same pattern as
``test_flight_details_endpoint_shapes.py``. Fixtures are the 2026-09-16 (PDT)
Matrice 4TD flights EyesOn matches against.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from datetime import datetime
from types import SimpleNamespace

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("JWT_SECRET_KEY", "test_secret_key_unused_in_unit_tests")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")

from fastapi import HTTPException

from app.config import settings
from app.routers import eyeson_integration as mod

TOKEN = "eyeson-test-token-0123456789abcdef"


class _Result:
    def __init__(self, rows=None, scalar=None, one=None):
        self._rows, self._scalar, self._one = rows or [], scalar, one

    def all(self):
        return self._rows

    def scalar_one_or_none(self):
        return self._scalar

    def one_or_none(self):
        return self._one


class _Session:
    def __init__(self, results):
        self._queue = list(results)
        self.statements = []

    async def execute(self, stmt):
        self.statements.append(str(stmt))
        return self._queue.pop(0)


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(settings, "eyeson_service_token_sha256", hashlib.sha256(TOKEN.encode()).hexdigest())


def _flight_row(start, duration, first=None, last=None, name="DJI-Matrice-4TD_20260916_0003"):
    return SimpleNamespace(
        id=uuid.uuid4(), name=name, start_time=start, duration_secs=duration,
        drone_model="DJI Matrice 4TD", drone_serial="1581F8HGX255P00A", source="dji_txt",
        first_frame_at=first, last_frame_at=last,
    )


# ── auth ──────────────────────────────────────────────────────────────
def test_unconfigured_is_dark_503(monkeypatch):
    monkeypatch.setattr(settings, "eyeson_service_token_sha256", "")
    with pytest.raises(HTTPException) as e:
        mod.require_eyeson_service_token(TOKEN)
    assert e.value.status_code == 503


def test_missing_and_wrong_token_401(configured):
    for bad in (None, "", "wrong-token"):
        with pytest.raises(HTTPException) as e:
            mod.require_eyeson_service_token(bad)
        assert e.value.status_code == 401


def test_correct_token_passes(configured):
    assert mod.require_eyeson_service_token(TOKEN) is None


def test_routes_all_carry_the_token_dependency():
    """A route added later without the dependency would be an open read of flights."""
    for route in mod.router.routes:
        deps = [d.call for d in route.dependant.dependencies]
        assert mod.require_eyeson_service_token in deps, route.path


# ── list ──────────────────────────────────────────────────────────────
async def test_window_validation():
    for start, end in (("nope", "2026-09-17T03:00:00Z"),
                       ("2026-09-17T03:00:00Z", "2026-09-17T02:00:00Z"),
                       ("2026-09-15T00:00:00Z", "2026-09-17T00:00:01Z")):
        with pytest.raises(HTTPException) as e:
            await mod.list_flights_in_window(start=start, end=end, db=_Session([]))
        assert e.value.status_code == 400


async def test_overlap_filter_uses_frame_bounds_and_drops_non_overlapping():
    # Session 263 ± 10 min: 02:46:49 .. 03:09:35 UTC.
    f1 = _flight_row(datetime(2026, 9, 17, 2, 59, 43, 589000), 1290.4,
                     datetime(2026, 9, 17, 2, 59, 43, 696000), datetime(2026, 9, 17, 3, 21, 14, 99000))
    earlier = _flight_row(datetime(2026, 9, 16, 2, 30, 5), 1160.6, name="DJI-Matrice-4TD_20260915_0001")
    session = _Session([_Result(rows=[earlier, f1])])
    out = await mod.list_flights_in_window(start="2026-09-17T02:46:49Z", end="2026-09-17T03:09:35Z", db=session)
    assert [f["name"] for f in out["flights"]] == ["DJI-Matrice-4TD_20260916_0003"]
    got = out["flights"][0]
    assert got["first_frame_at"] == "2026-09-17T02:59:43.696000+00:00"
    assert got["start_time"].endswith("+00:00")
    sql = session.statements[0]
    for heavy in ("gps_track", "telemetry", "raw_metadata"):
        assert heavy not in sql, f"list query must not select {heavy} (ADR-0019)"


async def test_flight_started_before_window_but_overlapping_is_kept():
    f = _flight_row(datetime(2026, 9, 17, 2, 59, 43), 1290.4)  # ends 03:21:13
    out = await mod.list_flights_in_window(start="2026-09-17T03:10:00Z", end="2026-09-17T03:15:00Z",
                                           db=_Session([_Result(rows=[f])]))
    assert len(out["flights"]) == 1


async def test_epoch_frame_bound_does_not_widen_the_match():
    """Real 0002: first_frame_at = 1970-01-01. Trusted, it overlaps every window."""
    f2 = _flight_row(datetime(2026, 9, 17, 3, 22, 1, 99000), 586.9,
                     datetime(1970, 1, 1), datetime(2026, 9, 17, 3, 31, 47, 925000),
                     name="DJI-Matrice-4TD_20260916_0002")
    out = await mod.list_flights_in_window(start="2026-09-17T02:46:49Z", end="2026-09-17T03:09:35Z",
                                           db=_Session([_Result(rows=[f2])]))
    assert out["flights"] == []


# ── timeline ──────────────────────────────────────────────────────────
async def test_timeline_404():
    with pytest.raises(HTTPException) as e:
        await mod.flight_timeline(flight_id=uuid.uuid4(), max_points=100, db=_Session([_Result(one=None)]))
    assert e.value.status_code == 404


async def test_timeline_shape_and_stride_downsampling():
    n = 50
    stamps = [f"2026-09-17T02:59:{43 + i // 10:02d}.{(i % 10)}00+00:00" for i in range(n)]
    telemetry = {"timestamps": stamps, "altitude": list(range(n)), "speed": [0.5] * n,
                 "battery_pct": [99] * n}
    track = [{"lat": 44.0 + i, "lng": -123.0, "alt": 0, "timestamp": stamps[i], "speed": 0, "heading": 4.2}
             for i in range(n)]
    row = SimpleNamespace(start_time=datetime(2026, 9, 17, 2, 59, 43), telemetry=telemetry, gps_track=track)
    frame_rows = [("t_offset_s", [i * 0.1 for i in range(n)]), ("rc_uplink", [100] * n)]
    db = _Session([_Result(one=row), _Result(scalar=datetime(2026, 9, 17, 2, 59, 43, 696000)),
                   _Result(rows=frame_rows)])
    out = await mod.flight_timeline(flight_id=uuid.uuid4(), max_points=10, db=db)

    assert len(out["telemetry"]["timestamps"]) == 10
    assert out["telemetry"]["altitude"][0] == 0 and out["telemetry"]["altitude"][-1] == n - 1
    assert out["telemetry"]["signal_strength"] is None  # absent key → null, not []
    assert len(out["track"]) == 10 and set(out["track"][0]) == {"t", "lat", "lng", "heading"}
    assert out["frame"]["origin"] == "2026-09-17T02:59:43.696000+00:00"
    assert len(out["frame"]["t_offset_s"]) == 10 and out["frame"]["vps_height_m"] is None
