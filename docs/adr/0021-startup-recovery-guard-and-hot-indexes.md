# ADR-0021 — Startup schema-write recovery guard + hot-path indexes

- **Status:** Accepted
- **Date:** 2026-06-11
- **Relates to:** Ground-up audit `docs/plans/2026-06-11-ground-up-audit.md`
  findings **P1-1** (FAILOVER-SENSITIVE — startup DDL/seed runs on every boot
  against the live primary, crash-loops on a standby) and **P1-5** (missing
  indexes on hot FK/filter columns). Same startup path
  (`backend/app/main.py` lifespan), shipped together because the index
  creation must live *inside* the same recovery-guarded, primary-only block.

## Context

DroneOpsCommand has **no Alembic**. The de-facto migration mechanism is an
imperative startup block in the FastAPI lifespan (`backend/app/main.py`):
on every backend boot it runs `Base.metadata.create_all`, a large
`_add_missing_columns` batch (`CREATE TABLE` / `ALTER TABLE ADD COLUMN` /
`ALTER TYPE ADD VALUE` / `ALTER COLUMN … TYPE TEXT` / `ADD CONSTRAINT`),
`seed_database()`, optional demo seed, managed-instance admin auto-create,
and a flight→aircraft DB backfill **commit**. Every one of these is a
**write**.

The live topology (CLAUDE.md §Deployment topology) is PostgreSQL streaming
replication: BOS-HQ runs the promoted primary (`droneops-standby-db`), CHAD-HQ
is the failback standby, behind a blue-green swap. The backend's
`DATABASE_URL` routes to that primary. **There was no `pg_is_in_recovery()`
guard anywhere.**

Two problems follow:

1. **Crash-loop on a standby (the failover hazard).** If `DATABASE_URL` ever
   resolves to a node that is still a read-only standby — mid-failover, a
   misconfigured override, or a backend that booted before promotion
   completed — then `engine.begin()` + `create_all`/`ALTER`/seed raises
   *"cannot execute … in a read-only transaction"*. The lifespan aborts, the
   container crash-loops, and it does so during the exact window
   (a site failover) when customer-facing uptime matters most. The
   backend *could* have come up serving read traffic against the standby;
   instead it dies.

2. **No indexes exist** anywhere in the schema except on the TOS-audit table.
   PostgreSQL does **not** auto-create indexes on foreign-key columns (only on
   PRIMARY KEY / UNIQUE constraints), so every hot FK join and the
   client-portal login lookup is a sequential scan that worsens with data
   growth.

## Decision

### 1. `pg_is_in_recovery()` guard around all startup writes

Before the DDL/seed/backfill block, run `SELECT pg_is_in_recovery()` on a
fresh connection (`_is_in_recovery()` in `main.py`):

- **In recovery (standby):** log a clear `WARNING` and **skip the entire
  schema-DDL / seed / backfill block.** Continue startup — directories,
  bundled static assets — and serve **READ traffic**. The backend does not
  crash.
- **Writable primary:** run the schema sync + seed exactly as before
  (`_run_startup_schema_and_seed()`).
- **Fail-safe:** if the recovery probe itself errors, assume **primary**
  (run the writes). Rationale: a healthy primary must never be wrongly
  treated as a standby and left un-migrated. A genuinely unreachable DB has
  already failed `_wait_for_db()`'s `SELECT 1` upstream; a reachable standby
  answers `pg_is_in_recovery()` cleanly.

The schema/seed/backfill body was **extracted verbatim** into
`_run_startup_schema_and_seed()` so the skip/run decision lives in one place
and is unit-testable. Filesystem-only steps (upload/report dir creation,
bundled aircraft-image copy) stay in the lifespan **outside** the guard — a
read-replica backend still serves bundled assets.

**Promotion behaviour (documented, intentional).** The guard is evaluated
**once at startup**. If a backend booted against a standby (writes skipped)
and that node is *later* promoted to primary, the already-running backend
does **not** retroactively run the skipped DDL/seed. The accepted recovery
path is a **restart after promotion**: the blue-green deploy flow recreates
the container (and an operator can trigger a restart), the guard re-evaluates,
sees `pg_is_in_recovery() = false`, and runs the schema sync normally. This is
a deliberate trade-off — re-checking continuously would add a background loop
and a "did I migrate yet?" state machine for a transition that already
triggers a container recreate in this topology.

### 2. Hot-path indexes via `CREATE INDEX IF NOT EXISTS` (primary-only, WAL-replicated)

`_create_hot_indexes()` runs **inside** the same guarded `engine.begin()`
block, after `create_all` + `_add_missing_columns`, issuing four idempotent
`CREATE INDEX IF NOT EXISTS` statements. Because it is inside the recovery
guard it only ever runs on the writable primary; the created indexes
**replicate to the CHAD-HQ failback standby via WAL** (the standby never
issues the DDL itself — it receives the index through replication). That is
precisely why adding indexes here is failover-safe.

**The four indexes (each verified against the real model + query pattern):**

| Index | Column | Why (evidence) |
|---|---|---|
| `ix_mission_flights_mission_id` | `mission_flights.mission_id` | `Mission.flights` back-populated relationship + selectin emits `WHERE mission_flights.mission_id IN (…)` on every mission / financials path; explicit join at `business_signals.py:107`. Plain FK — not auto-indexed. |
| `ix_flights_aircraft_id` | `flights.aircraft_id` | `GROUP BY Flight.aircraft_id` in the maintenance dashboard `/status` aggregate (`maintenance.py:505`, with `WHERE aircraft_id IS NOT NULL`). Full scan on a frequently-polled endpoint. Plain FK. |
| `ix_customers_email` | `customers.email` | Client-portal login lookup `WHERE Customer.email == data.email` (`client_portal.py:159`). Login-path full scan. Plain nullable column, no UNIQUE. |
| `ix_line_items_invoice_id` | `line_items.invoice_id` | `Invoice.line_items` is `lazy="selectin"` (`models/invoice.py:97`) → `WHERE line_items.invoice_id IN (…)` on every invoice load. Plain FK. |

**Indexes considered and REJECTED (already covered — no action):**

- `invoices.mission_id` and `reports.mission_id` are `unique=True`
  (`models/invoice.py:62`; reports model) → PostgreSQL auto-creates a
  UNIQUE index. Adding our own would duplicate it.
- `customers.intake_token` is `unique=True` → already indexed.
- All PRIMARY KEY columns (`id` on every table) → already indexed.

The audit also listed several P2/P3-grade candidates (`flights.start_time`,
`maintenance_records.aircraft_id`, `mission_images.mission_id`,
`missions.customer_id`/`status`/`mission_date`, etc.). This ADR deliberately
ships **only the four P1-grade indexes** with the clearest, hottest evidence,
to keep the lock footprint and review surface minimal. The remainder are
deferred to the Alembic follow-up (below) where they can be reviewed
individually.

**`CREATE INDEX` (non-`CONCURRENTLY`) trade-off.** A plain `CREATE INDEX`
takes a `SHARE` lock that blocks **writes** (not reads) on the target table
for the build duration. At current table sizes (single-operator fleet —
thousands of rows, not millions) each build is sub-second, so the brief
write-lock is acceptable and lets the indexes be built inside the existing
transactional startup block alongside `create_all`. `CREATE INDEX
CONCURRENTLY` would avoid the write-lock but **cannot run inside a transaction
block** and cannot be combined with the other DDL in one `engine.begin()`; it
needs its own autocommit connection and leaves an `INVALID` index on failure
that must be dropped manually. The threshold to revisit (switch to
CONCURRENTLY, or move to Alembic) is any of these tables growing past ~1M
rows. Each statement is best-effort per index (a missing table logs a warning
and the rest still build) so the index pass can never abort startup.

## Alternatives considered

- **Adopt Alembic now (rejected for this change; recorded as future work).**
  The structurally correct fix is to retire the imperative
  `_add_missing_columns` startup block in favour of Alembic with reviewed,
  blue-green-aware (expand/contract) migrations, and to create indexes via
  `op.create_index(..., postgresql_concurrently=True)` in a versioned
  migration run by a single one-shot deploy job rather than every
  backend/worker/beat boot. **Rejected for now because the repo has no
  Alembic at all** — introducing it is a multi-step migration of its own
  (baseline the current schema, wire the deploy job, gate DDL behind a single
  runner) and is out of scope for a failover-hardening change. The
  `pg_is_in_recovery()` guard is the cheap, immediate fix that stops the
  crash-loop today; Alembic is the follow-up that also lets the deferred
  P2/P3 indexes land reviewed and individually. **Future work.**

- **Gate DDL behind a `RUN_MIGRATIONS=true` env flag** so only one designated
  container mutates schema. Complementary to the recovery guard (it solves
  "every worker/beat re-runs the DDL", a different concern) but does **not**
  solve crash-loop-on-standby on the one container that *does* run it. Worth
  doing alongside Alembic; not a substitute for the recovery guard. Deferred.

- **Continuously re-check recovery and run the skipped migration on
  promotion.** Rejected — adds a background loop and migration-state tracking
  for a transition that already triggers a container recreate in the
  blue-green topology. Restart-after-promotion is simpler and sufficient.

## Failover & Resilience self-check (CLAUDE.md §Failover Guard — MANDATORY)

1. **Will this break PostgreSQL streaming replication?** No — it *reduces*
   risk. The guard ensures DDL only ever runs on the writable primary; indexes
   replicate to the standby via WAL like any other primary change. No port,
   pg_hba, or connection-string changes.
2. **Will this survive a container recreation?** Yes — the guard re-evaluates
   on every boot. On a recreate against the primary it runs the (idempotent)
   schema sync; against a standby it skips and serves reads. The hot indexes
   are `IF NOT EXISTS`, so a recreate never double-creates.
3. **Will this break the blue-green swap flow?** No — it *hardens* it. A
   standby-first deploy that boots the new image against a not-yet-promoted
   standby no longer mutates schema before the swap (and no longer crash-loops);
   it waits and serves reads until promotion + restart. Additive-only indexes
   carry no breaking-DDL risk.
4. **Will this break the failover engine?** No. It changes only the backend's
   own startup behaviour; quorum/health/WireGuard are untouched. It removes a
   backend crash-loop that previously *worsened* a failover.
5. **Will this affect any customer-facing service during a site failover?**
   Yes — **positively.** Before: a backend booting against a standby
   crash-looped → outage. After: it comes up serving read traffic and is ready
   to serve writes the moment it is restarted against the promoted primary.

## Consequences

- A backend that boots against a read-only standby no longer crash-loops; it
  serves read traffic and logs a clear `WARNING` instructing a
  restart-after-promotion.
- The four hottest FK/login lookups are now index-backed; full scans on the
  Financials dashboard, the maintenance `/status` aggregate, invoice loads,
  and client-portal login are eliminated and stay flat as data grows.
- The startup write block is now a single extracted, testable function. New
  startup writes MUST go inside `_run_startup_schema_and_seed()` (guarded) —
  not the unguarded filesystem tail of the lifespan.
- Hot-path index changes still live in imperative startup code, not versioned
  migrations. The Alembic follow-up remains the path to retire this pattern.

## Verification

- `cd backend && python3 -m pytest tests/ -q` → **355 passed, 1 skipped**
  (was 347 passed + 1 skipped; +8 new tests in
  `tests/test_startup_recovery_guard.py`).
- New tests prove: `_is_in_recovery()` returns the probe boolean and
  fails-safe to primary on error; the lifespan **skips** the schema/seed block
  when in recovery and **runs** it on a primary; `_create_hot_indexes()`
  issues exactly the four `CREATE INDEX IF NOT EXISTS` statements and is
  best-effort per index.
