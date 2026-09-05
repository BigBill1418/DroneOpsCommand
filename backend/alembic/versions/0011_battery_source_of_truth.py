"""ADR-0043 D4 — battery source-of-truth columns (landed early, inert)

Revision ID: 0011_battery_src_truth
Revises: 0010_flight_details
Create Date: 2026-09-05

Three additive nullable columns (plan §1.6):

* ``batteries.cycle_count_observed`` — preserves the existing per-import
  increment history once ``cycle_count`` starts showing the pack's own
  lifetime count.
* ``batteries.metrics_source`` — ``'observed'`` | ``'pack'``, so a displayed
  value is never ambiguous about where it came from.
* ``battery_logs.pack_cycle_count`` — the pack's own reported count at that
  flight. A NEW column on purpose: ``cycles_at_time`` keeps its existing
  meaning, because silently redefining it mid-history would corrupt the
  series it already holds.

**Landed here, one phase early, deliberately.** The phase that actually
switches the battery semantics then needs no migration of its own, which
keeps a schema change off the critical path of a behavioural change. Nothing
reads or writes these columns yet — this revision is INERT.

Additive nullable DDL: no backfill, no data rewrite, replication-safe over
WAL, survives container recreation and standby promotion, no port /
``pg_hba`` / connection-string change.

IDEMPOTENT (ADR-0042): ``0001`` builds fresh databases with ``create_all``
from the LIVE models, which already carry these columns — so on a fresh DB
each ``add_column`` must no-op instead of raising DuplicateColumn.

Revision id is 21 chars (<= 32).
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0011_battery_src_truth"
down_revision: Union[str, None] = "0010_flight_details"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)

    battery_cols = {c["name"] for c in insp.get_columns("batteries")}
    if "cycle_count_observed" not in battery_cols:
        op.add_column(
            "batteries", sa.Column("cycle_count_observed", sa.Integer(), nullable=True)
        )
    if "metrics_source" not in battery_cols:
        op.add_column(
            "batteries", sa.Column("metrics_source", sa.String(length=16), nullable=True)
        )

    log_cols = {c["name"] for c in insp.get_columns("battery_logs")}
    if "pack_cycle_count" not in log_cols:
        op.add_column(
            "battery_logs", sa.Column("pack_cycle_count", sa.Integer(), nullable=True)
        )


def downgrade() -> None:
    op.drop_column("battery_logs", "pack_cycle_count")
    op.drop_column("batteries", "metrics_source")
    op.drop_column("batteries", "cycle_count_observed")
