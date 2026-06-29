"""ADR-0028 M1 — strip heavy track/telemetry keys from native flight caches

Revision ID: 0007_strip_legacy_cache_track
Revises: 0006_repair_odl_distance
Create Date: 2026-06-29

ADR-0025 stopped duplicating the GPS track into ``mission_flights.flight_data_cache``
for native flights (the track lives once on ``Flight.gps_track``, loaded on
demand). But legacy native caches written BEFORE that change still embed the
full track + telemetry — up to ~0.95 MB per row in the live data — which the
map/report on-demand loader reads back, reintroducing the ADR-0019/0025 OOM
risk on a large mission.

This strips the heavy keys (``track``, ``gps_data``, ``coordinates``,
``telemetry``) from caches of NATIVE rows only (``flight_id IS NOT NULL``).
Those rows can always re-resolve the authoritative track from
``Flight.gps_track``, so nothing is lost. Legacy-ODL rows (``flight_id IS NULL``)
are LEFT UNTOUCHED — their cached track is the only copy.

Key NAMING normalization (the dual snake_case/camelCase keys) is deliberately
NOT done here: the cache readers already accept both casings (``_cache_num`` and
the report layer's live-scalar resolution), so a rename is churn with downside
risk and no correctness gain.

Idempotent: guarded to touch only rows that actually still carry a heavy key.
Replication-safe (row data via WAL). Revision id is 29 chars (≤ 32).
"""
from __future__ import annotations

import logging
from typing import Sequence, Union

from alembic import op

revision: str = "0007_strip_legacy_cache_track"
down_revision: Union[str, None] = "0006_repair_odl_distance"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("doc.migrations")

# Strip heavy keys from native-flight caches only (flight_id IS NOT NULL), and
# only where at least one heavy key is actually present (so a re-run is a no-op).
_STRIP = """
UPDATE mission_flights
SET flight_data_cache = (
        (flight_data_cache::jsonb) - 'track' - 'gps_data' - 'coordinates' - 'telemetry'
    )::json
WHERE flight_id IS NOT NULL
  AND flight_data_cache IS NOT NULL
  AND (flight_data_cache::jsonb) ?| array['track','gps_data','coordinates','telemetry']
"""


def upgrade() -> None:
    bind = op.get_bind()
    res = bind.exec_driver_sql(_STRIP)
    logger.info(
        "MIGRATIONS: ADR-0028 M1 stripped heavy keys from %s native flight cache(s)",
        res.rowcount,
    )


def downgrade() -> None:
    # The stripped tracks remain authoritatively on Flight.gps_track; there is
    # nothing to restore into the cache.
    logger.info("MIGRATIONS: ADR-0028 M1 cache strip is not reversed (track lives on Flight)")
