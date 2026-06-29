"""ADR-0027 — re-stamp DJI durations to authoritative header airtime + fix auto flight names

Revision ID: 0004_dji_duration_and_flight_name_restamp
Revises: 0003_mission_flight_dedup_unique
Create Date: 2026-06-29

Two data corrections + one storage guard, all idempotent and safe on the live
primary (ADR-0027). None of this touches port bindings, connection strings,
pg_hba, replication or the blue-green swap flow — it is pure row data + one
partial index, all of which replicate to the standbys via WAL.

ROOT CAUSE #1 — inflated/undercounted durations. The Rust DJI parser
(``flight-parser/src/dji.rs``) discarded DJI's authoritative airtime
(``details.total_time``, persisted in ``flights.raw_metadata->>'header_duration'``)
and estimated ``frames.len() / 10.0`` — a hard-coded 10 Hz divisor. Mavic 4 Pro
logs at 15 Hz → durations inflated exactly 1.5×; DJI FPV logs ~5 Hz → halved.
Other models drift a few % when their cadence isn't exactly 10 Hz or frames
dropped. The header is authoritative, so:

  1. **flights re-stamp** — for every ``source='dji_txt'`` flight with a valid
     header_duration whose stored ``duration_secs`` diverges > 1 %, set
     ``duration_secs = header_duration``.
  2. **report-cache re-stamp** — the mission report sums
     ``mission_flights.flight_data_cache->>'duration_secs'`` (a snapshot taken at
     attach time), NOT live ``flights.duration_secs``. Re-stamp that cached key
     to the same authoritative header value so regenerated reports read correct
     airtime. (``flight_data_cache`` is a ``json`` column → jsonb_set round-trip.)

ROOT CAUSE #2 — duplicate / garbage-ordered auto names. ``_generate_flight_name``
counted by ``created_at`` inside a ``start_time``-day window, fleet-wide, not
per-aircraft, not start-ordered → collisions on ``_0001`` and descending junk
sequences. The app fix recomputes the sequence as a per-(label, operator-local
day) start-time rank; here we restamp existing auto-pattern names the same way:

  3. **name recompute** — for every flight whose name matches the machine
     pattern ``<label>_YYYYMMDD_NNNN``, regenerate ``NNNN`` as the start_time
     rank within its ``(label, operator-local-day)`` group. Operator-typed names
     (e.g. "Batt Maint") don't match the pattern and are left untouched. The
     date token is recomputed in the operator-local timezone (ADR-0017) — for
     correctly-dated rows this is a no-op on the date and only fixes the
     sequence. Grouping is by the *label* (the model token that appears in the
     name) because that is exactly what must be unique on the wire; when one
     model maps to one airframe this equals the per-airframe sequence.
  4. **storage guard** — partial UNIQUE index ``uq_flights_autoname`` on
     ``name`` WHERE the name matches the machine pattern, so a duplicate
     auto-generated name can never persist again. Operator free-text names are
     outside the predicate and may still repeat.

The operator-local timezone is fixed to ``America/Los_Angeles`` here (the
``settings.operator_timezone`` default, ADR-0017) to keep the migration
self-contained; change it alongside the app setting if the fleet ever moves.

Idempotency: every UPDATE is guarded so a re-run changes zero rows, and the
index uses ``IF NOT EXISTS``. The programmatic runner applies migrations only on
the writable primary (``pg_is_in_recovery()`` guard, ADR-0021).
"""
from __future__ import annotations

import logging
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004_dji_duration_and_flight_name_restamp"
down_revision: Union[str, None] = "0003_mission_flight_dedup_unique"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("doc.migrations")

# Operator-local timezone for the calendar-day reduction (ADR-0017 default).
_TZ = "America/Los_Angeles"

# Relative tolerance: re-stamp only durations that diverge from the
# authoritative header by more than this fraction (avoids churning rows whose
# frames-based estimate already matched, e.g. true 10 Hz airframes).
_TOL = 0.01

# 1) flights.duration_secs ← authoritative header airtime.
_RESTAMP_FLIGHTS = f"""
UPDATE flights f
SET duration_secs = (f.raw_metadata->>'header_duration')::float
WHERE f.source = 'dji_txt'
  AND f.raw_metadata->>'header_duration' IS NOT NULL
  AND (f.raw_metadata->>'header_duration')::float > 0
  AND abs(f.duration_secs - (f.raw_metadata->>'header_duration')::float)
        > {_TOL} * (f.raw_metadata->>'header_duration')::float
"""

# 2) mission_flights.flight_data_cache.duration_secs ← same authoritative value.
#    json column → cast to jsonb for jsonb_set, cast back to json. Sets the
#    snake_case key the report reads first, so a camelCase-only cache is also
#    corrected. Guarded on the cached value (or its absence) actually diverging.
_RESTAMP_CACHE = f"""
UPDATE mission_flights mf
SET flight_data_cache = jsonb_set(
        COALESCE(mf.flight_data_cache::jsonb, '{{}}'::jsonb),
        '{{duration_secs}}',
        to_jsonb((f.raw_metadata->>'header_duration')::float)
    )::json
FROM flights f
WHERE mf.flight_id = f.id
  AND f.source = 'dji_txt'
  AND f.raw_metadata->>'header_duration' IS NOT NULL
  AND (f.raw_metadata->>'header_duration')::float > 0
  AND (
        (mf.flight_data_cache->>'duration_secs') IS NULL
        OR abs((mf.flight_data_cache->>'duration_secs')::float
               - (f.raw_metadata->>'header_duration')::float)
             > {_TOL} * (f.raw_metadata->>'header_duration')::float
      )
"""

# 3) Recompute auto-pattern names: <label>_<localday>_<rank>, rank = start_time
#    order within (label, operator-local day). Idempotent via the trailing
#    inequality (only rows whose name actually changes are written).
_RECOMPUTE_NAMES = f"""
WITH ranked AS (
    SELECT
        f.id,
        substring(f.name from '^(.+)_\\d{{8}}_\\d{{4}}$') AS label,
        to_char(
            (f.start_time AT TIME ZONE 'UTC' AT TIME ZONE '{_TZ}'),
            'YYYYMMDD'
        ) AS lday,
        row_number() OVER (
            PARTITION BY
                substring(f.name from '^(.+)_\\d{{8}}_\\d{{4}}$'),
                (f.start_time AT TIME ZONE 'UTC' AT TIME ZONE '{_TZ}')::date
            ORDER BY f.start_time, f.id
        ) AS rn
    FROM flights f
    WHERE f.name ~ '^.+_\\d{{8}}_\\d{{4}}$'
      AND f.start_time IS NOT NULL
)
UPDATE flights f
SET name = ranked.label || '_' || ranked.lday || '_' || lpad(ranked.rn::text, 4, '0')
FROM ranked
WHERE f.id = ranked.id
  AND f.name <> ranked.label || '_' || ranked.lday || '_' || lpad(ranked.rn::text, 4, '0')
"""

# 4) Partial unique index on machine-generated names only.
_AUTONAME_INDEX = (
    "uq_flights_autoname",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_flights_autoname "
    "ON flights (name) WHERE name ~ '^.+_\\d{8}_\\d{4}$'",
)


def upgrade() -> None:
    bind = op.get_bind()

    res = bind.exec_driver_sql(_RESTAMP_FLIGHTS)
    logger.info("MIGRATIONS: ADR-0027 re-stamped %s flights.duration_secs to header airtime", res.rowcount)

    res = bind.exec_driver_sql(_RESTAMP_CACHE)
    logger.info("MIGRATIONS: ADR-0027 re-stamped %s mission_flights cache durations", res.rowcount)

    res = bind.exec_driver_sql(_RECOMPUTE_NAMES)
    logger.info("MIGRATIONS: ADR-0027 recomputed %s auto-generated flight names", res.rowcount)

    op.execute(_AUTONAME_INDEX[1])
    logger.info("MIGRATIONS: ADR-0027 ensured partial unique index %s", _AUTONAME_INDEX[0])


def downgrade() -> None:
    # The data re-stamps are not reversed — the prior values were derived from a
    # known-buggy estimator and have no independent meaning. Only the new storage
    # guard is dropped.
    op.execute(f"DROP INDEX IF EXISTS {_AUTONAME_INDEX[0]}")
    logger.info("MIGRATIONS: dropped partial unique index %s", _AUTONAME_INDEX[0])
