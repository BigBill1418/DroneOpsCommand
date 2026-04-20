# DroneOpsCommand — In-Flight Work

Maintained alongside `CHANGELOG.md` and `docs/adr/`. `CHANGELOG.md` is
the ledger of shipped changes; this file tracks what's in-flight or
blocked.

## 2026-04-20 — Maintenance type vocabulary unified (v2.63.3)

Fixes a long-standing bug where overdue schedule alerts (Compass
Calibration et al.) could not be cleared via "+ Log Maintenance".

- Frontend `MAINTENANCE_TYPES` now mirrors backend
  `DJI_MAINTENANCE_DEFAULTS` exactly — 10 DJI categories + `General
  Service` + `Other`, Title-Case as both value and label.
- Migration script `scripts/migrate_maintenance_type_vocab.py` rewrites
  legacy snake_case record rows → canonical Title-Case. Idempotent.

**Status** — HSH-HQ prod: v2.63.3 live `2026-04-20`, 5 legacy records
remapped to Title-Case via the migration script. User logs a Compass
Calibration record per affected aircraft through the UI to clear the
three overdue schedules (now possible because the dropdown has the
option and the backend schedule-match will fire).

**Deferred — CHAD-HQ demo:** still on `dfad0a3`. `git pull` blocked by
uncommitted operational fixes on `docker-compose.demo.yml`,
`docker-compose.standby.yml`, `.env.demo` (port-binding hardening +
primary_conninfo IP correction). Demo has **zero maintenance records**
so the migration would be a no-op there anyway. The frontend fix for
demo can land when someone reconciles the uncommitted compose edits
— scope for a separate session.

## 2026-04-19 — Zombie-leak incident (RESOLVED)

Completed: zombie-leak fixes + Redis-heartbeat healthcheck.

- **v2.63.2** (commit `897c78a`) — Redis-heartbeat docker healthcheck.
  Replaces `celery inspect ping` subprocess (which re-imported the full OTel
  chain every 60s) with a lightweight Redis age check. Worker's
  `worker_heartbeat` signal writes unix-ts to `droneops:worker:heartbeat`
  (120s TTL); healthcheck is `redis-cli GET + age < 60s`. Interval 30s,
  timeout 5s, start_period 30s. Fast path, resilient to Redis brief outages.

- **Ops** (commit `98f7309`) — Backend zombie-leak fix (follow-up).
  Investigation found 3 fresh `<defunct>` curl children accumulating under
  uvicorn master. Same SIGCHLD reap leak pattern as worker, different
  container. Added `init: true` (tini PID 1) to backend service in compose.

- **Ops** (commit `9ae3c95`) — Worker zombie-leak fix (primary).
  HSH-HQ high-load incident found 33 defunct celery children accumulating
  ~2/hr over 18h. Root cause: celery prefork master loses occasional SIGCHLD
  reaps on Python 3.12. Added `init: true` (tini) + `--max-tasks-per-child=50`
  to worker service. Per-child task cap keeps leaked children short-lived;
  tini as PID 1 makes the leak structurally impossible.

### Repair quality
- All changes compose-only; no application code touched.
- Failover-safe: per-container health signals, no cross-container state.
- Docker inspect: confirms `redis-cli` present in new backend image.
- Roundtrip tested: SETEX/GET on redis:7 (stack image).
- Incident log: `~/noc-master/docs/incidents/2026-04-19-hsh-hq-high-load.md`.

## 2026-04-18 — Observability Phase 5 (COMPLETE on code side)

Ships in two functional commits + one doc commit:

- **v2.63.0** (commit `6b7e626`) — structured JSON logging pre-req.
  Replaces plain `logging.basicConfig` with `python-json-logger` on
  root + Celery `after_setup_logger` signals.
- **v2.63.1** (commit `d4df8e7`) — Sentry + OTel SDKs + compose labels.
  Backend `app/observability/` package, frontend `src/lib/sentry.ts`,
  `com.barnardhq.*` labels on every service, demo override pins CHAD-HQ
  Alloy + `env=demo`.
- **docs** (this commit) — ADR-0001, PROGRESS.md, CHANGELOG.md entries.

### Deploy + verification

1. NOC Master (swarmpilot) picks up the push on `main` and runs the
   prod build/up sweep on HSH-HQ.
2. Demo build on CHAD-HQ: `cd ~/droneops && ./bootstrap.sh` (or the
   documented `docker compose -p droneops-demo -f docker-compose.yml -f
   docker-compose.demo.yml --env-file .env.demo up -d --build`).
3. Verify on BOTH hosts per `reference_droneops_topology.md`:
   - Loki: `{service="droneops-api",env="prod",host="hsh-hq"}` +
     `{service="droneops-api",env="demo",host="chad-hq"}` return JSON
     lines.
   - GlitchTip: issue in `droneops` project tagged `env=prod` and
     `env=demo` (separate).
   - Tempo: `service.name=droneops-api` trace on an `/api/health` hit.
   - `docker exec <container> curl localhost:8000/api/health` returns
     200 on both hosts.

### Env vars required at deploy time

On each host's `.env` / `.env.demo`:

- `SENTRY_DSN` — from
  `/home/bbarnard065/.secrets/observability-dsns.env::DRONEOPS_API_SENTRY_DSN`.
- `VITE_SENTRY_DSN` — from that same file::`DRONEOPS_FRONTEND_SENTRY_DSN`.
- `OTEL_EXPORTER_OTLP_ENDPOINT=http://10.99.0.1:4317` on HSH-HQ,
  `http://10.99.0.2:4317` on CHAD-HQ (or leave the demo override's
  default).

Unset = no-op. Nothing in the app code fails if these are absent.

## Follow-ups

- **Companion APK instrumentation.** Per
  `feedback_droneops_companion_apk.md`, the Android companion at
  `~/droneops/companion/` (Kotlin) is not instrumented in Phase 5. If
  the companion needs `SentryAndroid.init`, that's a separate commit +
  an APK rebuild + release — flagged for the user, not scoped here.
- ~~**Managed-hosting branch.**~~ Resolved: `managed-hosting-v2` merged
  `2026-04-10` as v2.62.0 (merge commit `85f28e9`). `.env.example` already
  carries the observability block (`SENTRY_DSN`, `OTEL_EXPORTER_OTLP_ENDPOINT`,
  `VITE_SENTRY_DSN`, etc.), so managed instances get the hooks by default;
  operators just paste the DSN.
- **Dashboards (Aegis-F / Phase 7).** DroneOps-specific Grafana
  dashboards aren't in scope for this phase; Aegis-F is planning them.
