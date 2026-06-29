"""ADR-0028 H1/H2/M8 — report metric resolution (live scalars, ghost, altitude)."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

from app.routers import reports as R


def _live_row(fid, *, duration, distance, alt, source="dji_txt", notes=None):
    return SimpleNamespace(
        id=fid, duration_secs=duration, total_distance=distance,
        max_altitude=alt, max_speed=10.0, source=source, notes=notes,
    )


def _mf(*, flight_id=None, cache=None, aircraft_name=None):
    aircraft = SimpleNamespace(model_name=aircraft_name) if aircraft_name else None
    return SimpleNamespace(
        flight_id=flight_id, opendronelog_flight_id=None, id=uuid.uuid4(),
        flight_data_cache=cache, aircraft=aircraft,
    )


# ── H2: live Flight scalars win over the cache snapshot ──────────────────

def test_resolve_prefers_live_scalars_over_cache():
    fid = uuid.uuid4()
    mf = _mf(flight_id=fid, cache={"duration_secs": 999, "total_distance": 999,
                                   "max_altitude": 999})
    live = {fid: _live_row(fid, duration=120.0, distance=500.0, alt=80.0)}
    m = R._resolve_flight_metrics(mf, live)
    assert m["duration_secs"] == 120.0
    assert m["distance_m"] == 500.0
    assert m["max_altitude_m"] == 80.0


def test_resolve_falls_back_to_cache_for_legacy_odl():
    mf = _mf(flight_id=None, cache={"duration_secs": 200, "total_distance": 1500,
                                    "max_altitude": 500.0, "source": "opendronelog_import"})
    m = R._resolve_flight_metrics(mf, {})
    assert m["duration_secs"] == 200
    assert m["distance_m"] == 1500


# ── M8: ghost detection from resolved metrics ────────────────────────────

def test_ghost_flagged_when_short_and_stationary():
    fid = uuid.uuid4()
    mf = _mf(flight_id=fid)
    live = {fid: _live_row(fid, duration=5.0, distance=2.0, alt=0.0)}
    assert R._resolve_flight_metrics(mf, live)["is_ghost"] is True


def test_real_short_flight_is_not_ghost():
    fid = uuid.uuid4()
    mf = _mf(flight_id=fid)
    live = {fid: _live_row(fid, duration=20.0, distance=300.0, alt=50.0)}
    assert R._resolve_flight_metrics(mf, live)["is_ghost"] is False


# ── H1: Part-107 + ceiling-limited altitude flags ────────────────────────

def test_over_400ft_flag():
    fid = uuid.uuid4()
    mf = _mf(flight_id=fid)
    # 130 m AGL > 121.92 m (400 ft)
    live = {fid: _live_row(fid, duration=120.0, distance=500.0, alt=130.0)}
    assert R._resolve_flight_metrics(mf, live)["over_400ft"] is True
    # 100 m AGL < 400 ft
    live2 = {fid: _live_row(fid, duration=120.0, distance=500.0, alt=100.0)}
    assert R._resolve_flight_metrics(mf, live2)["over_400ft"] is False


def test_odl_ceiling_limited_flag():
    fid = uuid.uuid4()
    mf = _mf(flight_id=fid)
    live = {fid: _live_row(fid, duration=120.0, distance=500.0, alt=500.0,
                           source="opendronelog_import")}
    m = R._resolve_flight_metrics(mf, live)
    assert m["ceiling_limited"] is True
    # a dji_txt flight at 500 m is NOT a ceiling artifact
    live2 = {fid: _live_row(fid, duration=120.0, distance=500.0, alt=500.0,
                            source="dji_txt")}
    assert R._resolve_flight_metrics(mf, live2)["ceiling_limited"] is False


# ── H1: summaries render the altitude flags + aborted handling ───────────

def test_summary_annotates_over_400ft():
    fid = uuid.uuid4()
    mf = _mf(flight_id=fid, aircraft_name="Mavic 3")
    live = {fid: _live_row(fid, duration=120.0, distance=500.0, alt=130.0)}
    mission = SimpleNamespace(flights=[mf])
    summaries = R._build_flight_summaries(mission, live)
    assert len(summaries) == 1
    assert "exceeds the 400 ft AGL" in summaries[0]["max_altitude"]
    assert summaries[0]["over_400ft"] is True


def test_summary_marks_ceiling_limited_not_over400():
    fid = uuid.uuid4()
    mf = _mf(flight_id=fid, aircraft_name="M300")
    live = {fid: _live_row(fid, duration=120.0, distance=500.0, alt=500.0,
                           source="opendronelog_import")}
    mission = SimpleNamespace(flights=[mf])
    s = R._build_flight_summaries(mission, live)[0]
    assert "ceiling-limited" in s["max_altitude"]
    assert s["over_400ft"] is False


def test_summary_flags_aborted_launch():
    fid = uuid.uuid4()
    mf = _mf(flight_id=fid, aircraft_name="Mini 4")
    live = {fid: _live_row(fid, duration=4.0, distance=1.0, alt=0.0)}
    mission = SimpleNamespace(flights=[mf])
    s = R._build_flight_summaries(mission, live)[0]
    assert s.get("aborted") is True
    assert s["max_altitude"] == "N/A — aborted launch"
