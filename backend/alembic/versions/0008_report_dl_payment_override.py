"""ADR-0039 — per-report override for the unpaid-invoice download-link gate

Revision ID: 0008_report_dl_payment_override
Revises: 0007_strip_legacy_cache_track
Create Date: 2026-07-05

Adds ``reports.download_link_payment_override`` (bool, NOT NULL, default
false). ADR-0039 withholds the mission-footage download link from the report
PDF and email while the mission's invoice is not paid in full; this column is
the deliberate per-report operator override that releases the link early.

Default false = gate enforced for every existing and new report. Additive
column with a server default — no backfill, no data rewrite. Replication-safe
(plain DDL via WAL). Revision id is 31 chars (<= 32).
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008_report_dl_payment_override"
down_revision: Union[str, None] = "0007_strip_legacy_cache_track"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "reports",
        sa.Column(
            "download_link_payment_override",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("reports", "download_link_payment_override")
