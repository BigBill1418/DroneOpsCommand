"""ADR-0022 — Alembic adoption: migration tree integrity + brownfield runner.

Two tiers:

  * **Hermetic** (always run): assert the migration tree is well-formed
    (single linear head, baseline first, the ADR-0022 index revision after
    it), that the runner's brownfield-detection logic dispatches correctly,
    and that the runner is offloaded (synchronous, called via executor from
    the async lifespan).

  * **Real-Postgres integration** (skipped unless ``DOC_TEST_PG_URL`` is
    set, e.g. ``postgresql+asyncpg://doc:test@127.0.0.1:55432/doc``):
    proves BOTH startup paths end-to-end —
      (a) FRESH empty DB → ``upgrade head`` → schema == ``Base.metadata``
          (autogenerate yields an EMPTY diff);
      (b) BROWNFIELD (legacy ``create_all`` + ``_add_missing_columns`` +
          ``_create_hot_indexes``, NO ``alembic_version``) → runner detects,
          stamps the baseline, upgrades → same empty diff;
    and that both converge to the same head with the same indexes.

The integration tier is exactly what was run during development against a
disposable ``postgres:16-alpine`` container; CI / the operator can re-run it
by exporting ``DOC_TEST_PG_URL``.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest


# ── Hermetic: migration tree shape ──────────────────────────────────────


def _script_dir():
    from alembic.script import ScriptDirectory
    from app.db_migrations import _alembic_config
    return ScriptDirectory.from_config(_alembic_config())


def test_migration_tree_has_single_linear_head():
    script = _script_dir()
    heads = script.get_heads()
    assert heads == ["0008_report_dl_payment_override"], heads


def test_baseline_is_the_root_revision():
    script = _script_dir()
    bases = script.get_bases()
    assert bases == ["0001_baseline_schema"], bases


def test_revision_chain_is_baseline_then_indexes():
    script = _script_dir()
    # Walk from head back to base; expect exactly the revisions, in order.
    revs = [r.revision for r in script.walk_revisions()]
    assert revs == [
        "0008_report_dl_payment_override",
        "0007_strip_legacy_cache_track",
        "0006_repair_odl_distance",
        "0005_flight_hash_unique_index",
        "0004_dji_duration_name_restamp",
        "0003_mission_flight_dedup_unique",
        "0002_p2_p3_indexes",
        "0001_baseline_schema",
    ], revs


def test_baseline_revision_constant_matches_tree():
    from app.db_migrations import BASELINE_REVISION
    script = _script_dir()
    assert script.get_revision(BASELINE_REVISION) is not None


# ── Hermetic: brownfield-detection dispatch ─────────────────────────────


def _run_with_fakes(*, has_alembic_version: bool, has_sentinel: bool,
                    current: str | None, head: str = "0002_p2_p3_indexes",
                    upgrade_raises: bool = False, calls: dict | None = None):
    """Drive ``run_migrations_sync`` with the engine / inspector / Alembic
    command surface mocked, returning ``(action, calls)``.

    No real DB. We assert the BRANCHING logic (fresh vs brownfield vs
    pending vs noop) AND the advisory-lock envelope (ADR-0035): ``calls``
    carries ``lock_sql`` (the raw SQL issued on the dedicated lock
    connection) and ``events`` (an ordered log of lock/stamp/upgrade/unlock)
    so tests can prove the lock is acquired BEFORE the upgrade and released
    AFTER — even when the upgrade raises.

    ``action`` is ``None`` when ``upgrade_raises`` is set (the exception
    propagates through the runner; the caller catches it).
    """
    from app import db_migrations as dm

    if calls is None:
        calls = {}
    calls.setdefault("stamp", 0)
    calls.setdefault("upgrade", 0)
    calls.setdefault("stamp_rev", None)
    calls.setdefault("lock_sql", [])
    calls.setdefault("events", [])

    # A single fake connection type serves every ``engine.connect()`` /
    # ``engine.begin()`` call. It is BOTH a context manager (the read-only
    # probe + the ``begin()`` upgrade transaction use ``with``) AND a plain
    # object with ``execution_options``/``execute``/``close`` (the dedicated
    # advisory-lock connection is held open across the whole run). It records
    # any advisory lock/unlock SQL it is asked to run.
    class _FakeConn:
        def execution_options(self, **_k):
            return self

        def execute(self, clause):
            sql = str(clause)
            if "pg_advisory_lock" in sql:
                calls["lock_sql"].append(sql)
                calls["events"].append("lock")
            elif "pg_advisory_unlock" in sql:
                calls["lock_sql"].append(sql)
                calls["events"].append("unlock")
            return MagicMock()

        def close(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    fake_engine = MagicMock()
    fake_engine.connect.side_effect = lambda: _FakeConn()
    fake_engine.begin.side_effect = lambda: _FakeConn()

    fake_inspector = MagicMock()
    fake_inspector.has_table.side_effect = lambda t: (
        has_alembic_version if t == "alembic_version" else has_sentinel
    )

    fake_mc = MagicMock()
    fake_mc.get_current_revision.return_value = current

    def _stamp(cfg, rev):
        calls["stamp"] += 1
        calls["stamp_rev"] = rev
        calls["events"].append("stamp")

    def _upgrade(cfg, rev):
        calls["upgrade"] += 1
        calls["events"].append("upgrade")
        if upgrade_raises:
            raise RuntimeError("boom during upgrade")

    with patch.object(dm, "create_engine", return_value=fake_engine), \
            patch.object(dm, "inspect", return_value=fake_inspector), \
            patch.object(dm.MigrationContext, "configure", return_value=fake_mc), \
            patch.object(dm, "_current_head", return_value=head), \
            patch.object(dm.command, "stamp", _stamp), \
            patch.object(dm.command, "upgrade", _upgrade):
        action = dm.run_migrations_sync()
    return action, calls


def test_fresh_db_upgrades_without_stamp():
    action, calls = _run_with_fakes(
        has_alembic_version=False, has_sentinel=False, current=None)
    assert action == "upgraded:fresh"
    assert calls["stamp"] == 0
    assert calls["upgrade"] == 1


def test_brownfield_stamps_baseline_then_upgrades():
    from app.db_migrations import BASELINE_REVISION
    action, calls = _run_with_fakes(
        has_alembic_version=False, has_sentinel=True, current=None)
    assert action == "stamped+upgraded"
    assert calls["stamp"] == 1
    assert calls["stamp_rev"] == BASELINE_REVISION
    assert calls["upgrade"] == 1


def test_already_managed_pending_upgrades_without_stamp():
    action, calls = _run_with_fakes(
        has_alembic_version=True, has_sentinel=True,
        current="0001_baseline_schema")
    assert action == "upgraded"
    assert calls["stamp"] == 0
    assert calls["upgrade"] == 1


def test_already_at_head_is_noop():
    action, calls = _run_with_fakes(
        has_alembic_version=True, has_sentinel=True,
        current="0002_p2_p3_indexes")
    assert action == "noop"
    assert calls["stamp"] == 0
    assert calls["upgrade"] == 0


# ── Hermetic: migration run is advisory-locked (ADR-0035) ───────────────


def test_migration_lock_id_is_distinct_from_seed_lock():
    """The migration lock must NOT collide with seed's, so migrating and
    seeding don't needlessly serialize against each other (plan §Phase 1)."""
    from app.db_migrations import _MIGRATION_LOCK_ID
    from app.seed import _SEED_LOCK_ID
    assert _MIGRATION_LOCK_ID != _SEED_LOCK_ID
    assert isinstance(_MIGRATION_LOCK_ID, int)


def test_migration_acquires_and_releases_advisory_lock():
    """A steady-state upgrade takes ``pg_advisory_lock(<id>)`` before doing
    any work and ``pg_advisory_unlock(<id>)`` after — on the same id."""
    from app.db_migrations import _MIGRATION_LOCK_ID
    action, calls = _run_with_fakes(
        has_alembic_version=True, has_sentinel=True, current=None)
    assert action == "upgraded"
    assert calls["lock_sql"] == [
        f"SELECT pg_advisory_lock({_MIGRATION_LOCK_ID})",
        f"SELECT pg_advisory_unlock({_MIGRATION_LOCK_ID})",
    ], calls["lock_sql"]


def test_lock_is_held_around_the_upgrade():
    """Ordering contract: lock acquired BEFORE the upgrade runs, released
    AFTER — so two booting workers cannot both be inside ``upgrade``."""
    _, calls = _run_with_fakes(
        has_alembic_version=True, has_sentinel=True, current=None)
    assert calls["events"] == ["lock", "upgrade", "unlock"], calls["events"]


def test_lock_wraps_brownfield_stamp_and_upgrade():
    """The lock spans the entire detect+stamp+upgrade critical section."""
    _, calls = _run_with_fakes(
        has_alembic_version=False, has_sentinel=True, current=None)
    assert calls["events"] == ["lock", "stamp", "upgrade", "unlock"], calls["events"]


def test_noop_path_still_acquires_and_releases_lock():
    """Even the already-at-head fast path must take + drop the lock: a losing
    racer BLOCKS on acquire until the winner finishes, then re-detects head
    and no-ops (it must not skip acquisition and proceed un-serialized)."""
    from app.db_migrations import _MIGRATION_LOCK_ID
    action, calls = _run_with_fakes(
        has_alembic_version=True, has_sentinel=True,
        current="0002_p2_p3_indexes")
    assert action == "noop"
    assert calls["upgrade"] == 0
    assert calls["events"] == ["lock", "unlock"], calls["events"]
    assert calls["lock_sql"] == [
        f"SELECT pg_advisory_lock({_MIGRATION_LOCK_ID})",
        f"SELECT pg_advisory_unlock({_MIGRATION_LOCK_ID})",
    ]


def test_lock_released_even_when_upgrade_raises():
    """If ``command.upgrade`` throws, the exception propagates BUT the lock
    is still released in the ``finally`` — a crash must never leave the
    advisory lock held and wedge the next booting worker forever."""
    with pytest.raises(RuntimeError, match="boom during upgrade"):
        _run_with_fakes(has_alembic_version=True, has_sentinel=True,
                        current=None, upgrade_raises=True)


def test_lock_release_ordering_when_upgrade_raises():
    """Capture the event log across a failing upgrade to prove ``unlock``
    fired AFTER the failed ``upgrade`` — i.e. the lock was released on the
    exception path, not leaked."""
    calls: dict = {}
    with pytest.raises(RuntimeError, match="boom during upgrade"):
        _run_with_fakes(has_alembic_version=True, has_sentinel=True,
                        current=None, upgrade_raises=True, calls=calls)
    assert calls["events"] == ["lock", "upgrade", "unlock"], calls["events"]


# ── Hermetic: revision-id length invariant (v2.75.1 crash-loop fence) ────


def test_all_revision_ids_within_alembic_varchar_limit():
    """``alembic_version.version_num`` is ``VARCHAR(32)``. A revision id > 32
    chars runs its DDL then rolls the stamp back on EVERY boot — the v2.75.1
    crash-loop (revision ``0004`` was 41 chars). Fail CI before it ships."""
    script = _script_dir()
    offenders = {r.revision: len(r.revision)
                 for r in script.walk_revisions() if len(r.revision) > 32}
    assert not offenders, (
        "revision id(s) exceed alembic_version VARCHAR(32) and will "
        f"crash-loop the backend at startup: {offenders}"
    )


# ── Hermetic: startup offloads the (sync) runner to an executor ──────────


@pytest.mark.asyncio
async def test_startup_schema_offloads_migrations_to_executor():
    """The migration runner is synchronous (psycopg2). The lifespan MUST
    dispatch it via ``run_in_executor`` so it never blocks the event loop —
    house discipline (same as backup/Stripe/PIL offloads)."""
    from app import main

    seen = {"in_executor": False}

    real_run_in_executor = None

    async def _fake_seed_tail(*_a, **_k):
        return None

    # Patch the heavy seed / demo / backfill tail to no-ops by patching the
    # symbols _run_startup_schema_and_seed pulls in. Simplest: patch the
    # runner itself and assert it was invoked via loop.run_in_executor.
    def _fake_runner():
        return "noop"

    import asyncio as _asyncio
    orig_loop = _asyncio.get_event_loop_policy().get_event_loop

    with patch("app.db_migrations.run_migrations_sync", _fake_runner), \
            patch("app.seed.seed_database", new=_fake_seed_tail), \
            patch.object(main.settings, "demo_mode", False), \
            patch.object(main, "async_session") as fake_session_factory:
        # async_session() is used for seed/admin/backfill sessions — make it
        # an async context manager yielding a session whose execute returns
        # an object with .scalars().all() == [] (no users, no unlinked).
        class _Res:
            def scalars(self):
                return self

            def all(self):
                return []

        class _Sess:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def execute(self, *_a, **_k):
                return _Res()

            async def commit(self):
                return None

            def add(self, *_a, **_k):
                return None

        fake_session_factory.side_effect = lambda: _Sess()

        loop = _asyncio.get_running_loop()
        orig_rie = loop.run_in_executor

        def _spy_rie(executor, func, *args):
            if func is _fake_runner:
                seen["in_executor"] = True
            return orig_rie(executor, func, *args)

        with patch.object(loop, "run_in_executor", _spy_rie):
            await main._run_startup_schema_and_seed()

    assert seen["in_executor"] is True


# ── Real-Postgres integration (opt-in via DOC_TEST_PG_URL) ──────────────

_PG_URL = os.environ.get("DOC_TEST_PG_URL")
pg_integration = pytest.mark.skipif(
    not _PG_URL,
    reason="set DOC_TEST_PG_URL (postgresql+asyncpg://…) to run real-DB migration tests",
)


def _reset_public_schema(sync_url: str) -> None:
    from sqlalchemy import create_engine, text, pool
    eng = create_engine(sync_url, poolclass=pool.NullPool)
    with eng.begin() as c:
        c.execute(text("DROP SCHEMA public CASCADE"))
        c.execute(text("CREATE SCHEMA public"))
    eng.dispose()


def _autogenerate_diffs(sync_url: str):
    """Return the non-managed autogenerate diffs vs Base.metadata."""
    from sqlalchemy import create_engine, pool
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from app.database import Base
    import app.models  # noqa: F401

    managed = {
        "ix_mission_flights_mission_id", "ix_flights_aircraft_id",
        "ix_customers_email", "ix_line_items_invoice_id",
        "ix_flights_start_time", "ix_flights_created_at",
        "ix_missions_customer_id", "ix_missions_status",
        "ix_mission_images_mission_id", "ix_maintenance_records_aircraft_id",
        "ix_maintenance_schedules_aircraft_id",
        # ADR-0026 partial unique guards (migration 0003) — created by the
        # migration, not declared in Base.metadata, so excluded from the diff.
        "uq_mission_flights_mission_flight", "uq_mission_flights_mission_odl",
        # ADR-0027 partial unique on auto-generated flight names (migration 0004).
        "uq_flights_autoname",
        # ADR-0028 H4 partial unique on the dedup hash (migration 0005).
        "uq_flights_source_file_hash",
    }

    def _inc(obj, name, type_, reflected, compare_to):
        if type_ == "index" and name in managed:
            return False
        if type_ == "table" and name == "alembic_version":
            return False
        return True

    eng = create_engine(sync_url, poolclass=pool.NullPool)
    with eng.connect() as c:
        mc = MigrationContext.configure(
            c, opts={"compare_type": True, "compare_server_default": True,
                     "include_object": _inc})
        diffs = compare_metadata(mc, Base.metadata)
    eng.dispose()
    return diffs


@pg_integration
def test_fresh_db_upgrade_matches_base_metadata():
    from app.config import settings
    from app.db_migrations import run_migrations_sync
    sync_url = settings.database_url_sync
    _reset_public_schema(sync_url)

    assert run_migrations_sync() == "upgraded:fresh"
    diffs = _autogenerate_diffs(sync_url)
    assert diffs == [], f"fresh-DB schema diverges from Base.metadata: {diffs}"


@pg_integration
def test_brownfield_stamp_and_upgrade_matches_base_metadata():
    from sqlalchemy import create_engine, pool
    from app.config import settings
    from app.database import Base
    import app.models  # noqa: F401
    from app.main import _add_missing_columns, _create_hot_indexes
    from app.db_migrations import run_migrations_sync

    sync_url = settings.database_url_sync
    _reset_public_schema(sync_url)

    # Build the schema the LEGACY way — exactly the pre-Alembic startup path.
    eng = create_engine(sync_url, poolclass=pool.NullPool)
    with eng.begin() as conn:
        Base.metadata.create_all(bind=conn)
        _add_missing_columns(conn)
        _create_hot_indexes(conn)
    eng.dispose()

    # Sanity: brownfield preconditions.
    from sqlalchemy import inspect, text
    eng = create_engine(sync_url, poolclass=pool.NullPool)
    with eng.connect() as c:
        insp = inspect(c)
        assert insp.has_table("missions"), "sentinel table must exist"
        assert not insp.has_table("alembic_version"), "must be un-stamped"
    eng.dispose()

    # Runner must detect brownfield, stamp baseline, upgrade to head.
    assert run_migrations_sync() == "stamped+upgraded"

    # Schema now matches Base.metadata (empty diff) and is at head.
    diffs = _autogenerate_diffs(sync_url)
    assert diffs == [], f"brownfield schema diverges: {diffs}"

    eng = create_engine(sync_url, poolclass=pool.NullPool)
    with eng.connect() as c:
        head = c.execute(text("SELECT version_num FROM alembic_version")).scalar()
    eng.dispose()
    assert head == "0008_report_dl_payment_override", head

    # Idempotent: a second run is a no-op.
    assert run_migrations_sync() == "noop"
