"""ADR-0021 — failover guard for the startup schema-DDL / seed / backfill block.

The lifespan in ``app.main`` runs ``create_all`` + ``_add_missing_columns``
+ ``_create_hot_indexes`` + ``seed_database`` + the aircraft backfill on
every backend boot. On BOS-HQ that boot hits ``droneops-standby-db`` (the
promoted primary). If ``DATABASE_URL`` ever resolves to a node that is
still a read-only standby (mid-failover or a misconfig), those writes raise
*"cannot execute … in a read-only transaction"* and crash-loop the
container during a failover — the worst possible moment.

These tests prove the guard:

  1. ``_is_in_recovery`` returns the boolean that ``pg_is_in_recovery()``
     yields, and fails SAFE (→ primary) if the probe itself errors.
  2. The lifespan SKIPS the entire schema/seed block when the DB is in
     recovery, and RUNS it when the DB is a writable primary.
  3. ``_create_hot_indexes`` issues the four ADR-0021 ``CREATE INDEX IF NOT
     EXISTS`` statements and is best-effort per index (one bad table does
     not abort the rest).

Hermetic: no Postgres / Redis / FastAPI ASGI stack is started. We mock the
async engine connection and the heavy startup helpers — same spirit as
``test_missions_post_rejects_id_in_body.py`` (no real DB; doubles quack
like the SQLAlchemy objects the code touches).
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from app import main


# ── Async connection / engine doubles ──────────────────────────────────


class _FakeResult:
    """Mimics the object returned by ``await conn.execute(...)``."""

    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar(self) -> Any:
        return self._value


class _FakeAsyncConn:
    """Async-context-manager connection double.

    ``execute`` is awaitable and returns a ``_FakeResult`` wrapping the
    pre-seeded scalar (the ``pg_is_in_recovery()`` answer). If
    ``raise_on_execute`` is set, ``execute`` raises it — exercising the
    fail-safe path.
    """

    def __init__(self, scalar_value: Any = False, raise_on_execute: Exception | None = None) -> None:
        self._scalar = scalar_value
        self._raise = raise_on_execute

    async def __aenter__(self) -> "_FakeAsyncConn":
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False

    async def execute(self, _stmt: Any) -> _FakeResult:
        if self._raise is not None:
            raise self._raise
        return _FakeResult(self._scalar)


def _engine_returning(scalar_value: Any = False, raise_on_execute: Exception | None = None) -> SimpleNamespace:
    """Build an engine double whose ``.connect()`` yields a fake conn."""

    def _connect() -> _FakeAsyncConn:
        return _FakeAsyncConn(scalar_value=scalar_value, raise_on_execute=raise_on_execute)

    return SimpleNamespace(connect=_connect)


# ── 1. _is_in_recovery returns the probe's boolean / fails safe ─────────


def test_is_in_recovery_true_when_standby():
    """A standby answers pg_is_in_recovery() = True → guard returns True."""
    with patch.object(main, "engine", _engine_returning(scalar_value=True)):
        assert asyncio.run(main._is_in_recovery()) is True


def test_is_in_recovery_false_when_primary():
    """A writable primary answers False → guard returns False."""
    with patch.object(main, "engine", _engine_returning(scalar_value=False)):
        assert asyncio.run(main._is_in_recovery()) is False


def test_is_in_recovery_fails_safe_to_primary_on_probe_error():
    """If the probe itself errors we assume PRIMARY (False), never wrongly
    treat a healthy primary as a standby and leave it un-migrated."""
    boom = RuntimeError("driver hiccup")
    with patch.object(main, "engine", _engine_returning(raise_on_execute=boom)):
        assert asyncio.run(main._is_in_recovery()) is False


# ── 2. lifespan SKIPS vs RUNS the schema/seed block ─────────────────────


def _drive_lifespan_decision(in_recovery: bool) -> bool:
    """Run the lifespan far enough to make the guard decision, with every
    heavy side effect mocked out. Returns whether the schema/seed block ran.

    We stub: the dependency waits, the recovery probe (forced value), the
    extracted schema/seed function (records if called), and the
    filesystem-only tail (os.makedirs / image copy are harmless but we keep
    the test from touching disk).
    """
    ran = {"schema_seed": False}

    async def _fake_wait(*_a: Any, **_k: Any) -> None:
        return None

    async def _fake_is_in_recovery() -> bool:
        return in_recovery

    async def _fake_schema_seed() -> None:
        ran["schema_seed"] = True

    async def _run() -> None:
        with patch.object(main, "_wait_for_db", _fake_wait), \
                patch.object(main, "_wait_for_redis", _fake_wait), \
                patch.object(main, "_is_in_recovery", _fake_is_in_recovery), \
                patch.object(main, "_run_startup_schema_and_seed", _fake_schema_seed), \
                patch.object(main.os, "makedirs", lambda *a, **k: None), \
                patch.object(main.os.path, "isdir", lambda *a, **k: False):
            # ``lifespan`` is an @asynccontextmanager; entering it runs the
            # startup body up to ``yield``.
            async with main.lifespan(SimpleNamespace()):
                pass

    asyncio.run(_run())
    return ran["schema_seed"]


def test_lifespan_skips_schema_seed_when_in_recovery():
    """The whole DDL/seed/backfill block is skipped on a standby — the
    backend comes up serving READ traffic instead of crash-looping."""
    assert _drive_lifespan_decision(in_recovery=True) is False


def test_lifespan_runs_schema_seed_when_primary():
    """On a writable primary the schema/seed block runs as before."""
    assert _drive_lifespan_decision(in_recovery=False) is True


# ── 3. _create_hot_indexes issues the four ADR-0021 indexes ─────────────


class _RecordingSyncConn:
    """Sync connection double — captures the SQL text of each execute."""

    def __init__(self, fail_for: set[str] | None = None) -> None:
        self.executed: list[str] = []
        self._fail_for = fail_for or set()

    def execute(self, stmt: Any) -> None:
        sql = str(getattr(stmt, "text", stmt))
        self.executed.append(sql)
        for token in self._fail_for:
            if token in sql:
                raise RuntimeError(f"simulated failure on {token}")


def test_create_hot_indexes_issues_all_four_idempotent():
    """Creates exactly the four ADR-0021 indexes, each IF NOT EXISTS on the
    correct table+column."""
    conn = _RecordingSyncConn()
    main._create_hot_indexes(conn)

    joined = "\n".join(conn.executed)
    assert len(conn.executed) == 4, conn.executed
    for sql in conn.executed:
        assert "CREATE INDEX IF NOT EXISTS" in sql, sql

    expected = {
        ("ix_mission_flights_mission_id", "mission_flights", "mission_id"),
        ("ix_flights_aircraft_id", "flights", "aircraft_id"),
        ("ix_customers_email", "customers", "email"),
        ("ix_line_items_invoice_id", "line_items", "invoice_id"),
    }
    for index_name, table, column in expected:
        assert index_name in joined, f"missing {index_name}"
        assert f"ON {table} ({column})" in joined, f"wrong target for {index_name}"


def test_create_hot_indexes_is_best_effort_per_index():
    """A failure building one index (e.g. a missing table) must NOT abort
    the rest — the other three still get issued."""
    conn = _RecordingSyncConn(fail_for={"line_items"})
    # Must not raise.
    main._create_hot_indexes(conn)
    # All four were attempted (the failing one raised internally, caught).
    assert len(conn.executed) == 4, conn.executed


# ── Sanity: the four index names are stable contract (used by ops/docs) ──


def test_hot_index_names_match_adr0021_contract():
    conn = _RecordingSyncConn()
    main._create_hot_indexes(conn)
    names = {sql.split("IF NOT EXISTS")[1].split(" ON ")[0].strip() for sql in conn.executed}
    assert names == {
        "ix_mission_flights_mission_id",
        "ix_flights_aircraft_id",
        "ix_customers_email",
        "ix_line_items_invoice_id",
    }
