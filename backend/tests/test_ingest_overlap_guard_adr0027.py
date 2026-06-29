"""ADR-0027 — regression tests for the ingest overlap / cadence guard.

``_check_ingest_anomalies`` is defense-in-depth: it FLAGS (never rejects) two
fingerprints of a mis-parsed duration — an implausible point/duration cadence,
and two flights of the same physical airframe whose airborne intervals overlap.
Had it existed, the 1.5×-inflated Savannah Mavic durations would have been
caught at upload (the inflated intervals overlap the next flight's start).

Hermetic: the ``AsyncSession`` is mocked and ``send_alert`` is patched so we
assert on the paging decision without touching the network.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

import tests.conftest  # noqa: F401 — env stubs

from app.routers import flight_library
from app.routers.flight_library import _check_ingest_anomalies


def _flight(*, start, dur, serial="1581F986C258E002", pts=None, name="F"):
    return SimpleNamespace(
        id=uuid4(),
        name=name,
        drone_serial=serial,
        start_time=start,
        duration_secs=dur,
        point_count=pts if pts is not None else int(dur * 10),
    )


def _db_with_existing(rows):
    """rows: list of (id, name, start_time, duration_secs)."""
    res = MagicMock()
    res.all.return_value = rows
    db = MagicMock()
    db.execute = AsyncMock(return_value=res)
    return db


@pytest.mark.asyncio
async def test_overlap_pages_high():
    new = _flight(start=datetime(2026, 6, 27, 18, 29, 10), dur=870.0, name="new")
    # Existing flight of the SAME airframe still "airborne" when the new one
    # starts (its inflated duration runs long past) → overlap.
    existing = (uuid4(), "old", datetime(2026, 6, 27, 18, 1, 49), 7000.0)
    db = _db_with_existing([existing])
    with patch.object(flight_library, "send_alert", new=AsyncMock(return_value=True)) as alert:
        await _check_ingest_anomalies(db, new)
    assert alert.await_count == 1
    kwargs = alert.await_args.kwargs
    assert kwargs["priority"] == 1  # high
    assert kwargs["topic"] == "droneops-flight-overlap"


@pytest.mark.asyncio
async def test_no_overlap_does_not_page():
    new = _flight(start=datetime(2026, 6, 27, 18, 29, 10), dur=600.0, name="new")
    # Existing flight ended well before the new one starts.
    existing = (uuid4(), "old", datetime(2026, 6, 27, 17, 0, 0), 600.0)
    db = _db_with_existing([existing])
    with patch.object(flight_library, "send_alert", new=AsyncMock(return_value=True)) as alert:
        await _check_ingest_anomalies(db, new)
    assert alert.await_count == 0


@pytest.mark.asyncio
async def test_different_airframe_never_overlaps():
    new = _flight(start=datetime(2026, 6, 27, 18, 29, 10), dur=3000.0, name="new")
    # Same wall-clock window but a different serial is queried out by the WHERE
    # clause; the guard only ever sees same-serial rows, so: no rows → no page.
    db = _db_with_existing([])
    with patch.object(flight_library, "send_alert", new=AsyncMock(return_value=True)) as alert:
        await _check_ingest_anomalies(db, new)
    assert alert.await_count == 0


@pytest.mark.asyncio
async def test_implausible_cadence_does_not_crash_or_page_alone():
    # Cadence 0.05 Hz (5 pts over 100s) is implausible but, with no overlapping
    # neighbour, must be logged only — not paged.
    new = _flight(start=datetime(2026, 6, 27, 18, 0, 0), dur=100.0, pts=5, name="sparse")
    db = _db_with_existing([])
    with patch.object(flight_library, "send_alert", new=AsyncMock(return_value=True)) as alert:
        await _check_ingest_anomalies(db, new)
    assert alert.await_count == 0


@pytest.mark.asyncio
async def test_missing_serial_or_start_is_noop():
    base = dict(start=datetime(2026, 6, 27, 18, 0, 0), dur=600.0)
    for override in ({"serial": None}, {"start": None}):
        new = _flight(**{**base, **override})
        db = _db_with_existing([(uuid4(), "x", datetime(2026, 6, 27, 18, 0, 0), 600.0)])
        with patch.object(flight_library, "send_alert", new=AsyncMock(return_value=True)) as alert:
            await _check_ingest_anomalies(db, new)
        assert alert.await_count == 0
