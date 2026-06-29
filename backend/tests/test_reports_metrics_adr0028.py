"""Report metric resolution (live scalars, ghost, altitude).

ADR-0028 H2/M8 (live scalars, ghost) + ADR-0029, which REVERSES ADR-0028 H1:
mission reports are client deliverables, not compliance audits, so NO altitude-
limit / 400 ft / Part-107 exceedance flag is derived or rendered. The only
altitude caveat is a neutral data-confidence note for unverified ODL peaks.
"""

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


# ── ADR-0029: NO altitude-limit / Part-107 / 400 ft exceedance flag ──────
# Reverses ADR-0028 H1. Mission reports are client deliverables, not compliance
# audits — they must NEVER flag, list, or comment on altitude-limit exceedance.

# Terms that must NEVER appear in any per-flight summary the LLM receives.
_FORBIDDEN_ALT_TERMS = (
    "400", "121.92", "part 107", "part-107", "ceiling", "exceed", "limit",
    "over 400", "agl part", "above the", "regulatory",
)


def _assert_no_limit_language(summary: dict) -> None:
    blob = " ".join(str(v) for v in summary.values()).lower()
    for term in _FORBIDDEN_ALT_TERMS:
        assert term not in blob, f"forbidden altitude-limit term {term!r} in {summary!r}"
    # The exceedance flag field itself must be gone entirely.
    assert "over_400ft" not in summary
    assert "ceiling_limited" not in summary


def test_no_over_400ft_flag_derived():
    fid = uuid.uuid4()
    mf = _mf(flight_id=fid)
    # 130 m AGL would once have been flagged "over 400 ft" — no longer.
    live = {fid: _live_row(fid, duration=120.0, distance=500.0, alt=130.0)}
    m = R._resolve_flight_metrics(mf, live)
    assert "over_400ft" not in m
    assert "ceiling_limited" not in m


def test_resolve_marks_unverified_odl_peak():
    """Data-confidence flag only — NOT a regulatory/limit check."""
    fid = uuid.uuid4()
    mf = _mf(flight_id=fid)
    live = {fid: _live_row(fid, duration=120.0, distance=500.0, alt=500.0,
                           source="opendronelog_import")}
    m = R._resolve_flight_metrics(mf, live)
    assert m["unverified_peak"] is True
    # a dji_txt flight at 500 m is NOT an ODL device-max artifact
    live2 = {fid: _live_row(fid, duration=120.0, distance=500.0, alt=500.0,
                            source="dji_txt")}
    assert R._resolve_flight_metrics(mf, live2)["unverified_peak"] is False


# ── ADR-0029: summaries carry NO altitude-limit language ─────────────────

def test_summary_high_altitude_has_no_limit_language():
    fid = uuid.uuid4()
    mf = _mf(flight_id=fid, aircraft_name="Mavic 3")
    # 190.8 m AGL (626 ft) — the savannah case once flagged "exceeds 400 ft".
    live = {fid: _live_row(fid, duration=120.0, distance=500.0, alt=190.8)}
    mission = SimpleNamespace(flights=[mf])
    summaries = R._build_flight_summaries(mission, live)
    assert len(summaries) == 1
    # Altitude is still presented as neutral capture data, unit-correct...
    assert "190.8 m AGL" in summaries[0]["max_altitude"]
    assert "626 ft" in summaries[0]["max_altitude"]
    # ...but with ZERO limit/exceedance/Part-107 commentary.
    _assert_no_limit_language(summaries[0])


def test_summary_unverified_odl_peak_neutral_note():
    fid = uuid.uuid4()
    mf = _mf(flight_id=fid, aircraft_name="M300")
    live = {fid: _live_row(fid, duration=120.0, distance=500.0, alt=500.0,
                           source="opendronelog_import")}
    mission = SimpleNamespace(flights=[mf])
    s = R._build_flight_summaries(mission, live)[0]
    assert "unverified (device-reported maximum" in s["max_altitude"]
    _assert_no_limit_language(s)


def test_summary_flags_aborted_launch():
    fid = uuid.uuid4()
    mf = _mf(flight_id=fid, aircraft_name="Mini 4")
    live = {fid: _live_row(fid, duration=4.0, distance=1.0, alt=0.0)}
    mission = SimpleNamespace(flights=[mf])
    s = R._build_flight_summaries(mission, live)[0]
    assert s.get("aborted") is True
    assert s["max_altitude"] == "N/A — aborted launch"
