# ADR-0004 — Performance Audit Baseline (2026-04-24)

**Status:** proposed (becomes `accepted` once aegis lands FIX-1..4 and appends AFTER measurements)
**Date:** 2026-04-24
**HEAD at audit:** `e0295a1` (v2.63.6)
**Related plan:** `docs/plans/2026-04-24-perf-audit.md`
**Live host:** BOS-HQ (10.99.0.4)

---

## Context

Bill reported user-perceived lag on DroneOpsCommand despite running on a high-end VPS (BOS-HQ, 32 GiB RAM, modern CPU). Asked for a measured audit, root-cause findings, and a small set of high-leverage fixes — explicitly NOT a redesign.

This ADR captures the BEFORE-state measurements so subsequent audits have a fixed reference point. The fix-list itself is in the companion plan.

---

## Audit methodology

All measurements taken from BOS-HQ via SSH + Docker exec into running containers, not synthetic load. Production observability data sourced from the existing JSON-log middleware at `backend/app/main.py:455-470`.

Five lines of evidence:
1. Backend RES log timing (every endpoint logs `RES METHOD PATH STATUS X.XXs`).
2. Postgres extension + size + index inventory (live psql).
3. Container resource snapshots (`docker stats --no-stream`).
4. Frontend bundle inventory (live `ls -lh /usr/share/nginx/html/assets/`).
5. Static-analysis grep across the codebase for sync-in-async, sequential awaits, missing `Promise.all`, missing `lazy()`, missing `index=True`, missing cache.

Full raw data is preserved verbatim in §2 of the companion plan.

---

## BEFORE measurements (2026-04-24, 23:00 UTC)

### Host
- Load avg: 0.34, 0.85, 0.86 — uncontested.
- Uptime: 3 days, 14 hours.

### Container memory (steady state)
| Container | CPU | Mem |
|---|---|---|
| droneops-backend-1 | 0.22% | 874 MiB |
| droneops-worker-1 | 0.52% | 536 MiB |
| droneops-standby-db | 2.90% | 76 MiB |
| droneops-redis-1 | 0.35% | 3.5 MiB |
| droneops-frontend-1 | 0.00% | 8.0 MiB |

### Postgres
- Database size: **52 MB**.
- Top table: `flights` 4 rows, 43 MB (gps_track JSON column).
- All other tables: 0 rows except `battery_logs` (4 rows).
- Extensions: `plpgsql` only (`pg_stat_statements` not loaded).
- Indexes: 26 across 16 tables, all PK + auto-from-UNIQUE; **zero `index=True` on any model column**.

### SQLAlchemy pool
- `pool_size=5, max_overflow=10` — total ceiling 15.

### Backend latency from inside the container
- `/api/health` p50 = **2 ms**.
- Backend floor is healthy.

### Production endpoint latency (RES log sample, real users)
- Fastest cluster: `/api/health 0.00s`, `/api/branding 0.00s`.
- Mid cluster (heavy fan-out, pool-saturated): `/api/missions`, `/api/maintenance/next-due`, `/api/flight-library/stats/summary` all hovering 0.5–0.6s.
- Outlier: `/api/weather/current` measured **8.31s** and **7.44s** in the same minute.

### Frontend bundle
- `index-*.js`: **1.9 MB** (single chunk for all 17 main pages).
- `index-*.css`: 251 KB.
- `pdf.worker.min-*.mjs`: 1022 KB (correctly lazy).
- Client portal pages: 1.9–6.6 KB each (correctly lazy).
- Total: 3.2 MB across all assets.

### Frontend fetch density
- `Settings.tsx` issues **34 separate `api.get()` calls** in a single `useEffect`, no `Promise.all`.
- No React Query / TanStack Query / SWR in `package.json`.
- No client-side cache layer; every page mount re-fetches.

### SQLAlchemy relationships
- Already correctly tuned (selectin/noload chosen per relation). Not a finding.

### Sync I/O in async path
- Clean. Only one sync `httpx.Client` exists, in a Celery worker (not the FastAPI request path).

---

## Decision

Adopt the four-fix plan in `docs/plans/2026-04-24-perf-audit.md`:

1. **F-1** Parallelize `weather/current` with `asyncio.gather` + add 5-min Redis cache (failure-open).
2. **F-2** Raise async DB pool from 5+10 to 20+20; add 60s in-memory cache around `get_current_user`.
3. **F-3** Convert all 17 main pages to `lazy()` + add Vite `manualChunks` for vendor splits.
4. **F-4** Add a custom `useApiCache` hook (≤80 lines) with TTL-and-invalidate semantics; apply to Dashboard, Flights, Settings reads only.

Explicitly **rejected**:
- TanStack Query repo-wide adoption (too large for this audit; reduces gain from F-3).
- Index additions (zero measurable gain at current data sizes; deferred to ROADMAP).
- Postgres `pg_stat_statements` (requires postmaster restart, operator decision).
- Sub-component refactor of `Dashboard.tsx` (~1163 lines, single component) — gain too small to ship in this audit.

---

## Rationale

The plan ships in five small commits, each independently revertable. The four fixes target the four observed root causes — weather serial fan-out, pool saturation, bundle bloat, no client cache — in priority of user-impact. Indexes and database tuning are deliberately not in scope because the database is 52 MB with empty most tables; there is no current measurable gain.

Per Failover Guard (`/home/bbarnard065/droneops/CLAUDE.md`):
- No schema change.
- No replication settings change.
- No port binding, pg_hba, or connection-string change to `db` / `standby-db`.
- No quorum, fencing, or WG impact.
- Pool sizing and Redis caching survive container restart (failure-open + module-level config).

Per repo CLAUDE.md "Repair & Fix Quality Standard":
- Each fix has a verification command (§6 of plan).
- Each fix has a rollback procedure (`git revert` only — no DB / config / volume change).
- Each fix bumps the version in all 4 required files.
- Each fix logs enough to diagnose its own next failure.

---

## Consequences

### Positive
- Dashboard cold first-paint p95 estimated 9.5s → 2.5s.
- Dashboard warm repeat-visit p95 estimated 7.5s → 150ms.
- Settings p95 first-paint estimated 3.2s → 600ms.
- 30-parallel-burst test: estimated 1.5–3s → <700ms.
- Bundle main chunk: 1.9 MB → estimated <600 KB on first paint, vendor chunks cached separately by CF.

### Negative / accepted
- Token revocation latency up to 60s (FIX-2 user cache TTL). Acceptable for self-hosted single-operator deployment.
- Stale-read window up to 30s on client cache (FIX-4). Mutations explicitly invalidate.
- Adds Redis as a hard dependency for the weather endpoint, but with failure-open fallthrough — Redis down means slow weather, not broken weather.
- Five separate version bumps in one session (v2.63.7 → v2.63.11 if FIX-5 is its own commit).

---

## Research sources

- FastAPI async best practices — official docs (concurrent calls with `asyncio.gather`).
- SQLAlchemy 2.x async pool tuning — official docs (`create_async_engine`, `pool_size` semantics).
- Vite 6 build options — official docs (`build.rollupOptions.output.manualChunks`).
- React 18 `lazy()` + `Suspense` — React docs (already in production use in this repo's client portal).
- `httpx.AsyncClient` — official docs.
- Existing repo conventions and prior ADRs:
  - `docs/adr/0001-observability.md` (logging contract reused unchanged).
  - `docs/adr/0002-droneopssync-upload-auth.md` (no overlap).
  - `docs/adr/0003-zero-touch-device-key-rotation.md` (no overlap).
- Bill's standing rules from `~/.claude/CLAUDE.md` and `~/.claude/projects/.../MEMORY.md`:
  - `feedback_code_splitting.md` — code-splitting is mandatory; F-3 satisfies this.
  - `feedback_no_deferred_fixes.md` — no stopgaps; each fix lands shippable.
  - `feedback_prevent_failures.md` — Redis cache is failure-open; pool sizing is in `database.py`, survives restart.
  - `feedback_always_push.md` / `feedback_always_deploy.md` — NOC autopull deploys.

---

## AFTER measurements (post-fix)

> _Aegis: append the §6 acceptance block from the plan here once FIX-1..4 are deployed and the verification commands have been run. Flip the Status field at the top from `proposed` to `accepted` only after all three acceptance thresholds in §6 of the plan have passed._

```text
[ pending — populated by aegis post-deploy ]
```
