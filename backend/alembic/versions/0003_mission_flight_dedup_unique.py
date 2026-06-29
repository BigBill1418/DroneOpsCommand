"""ADR-0026 — de-duplicate mission_flights + partial UNIQUE guards

Revision ID: 0003_mission_flight_dedup_unique
Revises: 0002_p2_p3_indexes
Create Date: 2026-06-29

Root cause (ADR-0026): the single-attach path (``POST
/api/missions/{id}/flights``, ``missions.add_flight``) had NO dedup and there
was NO database constraint on ``mission_flights (mission_id, flight_id)``. A
retry storm during the 2026-06 OOM window (attach requests timing out / 500ing
while the operator/frontend re-clicked) inserted the same flight many times.
The "Savannah Bananas Games" mission ended up with 50 rows for 27 unique
flights (one flight ×13). Because report totals sum over ALL rows, total flight
time, distance and flight count inflated by 100-160%.

This migration makes that class of corruption impossible at the storage layer:

  1. **Dedup first** — for both the native key ``(mission_id, flight_id)`` and
     the legacy ODL key ``(mission_id, opendronelog_flight_id)``, keep exactly
     ONE row per group (the earliest ``added_at``, ``id`` as a deterministic
     tiebreaker) and delete the rest. Required because a UNIQUE index cannot be
     created while duplicates exist. Idempotent: on an already-clean DB this
     deletes zero rows. (The live BOS-HQ prod DB was deduped operationally
     before this revision shipped, so here it is a verified no-op.)

  2. **Constrain** — two PARTIAL UNIQUE indexes:
       * ``uq_mission_flights_mission_flight``  UNIQUE (mission_id, flight_id)
         WHERE flight_id IS NOT NULL
       * ``uq_mission_flights_mission_odl``     UNIQUE (mission_id,
         opendronelog_flight_id) WHERE opendronelog_flight_id IS NOT NULL
     Partial so the two attach modes (native ``flight_id`` vs legacy
     ``opendronelog_flight_id``) are each guarded without the NULL of one mode
     colliding with the NULL of another (NULLs are distinct under a plain unique
     index anyway, but the partial predicate documents intent and keeps the
     index lean — only real keys are indexed).

Idempotency / brownfield safety: both indexes use ``CREATE UNIQUE INDEX IF NOT
EXISTS`` so re-application is safe. Resilience (CLAUDE.md guard): the
programmatic runner executes migrations ONLY on the writable primary
(``pg_is_in_recovery()`` guard, ADR-0021); the new indexes replicate to the
CHAD-HQ / BOS standby via WAL. Plain (non-CONCURRENTLY) ``CREATE INDEX`` for the
same reason ADR-0021/0022 chose it: single-operator fleet, thousands of rows,
sub-second build — revisit at ~1M rows. Does NOT touch port bindings,
connection strings, pg_hba or the blue-green swap flow.
"""
from __future__ import annotations

import logging
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003_mission_flight_dedup_unique"
down_revision: Union[str, None] = "0002_p2_p3_indexes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("doc.migrations")

# Dedup: keep the earliest ``added_at`` (``id`` tiebreaker) per key group,
# delete every later duplicate. One statement per key (native + legacy ODL).
_DEDUP_NATIVE = """
DELETE FROM mission_flights mf
WHERE mf.flight_id IS NOT NULL
  AND EXISTS (
    SELECT 1 FROM mission_flights k
    WHERE k.mission_id = mf.mission_id
      AND k.flight_id = mf.flight_id
      AND (k.added_at < mf.added_at OR (k.added_at = mf.added_at AND k.id < mf.id))
  )
"""

_DEDUP_ODL = """
DELETE FROM mission_flights mf
WHERE mf.opendronelog_flight_id IS NOT NULL
  AND EXISTS (
    SELECT 1 FROM mission_flights k
    WHERE k.mission_id = mf.mission_id
      AND k.opendronelog_flight_id = mf.opendronelog_flight_id
      AND (k.added_at < mf.added_at OR (k.added_at = mf.added_at AND k.id < mf.id))
  )
"""

_INDEXES: tuple[tuple[str, str], ...] = (
    (
        "uq_mission_flights_mission_flight",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_mission_flights_mission_flight "
        "ON mission_flights (mission_id, flight_id) WHERE flight_id IS NOT NULL",
    ),
    (
        "uq_mission_flights_mission_odl",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_mission_flights_mission_odl "
        "ON mission_flights (mission_id, opendronelog_flight_id) "
        "WHERE opendronelog_flight_id IS NOT NULL",
    ),
)


def upgrade() -> None:
    res = op.get_bind().exec_driver_sql(_DEDUP_NATIVE)
    logger.info("MIGRATIONS: deduped %s native (mission_id,flight_id) duplicate rows", res.rowcount)
    res = op.get_bind().exec_driver_sql(_DEDUP_ODL)
    logger.info("MIGRATIONS: deduped %s legacy ODL duplicate rows", res.rowcount)
    for index_name, ddl in _INDEXES:
        op.execute(ddl)
        logger.info("MIGRATIONS: ensured partial unique index %s on mission_flights", index_name)


def downgrade() -> None:
    # Deleted duplicate rows are not restored (they were exact-duplicate
    # attachments with no independent value); only the guards are dropped.
    for index_name, _ddl in _INDEXES:
        op.execute(f"DROP INDEX IF EXISTS {index_name}")
        logger.info("MIGRATIONS: dropped partial unique index %s", index_name)
