"""Regression tests for the business-signals date-window helper.

DroneOpsCommand stores `paid_at` / `updated_at` / `created_at` as tz-NAIVE UTC
(`datetime.utcnow()`). The business-signals endpoint compares those columns
against window-start datetimes; if those window datetimes are tz-AWARE, asyncpg
raises `DataError` ("can't subtract offset-naive and offset-aware datetimes")
and every windowed metric silently zeroes out via `_safe_scalar`. (This is what
was feeding J.A.R.V.I.S. zeros and made the metric unusable for the marketing
revenue bridge.)

These tests pin the invariant: the window datetimes must be tz-naive UTC.
"""
from datetime import datetime, timezone

from app.routers.business_signals import _utc_windows


def test_default_windows_are_tz_naive():
    now, d30, d90 = _utc_windows()
    assert now.tzinfo is None
    assert d30.tzinfo is None
    assert d90.tzinfo is None


def test_windows_offsets_are_30_and_90_days():
    now, d30, d90 = _utc_windows()
    assert (now - d30).days == 30
    assert (now - d90).days == 90


def test_tz_aware_input_is_coerced_to_naive_utc():
    # Even handed a tz-aware "now", the helper must return naive UTC so the
    # comparison against naive columns stays asyncpg-safe.
    aware = datetime(2026, 5, 24, 17, 0, 0, tzinfo=timezone.utc)
    now, d30, d90 = _utc_windows(aware)
    assert now.tzinfo is None
    assert d30.tzinfo is None
    assert d90.tzinfo is None
    assert now == datetime(2026, 5, 24, 17, 0, 0)
