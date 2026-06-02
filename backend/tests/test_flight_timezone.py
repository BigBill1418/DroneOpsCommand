"""Flight timezone handling (ADR-0017).

Regression coverage for the bug where a flight flown the evening of June 1 in
the Pacific timezone was labeled June 2 because its UTC instant fell on the
next calendar day and no UTC->local conversion was applied.
"""

from datetime import datetime, timezone

from app.utils.timezone import (
    as_utc,
    iso_utc,
    local_date_compact,
    local_date_str,
    to_operator_local,
)


# The real production row: DJI-Matrice-4TD flown 2026-06-01 ~20:27 PDT,
# stored naive-UTC as 2026-06-02 03:27.
EVENING_FLIGHT_UTC_NAIVE = datetime(2026, 6, 2, 3, 27, 27)


def test_evening_pacific_flight_dates_to_local_day():
    """The core regression: UTC June 2 instant is a June 1 flight in Pacific."""
    assert local_date_str(EVENING_FLIGHT_UTC_NAIVE) == "2026-06-01"
    assert local_date_compact(EVENING_FLIGHT_UTC_NAIVE) == "20260601"


def test_to_operator_local_wall_clock():
    local = to_operator_local(EVENING_FLIGHT_UTC_NAIVE)
    # 03:27 UTC == 20:27 PDT (UTC-7 in June)
    assert (local.year, local.month, local.day) == (2026, 6, 1)
    assert (local.hour, local.minute) == (20, 27)
    assert local.utcoffset().total_seconds() == -7 * 3600


def test_iso_utc_marks_naive_as_utc():
    """Naive stored value must serialize with an explicit UTC offset so the
    frontend cannot misread it as local wall-clock time."""
    s = iso_utc(EVENING_FLIGHT_UTC_NAIVE)
    assert s == "2026-06-02T03:27:27+00:00"
    assert iso_utc(None) is None


def test_iso_utc_preserves_aware_instant():
    aware = datetime(2026, 6, 2, 3, 27, 27, tzinfo=timezone.utc)
    assert iso_utc(aware) == "2026-06-02T03:27:27+00:00"


def test_as_utc_idempotent_on_aware():
    aware = datetime(2026, 6, 2, 3, 27, 27, tzinfo=timezone.utc)
    assert as_utc(aware) == aware


def test_midday_flight_unaffected():
    """A midday Pacific flight is the same date in both zones — no regression."""
    midday = datetime(2026, 6, 1, 19, 0, 0)  # 12:00 PDT
    assert local_date_str(midday) == "2026-06-01"
