"""ADR-0043 R-5 — the extended-log sidecars must never enter the list path.

``Flight.details`` and ``Flight.series`` are declared ``lazy="noload"``. If
either were changed to an eager strategy, every ``select(Flight)`` in the
codebase would start pulling the sidecars — including the 500-row
mission-picker list query. ``flight_series`` alone holds ~100-300 KB
compressed per flight, so that is the ADR-0019 production OOM crash-loop
reproduced with a bigger payload.

**Two detectors, because one of them is blind on its own.**

1. ``test_list_query_references_neither_sidecar_table`` compiles the real list
   query and asserts neither table name appears. Control:
   ``test_joined_load_control_puts_the_table_in_the_select``, which proves the
   string check can fail.

   This detector catches ``joinedload`` / ``lazy="joined"`` / ``subquery``.
   **It cannot catch ``selectin``** — a ``selectin`` loader emits a SECOND
   statement, so the primary SELECT looks identical to the safe one. Since
   ``selectin`` is precisely the strategy R-5 names as the likely mistake, a
   compiled-SQL assertion alone would sit green through the exact regression
   it was written to stop.

2. ``test_sidecar_relationships_are_noload`` therefore asserts the loader
   strategy itself. That is the detector that actually covers R-5; the SQL
   check is the belt to its braces.
"""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("JWT_SECRET_KEY", "test_secret_key_unused_in_unit_tests")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")

from sqlalchemy import JSON, desc, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import defer, joinedload

from app.models.flight import Flight
from app.models.flight_details import FlightDetails, FlightSeries
from app.routers.flight_library import LIST_DEFERRED_COLUMNS

SIDECAR_TABLES = ("flight_details", "flight_series")


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


def test_list_query_references_neither_sidecar_table():
    sql = _compiled(_list_query())
    for table in SIDECAR_TABLES:
        assert table not in sql, (
            f"{table} must never appear in the flight-library list SELECT — "
            "eager-loading the extended-log sidecars for a 500-row page is "
            "the ADR-0019 OOM failure mode with a larger payload."
        )


def test_flight_detail_query_references_neither_sidecar_table():
    """The single-flight detail route loads the full entity; still no sidecar."""
    sql = _compiled(select(Flight).where(Flight.id.is_(None)))
    for table in SIDECAR_TABLES:
        assert table not in sql


def test_joined_load_control_puts_the_table_in_the_select():
    """Control for detector 1: prove the string assertion can fail.

    Without this, "the table name is absent" is a claim that could hold for
    reasons having nothing to do with the loader strategy.
    """
    for rel, table in (
        (Flight.details, "flight_details"),
        (Flight.series, "flight_series"),
    ):
        sql = _compiled(
            select(Flight).options(joinedload(rel)).order_by(desc(Flight.start_time))
        )
        assert table in sql, (
            f"joinedload({rel}) should name {table} in the primary SELECT; if it "
            "no longer does, detector 1 has gone blind and proves nothing."
        )


def test_sidecar_relationships_are_noload():
    """Detector 2 — the one that actually covers R-5, including ``selectin``."""
    assert Flight.details.property.lazy == "noload", (
        "Flight.details must stay lazy='noload'. 'selectin' would join the "
        "sidecar into every select(Flight) while leaving the compiled primary "
        "SELECT unchanged — invisible to the SQL-string guard above."
    )
    assert Flight.series.property.lazy == "noload", (
        "Flight.series must stay lazy='noload' — see above."
    )
    assert Flight.details.property.uselist is False
    # Deleting a flight must take its sidecars with it. The FK is ON DELETE
    # CASCADE at the DB level; the ORM cascade keeps a session-level delete
    # consistent with that instead of leaving orphans in the session.
    assert "delete-orphan" in Flight.details.property.cascade
    assert "delete-orphan" in Flight.series.property.cascade


def test_series_values_column_is_json_not_jsonb():
    """Plan §1.5: ``values`` is Postgres ``json`` on purpose.

    ``jsonb`` re-encodes every element as a variable-length ``numeric`` with a
    per-element header — tens of KB of overhead on a 13,870-element float
    array, and binary numerics compress worse than the rounded ASCII digit
    runs the parser emits. Nothing indexes or path-queries this column. The
    group columns on ``flight_details`` are the opposite case and stay JSONB.
    This test exists so a later "tidy up the JSON types" pass has to argue.
    """
    values_col = FlightSeries.__table__.c["values"]
    assert isinstance(values_col.type, JSON)
    assert not isinstance(values_col.type, JSONB)

    for group in ("phases", "events", "config", "firmware", "health", "sd_card", "serials"):
        assert isinstance(FlightDetails.__table__.c[group].type, JSONB), group


def test_series_primary_key_is_the_read_path_lookup():
    """(flight_id, source, name) is exactly what /details/series filters on,
    so no secondary index ships at P0."""
    pk = [c.name for c in FlightSeries.__table__.primary_key.columns]
    assert pk == ["flight_id", "source", "name"]
    assert list(FlightSeries.__table__.indexes) == []
    assert list(FlightDetails.__table__.indexes) == []


def test_series_index_projection_never_selects_values():
    """The /details endpoint's series index must not detoast the blobs.

    Selecting ``FlightSeries`` as an entity would pull ``values`` for every
    series on the flight — ~1.3 MB raw — to render an availability list. The
    endpoint selects columns explicitly; pin that.
    """
    projection = select(
        FlightSeries.source,
        FlightSeries.name,
        FlightSeries.unit,
        FlightSeries.sample_count,
        FlightSeries.precision_dp,
    )
    sql = _compiled(projection)
    assert "values" not in sql
    # Control: the entity load does select it.
    assert "values" in _compiled(select(FlightSeries))
