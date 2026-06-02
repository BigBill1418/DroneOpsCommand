"""Timezone helpers for flight date handling (ADR-0017).

Flight instants are captured and stored as **naive UTC** datetimes (DJI logs
record UTC; see ``flight_library._parse_datetime``). The *calendar date* of a
flight, however, must be the date in the **operator's local timezone** — a
flight flown at 20:27 PDT on June 1 is a June-1 flight, even though that instant
is 03:27 UTC on June 2.

Every place that reduces a stored UTC instant to a date or a wire string MUST go
through these helpers so the conversion happens in exactly one, consistent way.

The operator timezone is fixed per deployment via ``settings.operator_timezone``
(default ``America/Los_Angeles``). See ADR-0017 for why a fixed operator TZ was
chosen over per-flight GPS-derived timezones.
"""

from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo

from app.config import settings


@lru_cache(maxsize=4)
def operator_tz(name: str | None = None) -> ZoneInfo:
    """Return the configured operator ZoneInfo (cached)."""
    return ZoneInfo(name or settings.operator_timezone)


def as_utc(dt: datetime) -> datetime:
    """Return a timezone-aware UTC datetime.

    Naive datetimes are *assumed* to already be UTC (the storage convention)
    and simply stamped. Aware datetimes are converted to UTC.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def to_operator_local(dt: datetime) -> datetime:
    """Convert a stored (naive-UTC or aware) instant to operator-local time."""
    return as_utc(dt).astimezone(operator_tz())


def local_date_str(dt: datetime) -> str:
    """Operator-local calendar date as ``YYYY-MM-DD``."""
    return to_operator_local(dt).strftime("%Y-%m-%d")


def local_date_compact(dt: datetime) -> str:
    """Operator-local calendar date as ``YYYYMMDD`` (for flight names)."""
    return to_operator_local(dt).strftime("%Y%m%d")


def iso_utc(dt: datetime | None) -> str | None:
    """Serialize an instant as an unambiguous UTC ISO-8601 string (``…+00:00``).

    Stamps the storage-convention UTC tzinfo onto naive datetimes so API
    consumers never misread a naive timestamp as local wall-clock time — the
    bug behind flights showing the wrong calendar date (ADR-0017).
    """
    if dt is None:
        return None
    return as_utc(dt).isoformat()
