# ADR-0036 — Migration single-path hardening: advisory-lock the Alembic boot path

- **Status:** Accepted
- **Date:** 2026-07-03
- **Relates to:** **ADR-0022** (Alembic adoption — baseline + brownfield stamp;
  `run_migrations_sync` is the startup schema mechanism), **ADR-0021** (the
  `pg_is_in_recovery()` primary-only recovery guard that wraps the whole write
  block — preserved intact), **ADR-0016** (mission source attribution — used the
  legacy `_add_missing_columns` mechanism, now captured in baseline 0001), the
  **v2.75.1** hotfix (revision-id length crash-loop). Executes **Phase 1** of
  `docs/plans/2026-07-03-migration-consolidation.md`.

## Context

The migration-consolidation investigation (see the plan doc) established that the
feared "three schema mechanisms running simultaneously" split-brain is **already
mostly resolved**: `backend/app/db_migrations.py` *is* the Alembic runner (ADR-0022);
`_add_missing_columns` / `_create_hot_indexes` / `Base.metadata.create_all` survive in
`main.py` but are invoked only by baseline migration `0001_baseline_schema.py` for
brownfield stamping, not by the runtime boot path (`main.py:387` calls
`run_migrations_sync` behind the ADR-0021 recovery guard).

What remained was a latent correctness hole, not a mechanism migration:

1. **No advisory lock on the migration run.** The seed path already takes
   `pg_advisory_lock(8675309)` (`seed.py`), but `run_migrations_sync` relied only on
   transaction atomicity plus two fragile assumptions — the Dockerfile's `--workers 1`
   and a single backend replica. If DOC ever runs 2 workers, 2 replicas, or a
   blue-green pair briefly points two backends at the same writable primary, both can
   pass state-detection and both call `command.upgrade`, deadlocking on migration
   0003's DELETEs or double-applying DDL. The task explicitly asked for "an
   advisory-lock'd boot path."

2. **Revision-id length was an un-enforced invariant.** v2.75.1 crash-looped because
   revision `0004_dji_duration_and_flight_name_restamp` (41 chars) overflowed
   `alembic_version.version_num VARCHAR(32)`: the DDL ran but the stamp rolled back on
   every startup. The lesson lived only in a docstring.

## Decision

**Converge on Alembic as the single schema mechanism (already de facto true) and
harden the boot path with an advisory lock + a revision-id CI fence, rather than a
rewrite.** Concretely, Phase 1:

- **Advisory-lock the whole migration run.** `run_migrations_sync()` opens a dedicated
  connection with `isolation_level="AUTOCOMMIT"`, executes
  `SELECT pg_advisory_lock(_MIGRATION_LOCK_ID)` **before** state detection, runs the
  existing detect → (stamp) → upgrade flow, then executes
  `SELECT pg_advisory_unlock(_MIGRATION_LOCK_ID)` in a `finally` and closes the
  connection (`engine.dispose()` would release it regardless, but the explicit unlock
  is the contract). The lock is **session-scoped** (held across the inner
  `engine.begin()` transaction), and **best-effort released** — an unlock failure is
  logged but never masks the real exception being unwound.

- **`_MIGRATION_LOCK_ID = 8675310`, distinct from seed's `8675309`.** Distinct keys
  mean migration and seed do not serialize against each other unnecessarily; they are
  independent critical sections.

- **Blocking acquire (`pg_advisory_lock`), not `pg_try_advisory_lock`.** A losing racer
  must **wait** for the winner, then re-detect `current == head` and return `"noop"`.
  `try_` would let the loser skip and proceed against an un-migrated schema — the wrong
  failure mode.

- **AUTOCOMMIT on the lock connection** keeps it out of an open transaction (no
  idle-in-transaction) while the session-level lock — which is not transaction-scoped —
  stays held until unlock or connection close.

- **Revision-id ≤ 32-char fence.** A hermetic test walks the migration tree and fails
  if any revision id exceeds 32 chars. This directly prevents the v2.75.1 crash-loop
  class in CI. (Implemented as a test rather than a runtime assert to keep the change
  live-DB-safe and add no startup crash path — the plan scopes runtime-invariant work
  to CI.)

The **ADR-0021 `pg_is_in_recovery()` guard is untouched.** A standby never reaches
`run_migrations_sync`, so the lock is only ever taken on a writable primary.

## Consequences

- **Positive:** Concurrent booters can no longer both run migrations; the migration
  path now matches the seed path's mutual-exclusion posture. The revision-length trap
  is caught in CI, not in a prod crash-loop. No data is altered; the only DB writes are
  whatever Alembic revisions are already pending at head.
- **Negative / cost:** One extra short-lived connection and two round-trips
  (lock/unlock) per boot. A crashed backend that dies *without* running the `finally`
  (e.g. SIGKILL) leaves the lock held only until its DB session closes — Postgres
  releases session advisory locks automatically on backend disconnect, so there is no
  permanent-wedge risk. If a losing racer blocks, it blocks only for the duration of the
  winner's migration (seconds), then no-ops.
- **Scope boundary (deferred):** `_add_missing_columns` / `_create_hot_indexes` are NOT
  removed from `main.py` in this pass. Phase 2 (revision-length + model-vs-head
  autogenerate CI gate) and Phase 3 (freeze the helpers into baseline 0001 and sever
  them from the runtime import graph) remain in the plan for later, lower-urgency work.

## Failover & Resilience self-check

- **Standby safety:** unchanged — the ADR-0021 recovery guard still short-circuits the
  entire write block on `pg_is_in_recovery() = true`; the lock is never taken on a
  standby.
- **Crash safety:** the lock is released on every exit path (`finally`), and Postgres
  auto-releases it on session close, so no crash leaves it permanently held.
- **Rollback:** revert the `db_migrations.py` change; single-worker safety is exactly
  what it was before. CI-fence revert is inert at runtime.

## Verification

- `backend/tests/test_db_migrations.py` — hermetic lock-envelope tests (acquire before
  upgrade, release after, brownfield stamp+upgrade under lock, no-op path still
  locks/unlocks, lock released when `command.upgrade` raises, lock id ≠ seed lock id)
  and the ≤32-char revision fence. `25 passed, 2 skipped` (the two skips are the opt-in
  real-Postgres integration tests, run by exporting `DOC_TEST_PG_URL`).
- Existing `test_startup_recovery_guard.py` unaffected (all green), proving the boot
  path and the ADR-0021 guard still behave.
