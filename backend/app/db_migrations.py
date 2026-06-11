"""Programmatic Alembic runner for the FastAPI startup path (ADR-0022).

Replaces the legacy imperative schema block (``create_all`` +
``_add_missing_columns`` + ``_create_hot_indexes``) with a real migration
run. Invoked from the lifespan ONLY on a writable primary (the ADR-0021
``pg_is_in_recovery()`` guard still wraps this — a standby never reaches
here), and ONLY inside an executor thread so the synchronous psycopg2
migration never blocks the event loop.

Two correct entry states, both handled here:

  * **Fresh / empty DB** (new install, test DB, CI): no ``alembic_version``
    table and no schema → ``alembic upgrade head`` builds everything from
    revision 0001 onward.

  * **Brownfield** (the live BOS-HQ prod DB): the entire schema already
    exists (built by the legacy startup path across dozens of releases)
    but there is **no** ``alembic_version`` table. Running ``upgrade head``
    blind would try to re-create existing tables. So we **detect** that
    state — ``alembic_version`` absent AND a sentinel table (``missions``)
    present — and ``alembic stamp 0001_baseline_schema`` first, recording
    that the baseline is already applied WITHOUT running its DDL. Then
    ``upgrade head`` runs only the migrations AFTER the baseline (0002+).

The detection + stamp + upgrade all run on ONE synchronous connection so
the decision and the action cannot race against another booting worker.
``seed.py`` already takes a PG advisory lock to serialize seeding across
workers; migrations are guarded by Alembic's own ``alembic_version``
row-level semantics plus this single-connection flow.
"""

from __future__ import annotations

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect

from app.config import settings

logger = logging.getLogger("doc.migrations")

# The baseline revision that captures the pre-Alembic live schema. A
# brownfield DB (schema present, no alembic_version) is stamped to this
# before upgrading. Kept as a module constant so the value is asserted in
# tests and referenced in one place.
BASELINE_REVISION = "0001_baseline_schema"

# Sentinel table proving "the legacy schema is already here". ``missions``
# is a core table created by the very first legacy schema and present in
# every real DroneOpsCommand database.
SENTINEL_TABLE = "missions"

_BACKEND_ROOT = Path(__file__).resolve().parent.parent


def _alembic_config() -> Config:
    """Build an Alembic ``Config`` pointing at the in-repo migration tree.

    Resolves ``script_location`` and the ini file by path so the runner
    works regardless of process cwd (the container runs from ``/app``).
    """
    ini_path = _BACKEND_ROOT / "alembic.ini"
    cfg = Config(str(ini_path)) if ini_path.is_file() else Config()
    cfg.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))
    # env.py resolves the URL itself from settings; we still set it so any
    # bypass path has the correct value.
    cfg.set_main_option("sqlalchemy.url", settings.database_url_sync)
    return cfg


def _current_head(cfg: Config) -> str | None:
    """The single head revision id of the migration tree (or None)."""
    script = ScriptDirectory.from_config(cfg)
    return script.get_current_head()


def run_migrations_sync() -> str:
    """Bring the database schema to ``head``. Returns the action taken.

    SYNCHRONOUS — call via ``run_in_executor`` from async code. Builds its
    own short-lived psycopg2 engine (NullPool) so it never touches the
    app's async engine/pool.

    Return values (for logging / tests):
      * ``"upgraded:fresh"``      — empty DB, built from 0001.
      * ``"stamped+upgraded"``    — brownfield: stamped baseline, then 0002+.
      * ``"upgraded"``            — already had alembic_version, applied any
                                    pending revisions (the steady state).
      * ``"noop"``                — already at head, nothing to do.
    """
    cfg = _alembic_config()
    sync_url = settings.database_url_sync

    from sqlalchemy import pool as _pool

    engine = create_engine(sync_url, poolclass=_pool.NullPool)
    head = _current_head(cfg)
    try:
        # ── Phase 1: detect state (read-only) ───────────────────────────
        with engine.connect() as probe:
            inspector = inspect(probe)
            has_alembic_version = inspector.has_table("alembic_version")
            has_sentinel = inspector.has_table(SENTINEL_TABLE)
            current = None
            if has_alembic_version:
                current = MigrationContext.configure(probe).get_current_revision()

        # ── Phase 2: act (one committed transaction) ────────────────────
        # ``engine.begin()`` opens a connection already inside a
        # transaction and COMMITS it on clean exit. We share that single
        # connection with env.py so stamp+upgrade are atomic; Alembic's
        # own ``begin_transaction()`` nests harmlessly inside it. Without
        # an owning transaction that commits, a caller-supplied connection
        # silently rolls back on close (SQLAlchemy 2.0 no-autocommit) — a
        # trap caught during ADR-0022 validation.
        if not has_alembic_version and not has_sentinel:
            action = "upgraded:fresh"
            logger.info(
                "MIGRATIONS: fresh database — running upgrade head from "
                "baseline (ADR-0022)."
            )
        elif not has_alembic_version and has_sentinel:
            action = "stamped+upgraded"
            logger.warning(
                "MIGRATIONS: brownfield DB detected (schema present, no "
                "alembic_version) — stamping baseline %s without running its "
                "DDL, then upgrading to head (ADR-0022).",
                BASELINE_REVISION,
            )
        elif current == head:
            logger.info(
                "MIGRATIONS: schema already at head (%s) — no migration "
                "needed.", head,
            )
            return "noop"
        else:
            action = "upgraded"
            logger.info(
                "MIGRATIONS: upgrading schema from %s to head %s (ADR-0022).",
                current, head,
            )

        with engine.begin() as conn:
            cfg.attributes["connection"] = conn
            if action == "stamped+upgraded":
                command.stamp(cfg, BASELINE_REVISION)
            command.upgrade(cfg, "head")

        logger.info("MIGRATIONS: %s complete (head=%s)", action, head)
        return action
    finally:
        cfg.attributes.pop("connection", None)
        engine.dispose()
