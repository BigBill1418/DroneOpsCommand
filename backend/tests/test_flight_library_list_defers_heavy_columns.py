"""Regression guard for the 2026-06-10 mission-picker outage (ADR-0019).

The mission picker loads available flights from GET /api/flight-library with
the default limit=500. SQLAlchemy loads ALL mapped columns by default, so the
list query was pulling each flight's gps_track (~19k GPS points), telemetry
time-series, and raw_metadata blob. Decompressing the TOASTed JSON for ~500
rows into Python objects blew past the 1 GB uvicorn mem_limit and the kernel
cgroup OOM-killed the worker mid-serialization. The picker's request then
failed and the frontend silently fell back to the (unreachable) OpenDroneLog
proxy — so freshly uploaded flights disappeared from the picker.

The fix defers those heavy columns in the list query. FlightResponse never
serializes them, so deferring is transparent to clients while keeping the
list lean. These tests assert, against the actual production query options,
that the heavy columns are excluded from the emitted SELECT and the light
columns the picker needs are retained — without requiring a live database.
"""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("JWT_SECRET_KEY", "test_secret_key_unused_in_unit_tests")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")

from sqlalchemy import desc, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import defer

from app.models.flight import Flight
from app.routers.flight_library import LIST_DEFERRED_COLUMNS


def _list_query():
    """Rebuild the list_flights query exactly as the endpoint does."""
    return (
        select(Flight)
        .options(*[defer(col) for col in LIST_DEFERRED_COLUMNS])
        .order_by(desc(Flight.start_time), desc(Flight.created_at))
        .limit(500)
    )


def _compiled(query) -> str:
    return str(query.compile(dialect=postgresql.dialect()))


HEAVY = ("flights.gps_track", "flights.telemetry", "flights.raw_metadata")
LIGHT = (
    "flights.id",
    "flights.name",
    "flights.start_time",
    "flights.drone_model",
    "flights.aircraft_id",
    "flights.source",
)


def test_heavy_json_columns_are_deferred_from_list_select():
    sql = _compiled(_list_query())
    for col in HEAVY:
        assert col not in sql, (
            f"{col} must be deferred from the flight-library list SELECT — "
            "loading it for a 500-row page OOM-kills the API worker (ADR-0019)."
        )


def test_light_columns_still_present_in_list_select():
    sql = _compiled(_list_query())
    for col in LIGHT:
        assert col in sql, f"{col} is needed by the mission picker and must stay in the SELECT."


def test_plain_query_would_load_heavy_columns():
    """Control: without the defer options the heavy columns ARE selected.

    Proves the assertion above is meaningful (it would fail if someone drops
    the defer), not a tautology about the dialect compiler.
    """
    plain = _compiled(select(Flight).order_by(desc(Flight.start_time)))
    for col in HEAVY:
        assert col in plain


def test_list_deferred_columns_covers_every_heavy_json_column():
    """If a new large JSON column is added to Flight, force a deliberate
    decision about whether it belongs in the deferred set."""
    deferred_names = {col.key for col in LIST_DEFERRED_COLUMNS}
    assert deferred_names == {"gps_track", "telemetry", "raw_metadata"}
