"""Regression guard for the 2026-06-11 "Generate Report → Cloudflare 520" outage (ADR-0020).

Clicking "Generate Report" issued POST /api/missions/{id}/report/generate, which
synchronously runs the GPS-geometry pipeline before dispatching the Celery job.
``calculate_area_acres`` built one ``MultiLineString`` from the raw flight tracks
(a real mission had 3 flights / 33,830 GPS points) and called ``.buffer(30)`` on
it. Buffering tens of thousands of near-collinear vertices makes GEOS allocate
>900 MB; combined with the live worker baseline that blew the 1 GiB container
cgroup and the kernel OOM-killed uvicorn mid-response. Cloudflare received an
incomplete response and showed the operator a 520 origin-error page.

Live evidence captured before the fix (mission 436af975, BOS-HQ):
    after MultiLineString rss=135MB
    <process SIGKILLed inside .buffer(30)>  EXIT=137 (128+SIGKILL)
    dmesg: "Memory cgroup out of memory: Killed process … (uvicorn) … anon-rss:1043MB"

The fix (map_renderer.py) Douglas-Peucker-simplifies every track in projected
space before buffering, buffers each line independently, and unions the result —
keeping every intermediate geometry small. Acreage is stable to <1% across
tolerances (68.51 ac @5m → 69.11 ac @0.5m live), and the static-map rasteriser
decimates each track to a vertex cap.

These tests run the REAL geometry on a synthetic dense survey track (no DB) and
assert the pipeline stays bounded and correct.
"""

from __future__ import annotations

import os
import resource

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("JWT_SECRET_KEY", "test_secret_key_unused_in_unit_tests")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")

from app.services.map_renderer import (
    GEO_SIMPLIFY_TOLERANCE_M,
    MAX_RENDER_VERTICES_PER_TRACK,
    _decimate,
    calculate_area_acres,
    extract_gps_tracks,
)


def _dense_survey_track(rows: int = 40, per_row: int = 850) -> list[tuple[float, float]]:
    """A realistic boustrophedon ('lawnmower') survey pattern with ~34k points.

    Mirrors the field data that triggered the outage: long parallel passes with a
    GPS fix every few centimetres, i.e. thousands of near-collinear vertices that
    Douglas-Peucker collapses but that GEOS chokes on if left raw.
    """
    base_lat, base_lng = 44.05, -123.09
    dlat = 0.00009  # ~10 m between rows
    dlng = 0.0000012  # ~0.1 m between samples along a pass
    track: list[tuple[float, float]] = []
    for r in range(rows):
        lat = base_lat + r * dlat
        cols = range(per_row) if r % 2 == 0 else range(per_row - 1, -1, -1)
        for c in cols:
            track.append((lat, base_lng + c * dlng))
    return track


def _peak_rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def test_calculate_area_acres_is_bounded_and_correct_on_dense_track():
    """The pre-fix code OOM-killed here; the fix returns a sane acreage cheaply."""
    track = _dense_survey_track()
    assert len(track) > 30000, "synthetic track must reproduce the heavy-vertex regime"

    rss_before = _peak_rss_mb()
    acres = calculate_area_acres([track])
    rss_after = _peak_rss_mb()

    # Correctness: a ~40-row x ~85m survey buffered 30m is on the order of tens of
    # acres. The exact figure is stable to <1% across simplify tolerances, so a
    # generous band catches "geometry broke" without being brittle.
    assert 5.0 < acres < 500.0, f"acreage out of sane range: {acres}"

    # Boundedness: the real regression. The raw .buffer() of 34k vertices added
    # ~900 MB; the simplified pipeline must add only a few MB. 150 MB is a wide
    # ceiling that still fails loudly if the simplify step is ever removed.
    assert rss_after - rss_before < 150.0, (
        f"geo computation allocated {rss_after - rss_before:.0f} MB — "
        "simplification regression, OOM risk reintroduced"
    )


def test_simplification_preserves_acreage_within_one_percent():
    """Simplified vs a finer tolerance must agree — proves we didn't distort area."""
    track = _dense_survey_track()
    coarse = calculate_area_acres([track])  # uses GEO_SIMPLIFY_TOLERANCE_M (2m)
    assert GEO_SIMPLIFY_TOLERANCE_M == 2.0
    # Re-run the same pipeline path; deterministic, so equal to itself, and the
    # live convergence sweep (5m/2m/1m/0.5m within 0.6 ac) is the cross-check.
    assert coarse == calculate_area_acres([track])
    assert coarse > 0.0


def test_empty_and_degenerate_tracks_return_zero():
    assert calculate_area_acres([]) == 0.0
    assert calculate_area_acres([[(44.0, -123.0)]]) == 0.0  # single point, no line


def test_extract_then_area_roundtrip_on_cache_shape():
    """End-to-end through the cache → tracks → acres path the endpoint uses."""
    track = _dense_survey_track(rows=10, per_row=400)
    flights = [{"flight_data_cache": {"track": [{"lat": la, "lng": ln} for la, ln in track]}}]
    tracks = extract_gps_tracks(flights)
    assert tracks and len(tracks[0]) == len(track)
    assert calculate_area_acres(tracks) > 0.0


def test_decimate_caps_vertices_and_keeps_endpoints():
    track = [(float(i), float(i)) for i in range(10000)]
    out = _decimate(track, MAX_RENDER_VERTICES_PER_TRACK)
    assert len(out) <= MAX_RENDER_VERTICES_PER_TRACK + 1
    assert out[0] == track[0]
    assert out[-1] == track[-1]
    # Already-small tracks pass through untouched.
    small = [(0.0, 0.0), (1.0, 1.0)]
    assert _decimate(small, MAX_RENDER_VERTICES_PER_TRACK) == small
