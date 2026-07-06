"""ADR-0040 — dedup stamp for payment-triggered download-link delivery email

Revision ID: 0009_mission_dl_email_sent_at
Revises: 0008_report_dl_payment_override
Create Date: 2026-07-06

Adds ``missions.download_link_email_sent_at`` (timestamp, nullable). Stamped
when the automated "your download link is ready" email fires after the
invoice is paid in full; NULL means not yet delivered. The mission-update
path resets it to NULL when ``download_link_url`` changes, re-arming
delivery for a replacement link.

Additive nullable column — no backfill, no data rewrite. Replication-safe
(plain DDL via WAL). Revision id is 29 chars (<= 32).
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009_mission_dl_email_sent_at"
down_revision: Union[str, None] = "0008_report_dl_payment_override"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "missions",
        sa.Column("download_link_email_sent_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("missions", "download_link_email_sent_at")
