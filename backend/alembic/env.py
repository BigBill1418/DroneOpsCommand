"""Alembic migration environment for DroneOpsCommand (ADR-0022).

Resolves the database URL at runtime from ``app.config.settings`` (the
single source of truth, fed by env / ``.env``) and runs migrations over a
**synchronous** psycopg2 connection. The application's runtime engine is
async (asyncpg); Alembic's migration context is synchronous, so we strip
the ``+asyncpg`` driver suffix here. ``psycopg2-binary`` is already a
prod dependency (``requirements.txt``).

``target_metadata`` is ``Base.metadata`` with **every** model imported, so
``alembic revision --autogenerate`` and the ADR-0022 validation tests
(empty-diff after ``upgrade head``) see the complete schema.
"""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# ── Path bootstrap ──────────────────────────────────────────────────────
# When invoked via the CLI from backend/, ``app`` is importable; when
# invoked programmatically from the running app it already is. Insert the
# backend root (this file's grandparent) defensively so the CLI works from
# any cwd.
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.config import settings  # noqa: E402
from app.database import Base  # noqa: E402
import app.models  # noqa: F401,E402 — register every model on Base.metadata

config = context.config

# Honour Alembic's own logging config from alembic.ini if present. Guard
# against a missing config_file_name (programmatic invocation passes a
# Config built in code that may not point at a file).
if config.config_file_name is not None:
    try:
        fileConfig(config.config_file_name)
    except (KeyError, Exception):  # pragma: no cover - logging best-effort
        pass

target_metadata = Base.metadata

# Indexes created imperatively by migrations (the ADR-0021 hot-path
# indexes, captured in baseline 0001, plus any added in later revisions)
# that are NOT declared as ``index=True`` on the models. Autogenerate would
# otherwise see them in the live DB, find no model counterpart, and propose
# DROPPING them on every future ``revision --autogenerate`` run. They are
# owned by the migration tree, not the models, so we exclude them from the
# autogenerate comparison. Adding a model-declared index later (so it lives
# in Base.metadata) means removing its name from this set.
_MIGRATION_MANAGED_INDEXES = frozenset({
    "ix_mission_flights_mission_id",  # ADR-0021
    "ix_flights_aircraft_id",         # ADR-0021
    "ix_customers_email",             # ADR-0021
    "ix_line_items_invoice_id",       # ADR-0021
    "ix_flights_start_time",          # ADR-0022 (FU-8 #6)
    "ix_flights_created_at",          # ADR-0022 (FU-8 #6)
    "ix_missions_customer_id",        # ADR-0022 (FU-8 #6)
    "ix_missions_status",             # ADR-0022 (FU-8 #6)
    "ix_mission_images_mission_id",   # ADR-0022 (FU-8 #6)
    "ix_maintenance_records_aircraft_id",    # ADR-0022 (FU-8 #6)
    "ix_maintenance_schedules_aircraft_id",  # ADR-0022 (FU-8 #6)
    "uq_flights_autoname",            # ADR-0027 partial unique on auto names
})


def _include_object(obj, name, type_, reflected, compare_to):
    """Exclude migration-managed indexes from autogenerate comparison.

    Keeps ``alembic revision --autogenerate`` (and the ADR-0022 empty-diff
    validation test) from proposing to drop the imperatively-created
    hot-path indexes that have no model-declared counterpart.
    """
    if type_ == "index" and name in _MIGRATION_MANAGED_INDEXES:
        return False
    return True


def _sync_database_url() -> str:
    """App async URL → sync URL for the migration connection.

    ``postgresql+asyncpg://…`` → ``postgresql://…`` (psycopg2 default).
    Mirrors ``settings.database_url_sync`` but resolved here so env.py is
    self-contained for the CLI path.
    """
    return settings.database_url.replace("+asyncpg", "")


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL, no DBAPI connection)."""
    context.configure(
        url=_sync_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live (synchronous) connection.

    If a caller has already supplied a connection via
    ``config.attributes['connection']`` (the programmatic path can do this
    to share a connection / transaction), reuse it. Otherwise build a
    short-lived psycopg2 engine from the resolved sync URL.
    """
    connectable = config.attributes.get("connection", None)

    if connectable is not None:
        # Caller-supplied connection (the programmatic path in
        # app.db_migrations). The CALLER owns the transaction lifecycle and
        # commits — sharing a connection across stamp+upgrade and committing
        # once keeps the brownfield "stamp baseline then upgrade" atomic.
        # We still wrap in begin_transaction() so Alembic's version-table
        # bookkeeping participates, but the caller's outer transaction +
        # explicit commit is what persists it.
        context.configure(
            connection=connectable,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
            include_object=_include_object,
        )
        with context.begin_transaction():
            context.run_migrations()
        return

    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _sync_database_url()
    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
            include_object=_include_object,
        )
        with context.begin_transaction():
            context.run_migrations()
    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
