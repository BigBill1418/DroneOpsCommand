# ADR-0022 — Alembic adoption (baseline + brownfield stamp), deferred indexes, and the health-gate trim

- **Status:** Accepted
- **Date:** 2026-06-11
- **Relates to:** Ground-up audit `docs/plans/2026-06-11-ground-up-audit.md`
  findings **P1-1** (the structural Alembic follow-up explicitly called out
  in the recommended execution order: "Alembic as the structural follow-up"),
  **P1-5 / P2/P3 indexes** (FU-8 #6), and **P3-1** (health-gate trim).
  `ROADMAP.md` **FU-8 #2** (adopt Alembic) and **FU-8 #6** (deferred
  indexes). Builds directly on **ADR-0021** (the `pg_is_in_recovery()`
  startup recovery guard + the four hot-path indexes), which remains the
  prior art and whose semantics this ADR preserves intact.

## Context

DroneOpsCommand had **no Alembic in use**. The de-facto schema-migration
mechanism was an imperative block in the FastAPI lifespan
(`backend/app/main.py`): on every primary boot it ran
`Base.metadata.create_all` + `_add_missing_columns` (a large additive
`ALTER` / `ALTER TYPE ADD VALUE` / `ALTER COLUMN … TYPE TEXT` / `ADD
CONSTRAINT` batch) + `_create_hot_indexes` (the four ADR-0021 indexes),
then seed / demo-seed / admin-create / a flight→aircraft backfill commit.

ADR-0021 hardened this against the failover hazard: it wrapped the whole
write block in a `pg_is_in_recovery()` guard so a backend that boots against
a read-only standby **skips** the writes and serves reads instead of
crash-looping, and it extracted the block into a single testable
`_run_startup_schema_and_seed()`. ADR-0021 explicitly recorded **Alembic as
future work** — the imperative schema mechanism, while now failover-safe,
still:

- has no migration history, no review surface, and no down-path;
- cannot express a non-additive change (rename / type-narrow / drop)
  safely — the audit P1-1 flagged that one such change shipped through this
  block would hit the live primary at container-recreate with no gate;
- keeps DDL inline in application code rather than in versioned, reviewable
  artifacts.

`alembic==1.14.0` and `psycopg2-binary==2.9.10` were already pinned in
`requirements.txt` (installed but unused). This ADR adopts Alembic for real.

This ADR also closes the **P3-1** health-gate defect (Stripe coupled to the
container-liveness 503) because the change lives in the same `main.py`
lifespan/health surface and is cheap to land alongside.

## Decision

### 1. Adopt Alembic as the startup schema mechanism (FU-8 #2)

A new `backend/alembic/` tree (`env.py`, `script.py.mako`, `versions/`) plus
`backend/alembic.ini`. Key choices:

- **URL resolution.** `env.py` resolves the DB URL at runtime from
  `app.config.settings.database_url` and strips the `+asyncpg` driver suffix
  so migrations run over a **synchronous psycopg2** connection (Alembic's
  migration context is synchronous; the app's runtime engine stays async).
  `sqlalchemy.url` in `alembic.ini` is intentionally **blank** — the single
  source of truth for the connection string stays in env / app config, per
  the fleet "secrets live in env, never tracked files" convention.

- **`target_metadata = Base.metadata`** with every model imported, so
  `alembic revision --autogenerate` and the validation tests see the full
  schema.

- **Migration-managed indexes excluded from autogenerate.** The hot-path
  indexes are created imperatively (not declared `index=True` on the models),
  so without a filter every future `revision --autogenerate` would propose
  to DROP them. `env.py._include_object` excludes the named ADR-0021 +
  ADR-0022 indexes from comparison. Declaring one as a model index later
  means removing its name from that set.

### 2. Baseline migration `0001_baseline_schema` — captures the live schema EXACTLY

The baseline must equal the schema the **legacy startup path** produced —
not just what `create_all` alone produces (which omits the `ALTER`-only
changes such as the widened `maintenance_type` columns and the deposit
CHECK constraints, and the four hot indexes). Hand-transcribing the full
schema would inevitably drift from the models + the ALTER batch and silently
produce a baseline that does **not** match production.

Therefore `0001.upgrade()` **reuses the exact production functions**:
`Base.metadata.create_all(bind=conn)` + `app.main._add_missing_columns(conn)`
+ `app.main._create_hot_indexes(conn)` against the Alembic-provided sync
connection. The schema is identical to the live schema **by construction**.
This is why the legacy helpers in `main.py` are **retained** (the migration
imports them) even though they no longer drive startup. All three are fully
idempotent, so even an accidental re-run is a no-op.

### 3. Brownfield stamp strategy — the unattended-deploy-critical path

The live BOS-HQ database already has the entire schema (built by the legacy
path across dozens of releases) but **no `alembic_version` table**. Running
`upgrade head` blind would try to re-create existing tables. The programmatic
runner (`app/db_migrations.run_migrations_sync`) handles **both** correct
entry states:

| State | Detection | Action |
|---|---|---|
| **Fresh / empty** (new install, CI, test DB) | no `alembic_version` AND no sentinel table (`missions`) | `upgrade head` builds everything from 0001 |
| **Brownfield** (live prod) | no `alembic_version` AND sentinel `missions` **present** | `alembic stamp 0001_baseline_schema` (records baseline as applied **without** running its DDL) → then `upgrade head` runs only 0002+ |
| **Already managed, pending** | `alembic_version` present, current ≠ head | `upgrade head` applies pending revisions |
| **Already at head** | `alembic_version` present, current == head | `noop` |

Detection (read-only inspect) and the act (stamp+upgrade) run in two phases;
the act runs inside **one** `engine.begin()` transaction that commits on
success, sharing that connection with `env.py` so brownfield stamp+upgrade is
atomic. (A caller-supplied Alembic connection is **not** auto-committed —
without an owning transaction the whole migration silently rolls back on
connection close under SQLAlchemy 2.0. This was caught and fixed during
real-Postgres validation.)

The runner returns a string action (`upgraded:fresh` / `stamped+upgraded` /
`upgraded` / `noop`) for honest logging and test assertions.

### 4. Startup integration — recovery guard preserved byte-for-byte

`_run_startup_schema_and_seed()` (the ADR-0021 primary-only function) now
calls `run_migrations_sync` **instead of** the inline `create_all` /
`_add_missing_columns` / `_create_hot_indexes` triple. Everything else in
that function — seed, demo-seed, managed-instance admin auto-create, the
flight→aircraft backfill — is **unchanged**.

The ADR-0021 guard in the lifespan is **untouched**:
- **Standby / in recovery →** skip the entire block, log the WARNING, serve
  READ traffic. (Migrations never run on a standby; indexes still replicate
  to the CHAD-HQ failback standby via WAL from the primary.)
- **Writable primary →** run `_run_startup_schema_and_seed()` (now Alembic).
- **Probe error →** fail safe to primary.
- **Promotion behaviour →** restart-after-promotion re-runs, same as ADR-0021.

The migration runner is **synchronous** (psycopg2). It is dispatched via
`await loop.run_in_executor(None, run_migrations_sync)` so it never blocks the
event loop — the same house discipline applied to the backup subprocess /
Stripe SDK / PIL offloads elsewhere in the audit.

### 5. Second migration `0002_p2_p3_indexes` — deferred indexes (FU-8 #6), verified

Each candidate from audit P1-5's deferred list was checked against a **real
query pattern** in the current routers before inclusion. Added:

| Index | Column | Evidence |
|---|---|---|
| `ix_flights_start_time` | `flights.start_time` | DEFAULT list sort key (`flight_library.py:539`) + date-range filter (`:571`/`:580`) + per-pilot sort (`pilots.py:168`). Hottest unindexed sort/filter on the largest table. |
| `ix_flights_created_at` | `flights.created_at` | UNIVERSAL list tiebreaker appended to every flight-library sort (`:554`) + recent-activity feeds (`:489`, `:644`). |
| `ix_missions_customer_id` | `missions.customer_id` | FK; dup-detect + customer joins (`missions.py:184`). Not auto-indexed. |
| `ix_missions_status` | `missions.status` | Business-signals dashboard status filters on every poll (`business_signals.py:96,103,134,137`). |
| `ix_mission_images_mission_id` | `mission_images.mission_id` | FK; `Mission.images` selectin + scalar count (`missions.py:649`). |
| `ix_maintenance_records_aircraft_id` | `maintenance_records.aircraft_id` | FK; per-aircraft history filter (`maintenance.py:74`). |
| `ix_maintenance_schedules_aircraft_id` | `maintenance_schedules.aircraft_id` | FK; per-aircraft schedule lookups (`maintenance.py:285,317,434`). |

**REJECTED (unjustified against real patterns):**

- `missions.mission_date` — only used in an `ORDER BY` over an
  already-`id IN (...)`-bounded client-portal set (`client_portal.py:233`);
  the IN-list prunes to a handful of rows first, so the sort doesn't need an
  index.
- `flights.pilot_id` — the pilot-flights list is already FK-bounded to one
  pilot and small; its sort is served by `ix_flights_start_time`.
- `flights.source` / `flights.drone_model` — used only in `ILIKE '%…%'`
  leading-wildcard search predicates, which a B-tree index cannot serve;
  would require a trigram (`pg_trgm`) index — out of scope.
- Anything already covered: `invoices.mission_id` / `reports.mission_id`
  (auto-created UNIQUE indexes), `line_items.invoice_id` /
  `mission_flights.mission_id` / `flights.aircraft_id` / `customers.email`
  (ADR-0021).

All `CREATE INDEX IF NOT EXISTS` (idempotent, safe on a hand-indexed prod
DB), plain (non-CONCURRENTLY) for the same reason ADR-0021 gave.

### 6. Health-gate trim (audit P3-1)

`/api/health` previously set `degraded=True` (→ 503) on a Stripe probe error.
A Docker healthcheck treats 5xx as fail → `restart: unless-stopped` would
recreate a perfectly-serving API because **Stripe** had an outage or a bad
key. Fixed: Stripe status + error stay in the response **body** for
observability, but **only DB + Redis** drive the `degraded` flag / 503. A
payment provider can no longer restart the API. The existing
`test_health_returns_503_when_stripe_fails` was inverted to
`test_health_stays_healthy_when_stripe_fails`.

## Why startup-runs-migrations is retained (vs a deploy-time job)

The audit P1-1 fix ladder ended at "gate DDL behind a single runner / a
one-shot deploy job, then Alembic." We adopt Alembic but **keep migrations
running from the backend startup path** (primary-only, recovery-guarded)
rather than a separate deploy-time job. Trade-off, recorded deliberately:

- **For startup-runs-migrations (chosen):**
  - Zero new moving parts in the blue-green deploy flow. The deployer
    (`noc-master`, ADR-0018) recreates the backend container; the migration
    runs as part of that recreate, already inside the ADR-0021 recovery
    guard that makes it primary-only. No new job to schedule, monitor, or
    fail independently of the app.
  - The recovery guard already solves the "runs against a standby" hazard
    that a deploy job would *also* have to re-solve.
  - The runner is idempotent + offloaded; `noop` on an already-migrated DB is
    cheap, so the per-boot cost is negligible.
  - Multi-worker safety: the runner detects state and `noop`s; `seed.py`
    already takes a PG advisory lock for the seed step.

- **Against (the cost we accept):**
  - Every primary backend boot evaluates migration state (a cheap inspect +
    a possible `noop`). A dedicated one-shot job would run migrations exactly
    once per deploy instead of once per container.
  - A genuinely long migration would extend the backend's startup window
    (mitigated today: indexes are sub-second at current table sizes; the
    `start_period` already has slack, and audit P3-3 tracks bumping it).

**Revisit when:** a migration is expected to take longer than the healthcheck
`start_period` slack, OR multiple independently-deployed services need to
share one migration run. At that point, move to a single one-shot deploy job
(`RUN_MIGRATIONS=true` on one container, or a deployer step) — the runner is
already a standalone callable (`run_migrations_sync`) that a job can invoke
unchanged.

## Real-Postgres validation (evidence)

Validated against a disposable `postgres:16-alpine` container during
development; reproducible via the opt-in integration tests
(`tests/test_db_migrations.py`, gated on `DOC_TEST_PG_URL`):

- **(a) Fresh empty DB → `upgrade head`:** action `upgraded:fresh`, reaches
  head `0002_p2_p3_indexes`, 16 `ix_*` indexes present.
  `alembic.autogenerate.compare_metadata` vs `Base.metadata` → **EMPTY
  diff** (the only pre-filter diffs were the migration-managed indexes,
  excluded by `_include_object`).
- **(b) Brownfield:** schema built the legacy way
  (`create_all` + `_add_missing_columns` + `_create_hot_indexes`, **no**
  `alembic_version`); runner detects brownfield → `stamped+upgraded`
  (stamps `0001` without DDL, then upgrades to `0002`); **EMPTY diff** vs
  `Base.metadata`; head == `0002_p2_p3_indexes`; second run == `noop`.
- Both paths **converge** to identical state.
- `alembic history` CLI shows the linear tree
  `<base> → 0001 → 0002 (head)`.

## Failover & Resilience self-check (CLAUDE.md §Failover Guard — MANDATORY)

1. **Will this break PostgreSQL streaming replication?** No. DDL runs only
   on the writable primary (the ADR-0021 recovery guard is preserved
   byte-for-byte); migrations and their indexes replicate to the CHAD-HQ
   failback standby via WAL exactly like any other primary change. No port /
   pg_hba / connection-string changes. The runner strips `+asyncpg` to a
   psycopg2 URL but resolves it from the **same** `settings.database_url`, so
   it targets the same node the app already talks to.
2. **Will this survive a container recreation?** Yes — and more robustly than
   before. On recreate against the primary the runner detects state
   (`noop` if already at head, `upgraded` if a new revision shipped). The
   brownfield branch makes the **first** post-adoption recreate correct: it
   stamps the existing prod schema rather than trying to rebuild it. All DDL
   is `IF NOT EXISTS` / additive, so a recreate never double-creates.
3. **Will this break the blue-green swap flow?** No — it hardens it. A
   standby-first deploy that boots the new image against a not-yet-promoted
   standby still skips all writes (ADR-0021 guard) and serves reads; it does
   not run migrations until promotion + restart. Alembic now also gives a
   review surface + down-path so a future non-additive change can be made
   expand/contract-safe instead of shipping breaking DDL inline.
4. **Will this break the failover engine?** No. It changes only the backend's
   own startup schema mechanism; quorum / health / WireGuard are untouched.
   It removes nothing from the ADR-0021 crash-loop protection.
5. **Will this affect any customer-facing service during a site failover?**
   Positively / neutrally. The recovery guard behaviour is identical to
   ADR-0021 (standby → serve reads, no crash-loop). The health-gate trim
   additionally removes a self-inflicted restart vector (Stripe outage →
   503 → container restart), so the API stays up through a payment-provider
   outage.

## Consequences

- Schema changes are now versioned, reviewable Alembic migrations with a
  down-path. The next non-additive change can be expressed expand/contract.
- The legacy `_add_missing_columns` / `_create_hot_indexes` helpers remain in
  `main.py` **only** because the baseline migration imports them to reproduce
  the exact live schema. New schema changes go in a **new migration**, not in
  those helpers. (Retiring them fully means hand-authoring the baseline DDL,
  deferred as cosmetic.)
- The brownfield stamp runs **once** on the live DB at the next deploy, then
  every subsequent boot is `noop` / `upgraded`. This path is the
  unattended-critical one and is the most-tested (hermetic dispatch tests +
  real-PG integration test).
- Seven additional hot-path indexes back the flight-library list/sort, the
  business-signals dashboard, mission-image counts, and maintenance lookups.
- A Stripe outage can no longer restart the API.
- New `psycopg2-binary` is now a **runtime** dependency (migration
  connection), not just transitive — already pinned, documented in
  `requirements.txt`.

## Verification

- `cd backend && python3 -m pytest tests/ -q` → **0 failures** (suite green;
  the integration tier in `tests/test_db_migrations.py` skips unless
  `DOC_TEST_PG_URL` is set). New coverage:
  `tests/test_db_migrations.py` (tree shape, brownfield dispatch, executor
  offload, + opt-in real-PG fresh/brownfield end-to-end) and the inverted
  `tests/test_health_check.py::test_health_stays_healthy_when_stripe_fails`.
- Real-Postgres evidence above (run during development against
  `postgres:16-alpine`).

## Operator manual-verification plan (BOS demo stack)

Docker was available in the dev environment, so both paths were proven
against a real Postgres. To re-confirm on the BOS demo stack before the prod
deploy:

1. On the demo DB (a copy of prod shape, brownfield — no `alembic_version`):
   deploy this change, watch the backend startup log for
   `MIGRATIONS: brownfield DB detected … stamping baseline … then upgrading`
   followed by `STARTUP: Alembic migrations applied (stamped+upgraded)`.
2. `psql -c "SELECT version_num FROM alembic_version;"` → `0002_p2_p3_indexes`.
3. `psql -c "\di ix_*"` → the 11 hot indexes present.
4. Restart the backend → log shows
   `MIGRATIONS: schema already at head … no migration needed` /
   `applied (noop)`.
5. `curl -s localhost/api/health | jq` with a deliberately-bad Stripe key →
   `status: "healthy"`, `stripe: "error"`, HTTP **200** (not 503).
