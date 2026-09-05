"""ADR-0043 — migrations 0010/0011 on the path production will actually take.

``test_db_migrations.py``'s fresh-DB and brownfield tiers both build the
schema with ``Base.metadata.create_all`` from the LIVE models first. Since the
live models already declare ``flight_details``, ``flight_series`` and the
three battery columns, those tiers only ever exercise the **idempotency
guards** — ``op.create_table`` and ``op.add_column`` are never reached. A typo
inside either DDL body would sit green through the entire existing suite and
first surface as a crash-loop on BOS-HQ.

The live database is the opposite case: it is stamped at 0009 and does **not**
have these objects, so the DDL bodies are exactly what will run there. This
module reproduces that state — legacy schema, new objects removed, stamped at
0009 — and upgrades, so both branches of each migration are covered:

* here                        → the CREATE / ADD COLUMN branch (prod upgrade)
* ``test_db_migrations.py``   → the early-return branch (fresh install)

Opt-in on ``DOC_TEST_PG_URL`` exactly like the tier it complements, e.g.::

    docker run -d --name doc-mig-test -e POSTGRES_USER=doc \\
        -e POSTGRES_PASSWORD=test -e POSTGRES_DB=doc -p 55432:5432 \\
        postgres:16-alpine
    cd backend && DOC_TEST_PG_URL=postgresql+asyncpg://doc:test@127.0.0.1:55432/doc \\
        DATABASE_URL=$DOC_TEST_PG_URL pytest tests/test_migration_0010_0011_upgrade_path.py
"""

from __future__ import annotations

import os

import pytest

from tests.test_db_migrations import _autogenerate_diffs, _reset_public_schema

_PG_URL = os.environ.get("DOC_TEST_PG_URL")
pg_integration = pytest.mark.skipif(
    not _PG_URL,
    reason="set DOC_TEST_PG_URL (postgresql+asyncpg://…) to run real-DB migration tests",
)

NEW_TABLES = ("flight_details", "flight_series")
NEW_BATTERY_COLUMNS = (
    ("batteries", "cycle_count_observed"),
    ("batteries", "metrics_source"),
    ("battery_logs", "pack_cycle_count"),
)


def _build_pre_0010_schema(sync_url: str) -> None:
    """Reproduce a database that looks like BOS-HQ before this deploy.

    Full current schema, then the 0010/0011 objects dropped and the version
    stamp wound back to 0009 — i.e. every earlier migration applied, these two
    pending.
    """
    from sqlalchemy import create_engine, pool, text

    from app.database import Base
    from app.main import _add_missing_columns, _create_hot_indexes
    import app.models  # noqa: F401 — register every table on Base.metadata

    _reset_public_schema(sync_url)

    eng = create_engine(sync_url, poolclass=pool.NullPool)
    with eng.begin() as conn:
        Base.metadata.create_all(bind=conn)
        _add_missing_columns(conn)
        _create_hot_indexes(conn)
    with eng.begin() as conn:
        for table in NEW_TABLES:
            conn.execute(text(f"DROP TABLE IF EXISTS {table} CASCADE"))
        for table, column in NEW_BATTERY_COLUMNS:
            conn.execute(text(f"ALTER TABLE {table} DROP COLUMN IF EXISTS {column}"))
        # Stamp 0009 by writing alembic_version directly rather than calling
        # ``alembic.command.stamp`` with a bare Config. That path has no
        # ``connection`` attribute set, so env.py takes its CLI branch and runs
        # ``fileConfig()`` — which defaults to disable_existing_loggers=True and
        # silently kills every ``doc.*`` logger for the rest of the process.
        # That is the ADR-0042 hazard env.py's own guard exists to prevent, and
        # in a test session it makes unrelated log-assertion tests fail
        # depending on file ordering. A single INSERT builds the same state
        # with no global side effect.
        conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
        conn.execute(
            text(
                "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL "
                "CONSTRAINT alembic_version_pkc PRIMARY KEY)"
            )
        )
        conn.execute(
            text("INSERT INTO alembic_version (version_num) VALUES "
                 "('0009_mission_dl_email_sent_at')")
        )
    eng.dispose()


def _inspect(sync_url: str):
    from sqlalchemy import create_engine, inspect, pool

    eng = create_engine(sync_url, poolclass=pool.NullPool)
    with eng.connect() as conn:
        insp = inspect(conn)
        tables = set(insp.get_table_names())
        cols = {t: {c["name"] for c in insp.get_columns(t)} for t in tables}
        pk = {
            t: list(insp.get_pk_constraint(t)["constrained_columns"])
            for t in NEW_TABLES
            if t in tables
        }
        fks = {
            t: insp.get_foreign_keys(t) for t in NEW_TABLES if t in tables
        }
        idx = {t: insp.get_indexes(t) for t in NEW_TABLES if t in tables}
    eng.dispose()
    return tables, cols, pk, fks, idx


@pg_integration
def test_pending_upgrade_creates_the_sidecars_and_battery_columns():
    from app.config import settings
    from app.db_migrations import run_migrations_sync

    sync_url = settings.database_url_sync
    _build_pre_0010_schema(sync_url)

    # Precondition: this is genuinely the prod-shaped starting point.
    tables, cols, _, _, _ = _inspect(sync_url)
    for table in NEW_TABLES:
        assert table not in tables, f"{table} must be ABSENT before the upgrade"
    for table, column in NEW_BATTERY_COLUMNS:
        assert column not in cols[table]

    assert run_migrations_sync() == "upgraded"

    tables, cols, pk, fks, idx = _inspect(sync_url)
    for table in NEW_TABLES:
        assert table in tables, f"{table} was not created by migration 0010"
    for table, column in NEW_BATTERY_COLUMNS:
        assert column in cols[table], f"{table}.{column} was not added by 0011"

    # The DDL body — not just the table's existence — matches the model.
    assert pk["flight_details"] == ["flight_id"]
    assert pk["flight_series"] == ["flight_id", "source", "name"]

    # ON DELETE CASCADE is what keeps a deleted flight from orphaning
    # hundreds of KB of series rows.
    for table in NEW_TABLES:
        assert fks[table], f"{table} must carry a FK to flights"
        fk = fks[table][0]
        assert fk["referred_table"] == "flights"
        assert fk["options"].get("ondelete") == "CASCADE", fk

    # Plan §1.3: "Indexes at P0: the primary key only."
    for table in NEW_TABLES:
        assert idx[table] == [], f"{table} shipped an unplanned index: {idx[table]}"


@pg_integration
def test_upgraded_schema_matches_base_metadata():
    """After the real DDL runs, the schema equals the models — no drift.

    This is the assertion that would catch a column declared one way in the
    model and another way in the migration (wrong type, wrong nullability,
    missing entirely), which is the failure mode a hand-written
    ``create_table`` invites.
    """
    from app.config import settings
    from app.db_migrations import run_migrations_sync

    sync_url = settings.database_url_sync
    _build_pre_0010_schema(sync_url)
    assert run_migrations_sync() == "upgraded"

    diffs = _autogenerate_diffs(sync_url)
    assert diffs == [], f"post-0011 schema diverges from Base.metadata: {diffs}"


@pg_integration
def test_second_upgrade_is_a_noop():
    """Re-running must not raise DuplicateTable / DuplicateColumn."""
    from app.config import settings
    from app.db_migrations import run_migrations_sync

    sync_url = settings.database_url_sync
    _build_pre_0010_schema(sync_url)
    assert run_migrations_sync() == "upgraded"
    assert run_migrations_sync() == "noop"


@pg_integration
def test_deleting_a_flight_cascades_to_both_sidecars():
    """The FK cascade is the only thing stopping orphaned series rows.

    Asserted against the database, not the model declaration, because the ORM
    cascade and the DB cascade are separate mechanisms and only the DB one
    survives a raw DELETE.
    """
    import uuid

    from sqlalchemy import create_engine, pool, text

    from app.config import settings
    from app.db_migrations import run_migrations_sync

    sync_url = settings.database_url_sync
    _build_pre_0010_schema(sync_url)
    assert run_migrations_sync() == "upgraded"

    flight_id = uuid.uuid4()
    eng = create_engine(sync_url, poolclass=pool.NullPool)
    with eng.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO flights (id, name, duration_secs, total_distance, "
                "max_altitude, max_speed, point_count, source, created_at, updated_at) "
                "VALUES (:id, 'cascade probe', 0, 0, 0, 0, 0, 'dji_txt', now(), now())"
            ),
            {"id": str(flight_id)},
        )
        conn.execute(
            text(
                "INSERT INTO flight_details (flight_id, schema_version, generated_at) "
                "VALUES (:id, 1, now())"
            ),
            {"id": str(flight_id)},
        )
        conn.execute(
            text(
                'INSERT INTO flight_series (flight_id, source, name, unit, '
                'sample_count, precision_dp, "values") '
                "VALUES (:id, 'frame', 't_offset_s', 's', 3, 2, '[0,1,2]')"
            ),
            {"id": str(flight_id)},
        )
    with eng.connect() as conn:
        assert conn.execute(
            text("SELECT count(*) FROM flight_series WHERE flight_id = :id"),
            {"id": str(flight_id)},
        ).scalar() == 1
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM flights WHERE id = :id"), {"id": str(flight_id)})
    with eng.connect() as conn:
        for table in NEW_TABLES:
            assert conn.execute(
                text(f"SELECT count(*) FROM {table} WHERE flight_id = :id"),
                {"id": str(flight_id)},
            ).scalar() == 0, f"{table} row survived its flight's deletion"
    eng.dispose()
