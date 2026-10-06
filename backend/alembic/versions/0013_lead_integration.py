"""ADR-0050 — website-lead link columns.

customers.source_ref      — lead key the customer was created from (web-N / cold-N)
missions.lead_writeback_at — when the lead was marked `won` in the marketing pipeline

Idempotent: on a fresh install 0001's live-models create_all already built both
columns (the v2.80.2 fresh-install trap), so each add is guarded.
"""
import sqlalchemy as sa
from alembic import op

revision = "0013_lead_integration"
down_revision = "0012_cf_access_ident"
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "source_ref" not in {c["name"] for c in insp.get_columns("customers")}:
        op.add_column("customers", sa.Column("source_ref", sa.String(length=255), nullable=True))
    if "lead_writeback_at" not in {c["name"] for c in insp.get_columns("missions")}:
        op.add_column("missions", sa.Column("lead_writeback_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "lead_writeback_at" in {c["name"] for c in insp.get_columns("missions")}:
        op.drop_column("missions", "lead_writeback_at")
    if "source_ref" in {c["name"] for c in insp.get_columns("customers")}:
        op.drop_column("customers", "source_ref")
