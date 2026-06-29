"""ADR-0028 H4 — partial unique index on flights.source_file_hash

Revision ID: 0005_flight_hash_unique_index
Revises: 0004_dji_duration_name_restamp
Create Date: 2026-06-29

The flight dedup is read-then-insert (``SELECT ... WHERE source_file_hash = ?``
then ``INSERT``) with no DB constraint — a classic TOCTOU race: two concurrent
uploads of the same file both miss the SELECT and both insert. This adds the
missing guarantee: a PARTIAL UNIQUE index on ``source_file_hash`` (partial so the
many NULL/legacy hashes are unaffected — Postgres treats NULLs as distinct
anyway, but the predicate also keeps the index small and intent explicit).

Safe on the live primary: a pre-flight audit (2026-06-29) confirmed 737/737
flights carry a DISTINCT non-null ``source_file_hash`` — zero duplicates — so the
unique index builds without conflict. The parser emits exactly one flight per
file today, so ``source_file_hash`` is per-flight; multi-flight-per-file support
(M3) would key dedup on ``sha256(file)+index`` and remains compatible with this
index. The app-side builder (``_build_flight_from_parsed``) now treats a
violation of this index as a clean "duplicate skipped" rather than a 500.

Replication-safe: a unique index replicates via WAL; no port/credential/pg_hba
change. Idempotent via ``IF NOT EXISTS``.
"""
from __future__ import annotations

import logging
from typing import Sequence, Union

from alembic import op

revision: str = "0005_flight_hash_unique_index"
down_revision: Union[str, None] = "0004_dji_duration_name_restamp"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("doc.migrations")

_INDEX = "uq_flights_source_file_hash"


def upgrade() -> None:
    op.execute(
        f"CREATE UNIQUE INDEX IF NOT EXISTS {_INDEX} "
        "ON flights (source_file_hash) WHERE source_file_hash IS NOT NULL"
    )
    logger.info("MIGRATIONS: ADR-0028 ensured partial unique index %s", _INDEX)


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {_INDEX}")
    logger.info("MIGRATIONS: dropped partial unique index %s", _INDEX)
