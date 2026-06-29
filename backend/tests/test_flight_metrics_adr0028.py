"""ADR-0028 C1 — GPS outlier gate + ODL distance sanitation (flight_metrics)."""

from __future__ import annotations

import math

import pytest

from app.services.flight_metrics import (
    MAX_SEGMENT_DISTANCE_M,
    gated_track_distance,
    haversine_m,
    odl_distance_is_implausible,
    sanitize_odl_distance,
    segment_ok,
)


def test_haversine_known_distance():
    # ~111.32 km per degree of longitude at the equator.
    d = haversine_m(0.0, 0.0, 0.0, 1.0)
    assert 111_000 < d < 111_400


def test_segment_ok_speed_gate():
    assert segment_ok(3.0, 0.1)        # 30 m/s — fine
    assert not segment_ok(7.0, 0.1)    # 70 m/s — too fast
    assert not segment_ok(12_000_000.0, 1.0)  # teleport


def test_segment_ok_distance_fallback_when_no_dt():
    assert segment_ok(7.6, None)
    assert segment_ok(MAX_SEGMENT_DISTANCE_M, None)
    assert not segment_ok(MAX_SEGMENT_DISTANCE_M + 1, None)
    # dt <= 0 falls back to the distance gate too.
    assert segment_ok(7.0, 0.0)
    assert not segment_ok(9999.0, 0.0)


def _line(n: int, step_deg: float = 6e-5):
    """A straight track of n points spaced step_deg apart (~6.6 m at equator)."""
    return [{"lat": 0.0, "lng": i * step_deg} for i in range(n)]


def test_gated_distance_normal_track_unchanged():
    track = _line(100)
    dist, dropped = gated_track_distance(track)
    assert dropped == 0
    # 99 segments × ~6.66 m
    assert 600 < dist < 700


def test_gated_distance_drops_single_teleport():
    track = _line(50)
    # inject a teleport to null island far away, then back
    track.insert(25, {"lat": 40.0, "lng": -120.0})
    dist, dropped = gated_track_distance(track)
    # the two segments to/from the teleport are dropped; the rest is intact.
    assert dropped == 2
    assert dist < 1000  # not millions


def test_gated_distance_short_track_is_zero():
    assert gated_track_distance([]) == (0.0, 0)
    assert gated_track_distance([{"lat": 1, "lng": 1}]) == (0.0, 0)


def test_gated_distance_accepts_lonlat_pairs():
    # ODL raw track format [lon, lat, alt]
    track = [[i * 6e-5, 0.0, 10.0] for i in range(10)]
    dist, dropped = gated_track_distance(track)
    assert dropped == 0
    assert dist > 0


def test_odl_implausible_detection():
    # avg can never exceed max: 12.5M m / 554 s = 22,722 m/s vs 29 m/s max
    assert odl_distance_is_implausible(12_583_855, 553.8, 29.09)
    # a plausible flight
    assert not odl_distance_is_implausible(3000, 553.8, 29.09)
    # no max speed → absolute 60 m/s cap
    assert odl_distance_is_implausible(100_000, 100, 0)
    assert not odl_distance_is_implausible(5000, 100, 0)
    # degenerate inputs are never "implausible"
    assert not odl_distance_is_implausible(0, 100, 10)
    assert not odl_distance_is_implausible(1000, 0, 10)


def test_sanitize_passthrough_when_plausible():
    track = _line(50)
    dist, note = sanitize_odl_distance(300.0, 553.8, 29.0, track)
    assert dist == 300.0
    assert note is None


def test_sanitize_recomputes_from_track():
    track = _line(100)  # ~660 m of real path
    dist, note = sanitize_odl_distance(12_583_855, 553.8, 29.09, track)
    assert note is not None
    assert note["method"] == "track_recompute"
    assert note["original_distance_m"] == 12_583_855
    assert 600 < dist < 700


def test_sanitize_clamps_when_track_unusable():
    # implausible distance, no track → clamp to max_speed × duration
    dist, note = sanitize_odl_distance(12_583_855, 553.8, 29.09, None)
    assert note["method"] == "speed_clamp"
    assert math.isclose(dist, 29.09 * 553.8, rel_tol=1e-6)
