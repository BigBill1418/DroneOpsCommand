"""ADR-0028 C1 — repair physically-impossible OpenDroneLog distances

Revision ID: 0006_repair_odl_distance
Revises: 0005_flight_hash_unique_index
Create Date: 2026-06-29

OpenDroneLog imports passed ``totalDistance`` through verbatim. One row
(``f57c9373``) reports ``12,583,855 m`` over 554 s — an implied 22,722 m/s
(orbital) versus a recorded 29.1 m/s max. Its own stored GPS track, summed with
the C1 outlier gate, is ``3,604 m`` (6.5 m/s average — physically consistent).
Every mission report that sums per-flight distance is poisoned by such a row.

This migration recomputes/clamps the distance for every ``opendronelog_import``
flight whose reported distance is physically impossible (implied average speed
> 3× the recorded max speed, or > 60 m/s when no max speed is recorded), using
the SAME gate as the Rust parser and the live ingest clamp
(``app.services.flight_metrics.sanitize_odl_distance``):

  * recompute from the stored ``gps_track`` with the per-segment outlier gate
    when that yields a plausible value (``method='track_recompute'``), else
  * clamp to ``max_speed × duration`` (``method='speed_clamp'``).

The original distance + method are recorded under
``raw_metadata.distance_sanitized`` — an audit trail AND the idempotency key
(rows already carrying it are skipped, so a re-run changes zero rows).

A pre-flight audit (2026-06-29) found exactly ONE affected row fleet-wide; the
original row was also backed up out-of-band before deploy. Replication-safe
(pure row data via WAL); revision id is 24 chars (≤ 32). Not reversed on
downgrade — the prior value was known-corrupt and has no independent meaning.
"""
from __future__ import annotations

import json
import logging
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006_repair_odl_distance"
down_revision: Union[str, None] = "0005_flight_hash_unique_index"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("doc.migrations")

# Implausible + not-yet-repaired ODL rows. An average can never exceed the max,
# so total_distance/duration > 3× max_speed is physically impossible; when
# max_speed is absent fall back to the absolute 60 m/s cap.
_SELECT = sa.text(
    """
    SELECT id, duration_secs, total_distance, max_speed, gps_track
    FROM flights
    WHERE source = 'opendronelog_import'
      AND duration_secs > 0 AND total_distance > 0
      AND (
            (max_speed > 0 AND total_distance / duration_secs > 3 * max_speed)
         OR (max_speed <= 0 AND total_distance / duration_secs > 60)
      )
      AND (
            raw_metadata IS NULL
         OR (raw_metadata::jsonb -> 'distance_sanitized') IS NULL
      )
    """
)

_UPDATE = sa.text(
    """
    UPDATE flights
    SET total_distance = :dist,
        raw_metadata = jsonb_set(
            COALESCE(raw_metadata::jsonb, '{}'::jsonb),
            '{distance_sanitized}',
            (:note)::jsonb
        )::json
    WHERE id = :id
    """
)


def upgrade() -> None:
    # Imported here (not at module top) so the hermetic migration-tree walk
    # never needs the app service layer on the path.
    from app.services.flight_metrics import sanitize_odl_distance

    bind = op.get_bind()
    rows = bind.execute(_SELECT).fetchall()
    repaired = 0
    for row in rows:
        track = row.gps_track
        if isinstance(track, str):
            try:
                track = json.loads(track)
            except (ValueError, TypeError):
                track = None
        dist, note = sanitize_odl_distance(
            float(row.total_distance or 0.0),
            float(row.duration_secs or 0.0),
            float(row.max_speed or 0.0),
            track,
        )
        if note is None:
            # Defensive: SELECT already filtered to implausible rows.
            continue
        bind.execute(
            _UPDATE,
            {"dist": dist, "note": json.dumps(note), "id": row.id},
        )
        logger.info(
            "MIGRATIONS: ADR-0028 C1 repaired flight %s distance %.0f→%.1f m via %s",
            row.id, note["original_distance_m"], dist, note["method"],
        )
        repaired += 1

    logger.info("MIGRATIONS: ADR-0028 C1 repaired %d implausible ODL distance(s)", repaired)


def downgrade() -> None:
    # The prior distances were known-corrupt ODL passthroughs with no
    # independent meaning; they are not restored. The audit note remains.
    logger.info("MIGRATIONS: ADR-0028 C1 distance repair is not reversed (corrupt source)")
