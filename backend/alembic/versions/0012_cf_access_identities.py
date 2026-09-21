"""ADR-0047 — Cloudflare Access identity -> local users.id mapping table

Revision ID: 0012_cf_access_ident
Revises: 0011_battery_src_truth
Create Date: 2026-09-21

Adds ``cf_access_identities`` (email PK, user_id FK unique -> users.id).
Populated only by ``app.auth.jwt.resolve_cf_access_user`` on the operator's
first Cloudflare Access login — never a data migration, no backfill.

Additive table only — no existing table is altered. Replication-safe
(plain DDL via WAL). Revision id is 20 chars (<= 32).

IDEMPOTENT (matches 0009/0010/0011's convention): 0001 builds fresh
databases with ``create_all`` from the LIVE models, which already include
this table (``app/models/__init__.py`` registers it) — so on a fresh DB
this revision must no-op instead of raising DuplicateTable.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012_cf_access_ident"
down_revision: Union[str, None] = "0011_battery_src_truth"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "cf_access_identities"


def upgrade() -> None:
    conn = op.get_bind()
    if _TABLE in sa.inspect(conn).get_table_names():
        return  # fresh DB: 0001's live-models create_all already built it

    op.create_table(
        _TABLE,
        sa.Column("email", sa.String(), primary_key=True, nullable=False),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table(_TABLE)
