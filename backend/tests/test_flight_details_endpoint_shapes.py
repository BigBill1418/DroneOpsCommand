"""ADR-0043 §4.1 — ``GET /{flight_id}/details`` shape for every outcome.

Operator decision D3 puts the Flight Details link on **every** flight, of any
source. That makes "this flight has no extended data" a normal, well-formed
response rather than an error, and it makes ``unavailable_reason`` the field
the UI switches on. If that field is ever wrong the page shows the wrong
message — offering "re-process this flight" for a Litchi CSV that can never
produce details, or "not available for this source" for a DJI flight that just
needs a backfill.

These tests are hermetic (no DB): the endpoint coroutine is driven with a
stubbed session that returns queued results in the order the endpoint issues
its three statements.
"""

from __future__ import annotations

import os
import uuid
from types import SimpleNamespace

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("JWT_SECRET_KEY", "test_secret_key_unused_in_unit_tests")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")

from fastapi import HTTPException

from app.routers.flight_library import (
    _DETAILS_PAYLOAD_COLUMNS,
    _serialize_details,
    get_flight_details,
    get_flight_details_series,
)
from app.schemas.flight_details import (
    DETAILS_UNSUPPORTED_SOURCES,
    details_unavailable_reason,
)


# ── Stub session ───────────────────────────────────────────────────────
class _Result:
    def __init__(self, value, rows=None):
        self._value = value
        self._rows = rows or []

    def scalar_one_or_none(self):
        return self._value

    def scalar(self):
        return self._value

    def all(self):
        return self._rows


class _FakeSession:
    """Returns queued results in issue order; asserts the queue is consumed."""

    def __init__(self, results):
        self._queue = list(results)
        self.executed = 0

    async def execute(self, _stmt):
        self.executed += 1
        if not self._queue:
            raise AssertionError("endpoint issued more statements than queued")
        return self._queue.pop(0)


def _details_row(**overrides):
    """A details row stand-in carrying every mapped column."""
    row = SimpleNamespace(**{k: None for k in _DETAILS_PAYLOAD_COLUMNS})
    row.flight_id = uuid.uuid4()
    row.schema_version = 1
    row.frame_count = 13870
    row.photo_count = 5
    for k, v in overrides.items():
        setattr(row, k, v)
    return row


async def _call_details(source, details_row, index_rows=()):
    fid = uuid.uuid4()
    db = _FakeSession([
        _Result(source),
        _Result(details_row),
        _Result(None, rows=list(index_rows)),
    ])
    return await get_flight_details(flight_id=fid, db=db, _user=object())


# ── unavailable_reason, one test per outcome ───────────────────────────


@pytest.mark.asyncio
async def test_dji_without_a_row_reports_not_backfilled():
    resp = await _call_details("dji_txt", None)
    assert resp.unavailable_reason == "not_backfilled"
    assert resp.details is None
    assert resp.series_index == []
    assert resp.source == "dji_txt"


@pytest.mark.asyncio
@pytest.mark.parametrize("source", sorted(DETAILS_UNSUPPORTED_SOURCES))
async def test_csv_and_manual_sources_report_source_unsupported(source):
    resp = await _call_details(source, None)
    assert resp.unavailable_reason == "source_unsupported"
    assert resp.details is None


@pytest.mark.asyncio
async def test_opendronelog_import_reports_its_own_reason():
    """Distinct from ``source_unsupported``: an ODL row *could* gain details
    once an original file is recovered, so the UI must not tell the operator
    the source is structurally incapable."""
    resp = await _call_details("opendronelog_import", None)
    assert resp.unavailable_reason == "odl_import_no_original"


@pytest.mark.asyncio
async def test_present_row_reports_no_reason_and_returns_details():
    row = _details_row()
    resp = await _call_details("dji_txt", row, index_rows=[
        ("frame", "altitude_msl_m", "m", 13870, 1),
        ("frame", "t_offset_s", "s", 13870, 2),
    ])
    assert resp.unavailable_reason is None
    assert resp.details is not None
    assert resp.details["frame_count"] == 13870
    assert "flight_id" not in resp.details  # the join key is not payload
    assert [e.name for e in resp.series_index] == ["altitude_msl_m", "t_offset_s"]
    assert resp.series_index[0].unit == "m"
    assert resp.series_index[0].sample_count == 13870


@pytest.mark.asyncio
async def test_a_recovered_odl_row_renders_its_data():
    """A present row wins over the source rule — this is what makes an ODL
    re-import visible in the UI at all."""
    resp = await _call_details("opendronelog_import", _details_row())
    assert resp.unavailable_reason is None
    assert resp.details is not None


@pytest.mark.asyncio
async def test_missing_flight_is_the_only_404():
    db = _FakeSession([_Result(None)])
    with pytest.raises(HTTPException) as exc:
        await get_flight_details(flight_id=uuid.uuid4(), db=db, _user=object())
    assert exc.value.status_code == 404
    # It short-circuits: the details / index statements are never issued.
    assert db.executed == 1


@pytest.mark.asyncio
async def test_a_flight_with_no_details_is_never_a_404():
    """Regression guard for the obvious implementation shortcut. D3 requires
    the link on every flight; a 404 here would render an error state on every
    Litchi and manual flight in the library."""
    for source in ("dji_txt", "litchi_csv", "airdata_csv", "manual", "opendronelog_import"):
        resp = await _call_details(source, None)
        assert resp.unavailable_reason is not None
        assert resp.details is None


# ── the pure resolver, exhaustively ────────────────────────────────────


def test_reason_resolver_is_total():
    """Every source maps to exactly one outcome, including unknown ones."""
    assert details_unavailable_reason("dji_txt", True) is None
    assert details_unavailable_reason("dji_txt", False) == "not_backfilled"
    assert details_unavailable_reason("opendronelog_import", False) == "odl_import_no_original"
    assert details_unavailable_reason("opendronelog_import", True) is None
    for src in DETAILS_UNSUPPORTED_SOURCES:
        assert details_unavailable_reason(src, False) == "source_unsupported"
    # A source this build has never heard of, and a NULL source.
    assert details_unavailable_reason("some_future_format", False) == "source_unsupported"
    assert details_unavailable_reason(None, False) == "source_unsupported"


def test_payload_columns_track_the_model_and_exclude_the_join_key():
    """The payload is derived from the mapper, so a column added to the model
    is exposed without a second edit — and cannot be silently forgotten."""
    from app.models.flight_details import FlightDetails

    mapped = {c.key for c in FlightDetails.__mapper__.column_attrs}
    assert set(_DETAILS_PAYLOAD_COLUMNS) == mapped - {"flight_id"}
    # Spot-check the columns the page's sections actually need.
    for col in (
        "frame_count", "max_altitude_msl_m", "photo_count", "rc_downlink_avg",
        "battery_energy_wh", "pack_values_plausible", "events", "phases",
        "pilot_max_distance_m", "take_off_altitude_units",
    ):
        assert col in _DETAILS_PAYLOAD_COLUMNS


def test_datetimes_are_serialized_with_an_explicit_utc_offset():
    """Details timestamps are naive UTC in the DB. Emitting them bare is the
    ADR-0017 "flight shows next day" bug; the whole app uses ``iso_utc``."""
    from datetime import datetime

    payload = _serialize_details(
        _details_row(generated_at=datetime(2026, 9, 5, 3, 39, 0))
    )
    assert payload["generated_at"] == "2026-09-05T03:39:00+00:00"


# ── /details/series input validation ───────────────────────────────────


@pytest.mark.asyncio
async def test_series_rejects_an_empty_name_list():
    db = _FakeSession([_Result(uuid.uuid4())])
    with pytest.raises(HTTPException) as exc:
        await get_flight_details_series(
            flight_id=uuid.uuid4(), names=" , ,", source="frame",
            max_points=2000, db=db, _user=object(),
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_series_caps_the_number_of_requested_series():
    """Bounds the detoast set. Without a cap one request could name every
    series on the flight and pull the full ~1.3 MB the split table exists to
    avoid."""
    db = _FakeSession([_Result(uuid.uuid4())])
    with pytest.raises(HTTPException) as exc:
        await get_flight_details_series(
            flight_id=uuid.uuid4(), names=",".join(f"s{i}" for i in range(50)),
            source="frame", max_points=2000, db=db, _user=object(),
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_series_missing_flight_is_a_404():
    db = _FakeSession([_Result(None)])
    with pytest.raises(HTTPException) as exc:
        await get_flight_details_series(
            flight_id=uuid.uuid4(), names="altitude_msl_m", source="frame",
            max_points=2000, db=db, _user=object(),
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_series_always_returns_the_time_base_alongside():
    """The caller asks for one quantity; ``t_offset_s`` comes back too, at the
    same indices, or the values are uninterpretable."""
    rows = [
        ("altitude_msl_m", "m", [float(i) for i in range(100)]),
        ("t_offset_s", "s", [round(i / 10, 2) for i in range(100)]),
    ]
    db = _FakeSession([_Result(uuid.uuid4()), _Result(None, rows=rows)])
    resp = await get_flight_details_series(
        flight_id=uuid.uuid4(), names="altitude_msl_m", source="frame",
        max_points=10, db=db, _user=object(),
    )
    assert set(resp.series) == {"altitude_msl_m", "t_offset_s"}
    assert resp.sample_count == 100
    assert resp.returned_points == 10
    assert len(resp.series["altitude_msl_m"]) == 10
    # Same source group, same length → identical stride, so index i of one
    # series lines up with index i of the time base.
    assert resp.series["altitude_msl_m"] == [0.0, 11.0, 22.0, 33.0, 44.0, 55.0, 66.0, 77.0, 88.0, 99.0]
    assert resp.series["t_offset_s"] == [0.0, 1.1, 2.2, 3.3, 4.4, 5.5, 6.6, 7.7, 8.8, 9.9]
    assert resp.units == {"altitude_msl_m": "m", "t_offset_s": "s"}
    assert resp.missing == []


@pytest.mark.asyncio
async def test_series_reports_names_it_could_not_find():
    """A silently absent series would render an empty chart that looks like a
    flat signal. Name it instead."""
    rows = [("t_offset_s", "s", [0.0, 0.1, 0.2])]
    db = _FakeSession([_Result(uuid.uuid4()), _Result(None, rows=rows)])
    resp = await get_flight_details_series(
        flight_id=uuid.uuid4(), names="vps_height_m,altitude_msl_m",
        source="frame", max_points=2000, db=db, _user=object(),
    )
    assert resp.missing == ["altitude_msl_m", "vps_height_m"]
    assert resp.series == {"t_offset_s": [0.0, 0.1, 0.2]}
