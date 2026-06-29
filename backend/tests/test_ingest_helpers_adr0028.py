"""ADR-0028 — pure-helper coverage for L4/L5 datetime parsing and M6 viability."""

from __future__ import annotations

from datetime import datetime, timezone

from app.routers.flight_library import _parse_datetime, _parsed_is_viable


# ── L4: a date-like string must not become a 1970 epoch ──────────────────

def test_bare_year_is_not_epoch_1970():
    # The old order ran float("2024") first → 1970-01-01T00:33:44.
    result = _parse_datetime("2024")
    assert result is None or result.year != 1970


def test_iso_parsed_before_numeric():
    dt = _parse_datetime("2024-01-15T14:30:00")
    assert dt == datetime(2024, 1, 15, 14, 30, 0)


def test_iso_with_z_normalized_to_naive_utc():
    dt = _parse_datetime("2024-01-15T14:30:00Z")
    assert dt.tzinfo is None
    assert dt == datetime(2024, 1, 15, 14, 30, 0)


def test_real_epoch_seconds_still_parses():
    # 1_700_000_000 = 2023-11-14T22:13:20 UTC
    dt = _parse_datetime("1700000000")
    assert dt.year == 2023


def test_real_epoch_millis_still_parses():
    dt = _parse_datetime("1700000000000")
    assert dt.year == 2023


# ── L5: tz-aware datetime objects normalized to naive UTC ────────────────

def test_tzaware_datetime_object_normalized():
    aware = datetime(2024, 1, 15, 6, 0, 0, tzinfo=timezone.utc)
    out = _parse_datetime(aware)
    assert out.tzinfo is None
    assert out == datetime(2024, 1, 15, 6, 0, 0)


def test_iso_with_offset_normalized_to_utc():
    # 14:30 -07:00 == 21:30 UTC
    dt = _parse_datetime("2024-01-15T14:30:00-07:00")
    assert dt.tzinfo is None
    assert dt == datetime(2024, 1, 15, 21, 30, 0)


# ── M6: minimal-viability gate ───────────────────────────────────────────

def test_viability_requires_one_real_signal():
    assert not _parsed_is_viable({}, None)
    assert not _parsed_is_viable({"point_count": 0, "duration_secs": 0}, None)
    assert _parsed_is_viable({"point_count": 5}, None)
    assert _parsed_is_viable({"duration_secs": 12.3}, None)
    assert _parsed_is_viable({}, datetime(2024, 1, 1))
