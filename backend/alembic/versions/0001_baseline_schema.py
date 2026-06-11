"""baseline — capture the live schema as produced by the legacy startup path

Revision ID: 0001_baseline_schema
Revises:
Create Date: 2026-06-11

ADR-0022 — Alembic adoption baseline.

This revision reproduces the EXACT schema the legacy FastAPI-lifespan
startup path produced, namely the combination of:

  1. ``Base.metadata.create_all`` — every table + every PG enum type +
     PK / UNIQUE / FK / declared ``index=True`` (the TOS-audit indexes).
  2. ``app.main._add_missing_columns`` — the additive ``ALTER TABLE ADD
     COLUMN`` / ``ALTER TYPE ADD VALUE`` / ``ALTER COLUMN … TYPE TEXT`` /
     ``ADD CONSTRAINT`` batch that ``create_all`` cannot apply to existing
     tables (deposit columns, attribution columns, intake/portal columns,
     the widened ``maintenance_type`` columns, the deposit CHECK
     constraints, etc.).
  3. ``app.main._create_hot_indexes`` — the four ADR-0021 hot-path indexes
     (``ix_mission_flights_mission_id``, ``ix_flights_aircraft_id``,
     ``ix_customers_email``, ``ix_line_items_invoice_id``).

Rather than hand-transcribing the entire schema (which would inevitably
drift from the models and the ALTER batch and silently produce a baseline
that does NOT match production), this baseline **reuses the very functions
that built production**. The schema produced by this ``upgrade()`` is
therefore identical to the live schema BY CONSTRUCTION — verified by the
ADR-0022 test that runs ``upgrade head`` against an empty DB and asserts
``alembic revision --autogenerate`` yields an EMPTY diff against
``Base.metadata``.

BROWNFIELD: the live BOS-HQ database already has this entire schema (built
by the legacy startup path) but no ``alembic_version`` table. The
programmatic runner (``app.db_migrations``) detects that state and
``alembic stamp 0001_baseline_schema`` BEFORE upgrading, so this
revision's ``upgrade()`` is NEVER executed against the existing prod DB —
it only runs on a genuinely fresh/empty database. On an already-stamped
prod DB the runner jumps straight to revision 0002. (The functions reused
here are nonetheless fully idempotent — ``create_all`` is checkfirst,
``_add_missing_columns`` guards every ALTER, ``_create_hot_indexes`` uses
``IF NOT EXISTS`` — so even an accidental re-run is a no-op.)
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001_baseline_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Build the full schema on a fresh DB.

    Reuses the production schema-construction functions against the
    Alembic-provided synchronous connection so the result is byte-for-byte
    what the legacy startup path produced.
    """
    # Imported lazily so importing this migration module (e.g. for the
    # CLI's revision scan) does not import the whole FastAPI app eagerly.
    from app.database import Base
    import app.models  # noqa: F401 — register all tables on Base.metadata
    from app.main import _add_missing_columns, _create_hot_indexes

    conn = op.get_bind()

    # 1. Tables + enums + PK/UNIQUE/FK/declared indexes.
    Base.metadata.create_all(bind=conn)

    # 2. The additive ALTER batch create_all cannot apply (these are
    #    no-ops on a fresh DB whose columns already exist from create_all,
    #    but are applied verbatim so the baseline = legacy-startup output
    #    for the handful of cases create_all does NOT cover — e.g. the
    #    maintenance_type widening and the deposit CHECK constraints).
    _add_missing_columns(conn)

    # 3. The four ADR-0021 hot-path indexes.
    _create_hot_indexes(conn)


def downgrade() -> None:
    """Tear the entire schema back down (baseline = full schema)."""
    from app.database import Base
    import app.models  # noqa: F401

    conn = op.get_bind()
    Base.metadata.drop_all(bind=conn)
