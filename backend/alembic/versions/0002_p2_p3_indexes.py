"""ADR-0022 (FU-8 #6) — deferred P2/P3 hot-path indexes

Revision ID: 0002_p2_p3_indexes
Revises: 0001_baseline_schema
Create Date: 2026-06-11

The ground-up audit (P1-5) listed several FK / filter / sort columns beyond
the four P1-grade indexes that ADR-0021 shipped. This revision adds the
ones that survive verification against a REAL query pattern in the current
routers. Each is justified below; rejected candidates are documented in
ADR-0022 (not added here).

All use ``CREATE INDEX IF NOT EXISTS`` (idempotent) so this revision is safe
to (re-)apply on a brownfield DB that an operator may have hand-indexed, and
safe under the ADR-0021 recovery guard (DDL runs only on the writable
primary; indexes replicate to the CHAD-HQ standby via WAL). Plain
(non-CONCURRENTLY) ``CREATE INDEX`` for the same reason ADR-0021 chose it:
single-operator fleet, thousands of rows, sub-second builds; revisit at ~1M
rows per table (see ADR-0022 trade-off section).

Indexes added (verified evidence in parens):

  * ``flights.start_time``           — DEFAULT primary list sort key
    (``flight_library.py:539`` ``sort_col_map.get(sort_by, Flight.start_time)``)
    AND the date-range filter (``:571`` ``start_time >= dt_from``, ``:580``
    ``start_time <= dt_to``) AND the per-pilot flight sort
    (``pilots.py:168`` ``order_by(Flight.start_time.desc())``). The hottest
    unindexed sort/filter on the largest table.
  * ``flights.created_at``           — the UNIVERSAL list tiebreaker
    (``flight_library.py:554`` ``order_by(order, desc(Flight.created_at))``
    — appended to EVERY flight-library list sort) plus the recent-activity
    feeds (``:489`` ``created_at >= day_start``, ``:644`` ``order_by(
    desc(created_at)).limit(10)``). Every list query sorts on this.
  * ``missions.customer_id``         — FK; client-portal + dup-detect filter
    (``missions.py:184`` ``customer_id == data.customer_id``; mission→customer
    joins). Plain FK, not auto-indexed.
  * ``missions.status``              — business-signals dashboard filters
    (``business_signals.py:96,103,134,137`` ``status.in_(...)`` /
    ``status == ...`` on every signals poll). Low-cardinality but the
    dashboard scans the whole table per status bucket.
  * ``mission_images.mission_id``    — FK; ``Mission.images`` selectin on
    every mission detail/report path + the scalar count
    (``missions.py:649`` ``where(MissionImage.mission_id == mission_id)``).
    Plain FK.
  * ``maintenance_records.aircraft_id``   — FK; per-aircraft history filter
    (``maintenance.py:74`` ``where(MaintenanceRecord.aircraft_id == ...)``)
    + the latest-per-aircraft dashboard scan. Plain FK.
  * ``maintenance_schedules.aircraft_id`` — FK; per-aircraft schedule lookups
    (``maintenance.py:285,317,434`` ``where(MaintenanceSchedule.aircraft_id
    == ...)``). Plain FK.

REJECTED (documented in ADR-0022): ``missions.mission_date`` (only used in
ORDER BY on an already-``id IN (...)``-bounded client-portal set — the IN
prunes first, the sort is over a handful of rows), ``flights.pilot_id`` (the
pilot-flights list is already FK-bounded to one pilot and small; the sort
benefits from ``ix_flights_start_time``), ``flights.source`` /
``flights.drone_model`` (only ``ilike '%…%'`` search predicates — a B-tree
index does not serve leading-wildcard ILIKE; would need a trigram index, out
of scope), ``line_items`` / ``invoices`` extra columns (already covered by
ADR-0021 ``ix_line_items_invoice_id`` and the auto-created unique indexes on
``invoices.mission_id`` / ``reports.mission_id``).
"""
from __future__ import annotations

import logging
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002_p2_p3_indexes"
down_revision: Union[str, None] = "0001_baseline_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("doc.migrations")

# (index_name, table, column) — each verified against a real query pattern.
_INDEXES: tuple[tuple[str, str, str], ...] = (
    ("ix_flights_start_time", "flights", "start_time"),
    ("ix_flights_created_at", "flights", "created_at"),
    ("ix_missions_customer_id", "missions", "customer_id"),
    ("ix_missions_status", "missions", "status"),
    ("ix_mission_images_mission_id", "mission_images", "mission_id"),
    ("ix_maintenance_records_aircraft_id", "maintenance_records", "aircraft_id"),
    ("ix_maintenance_schedules_aircraft_id", "maintenance_schedules", "aircraft_id"),
)


def upgrade() -> None:
    for index_name, table, column in _INDEXES:
        op.execute(
            f"CREATE INDEX IF NOT EXISTS {index_name} ON {table} ({column})"
        )
        logger.info("MIGRATIONS: ensured index %s on %s(%s)", index_name, table, column)


def downgrade() -> None:
    for index_name, _table, _column in _INDEXES:
        op.execute(f"DROP INDEX IF EXISTS {index_name}")
