> **Maintained automatically by NOC doc-autogen.** This file is refreshed twice daily (04:00 + 16:00 UTC) by `~/noc-master/scripts/doc-autogen.py`, which summarizes recent commits via Claude Haiku 4.5 and commits with a `[skip-deploy]` trailer so no container rebuilds are triggered. See [NOC-Master ADR-0013](https://github.com/BigBill1418/NOC-Master-Control-SWARM/blob/main/docs/decisions/ADR-0013-docs-only-deploy-skip.md). Manual edits are preserved — the generator diffs against existing content before writing.

# Changelog

Notable changes to DroneOpsCommand. Dates are absolute (YYYY-MM-DD, UTC).

## 2026-04-24 — DroneOpsSync upload auth — root-cause CORRECTION (ADR-0002 §4.1)

The v2.63.4 commit (`890b875`) hypothesized a stale pre-v2.33 Gson APK as the root cause of Bill's 403 upload. **That hypothesis was wrong.** Bill challenged it and `git show ab32335:companion/src/sync.ts` proved v2.61.5 (the APK actually on his RC Pro per memory) already posts to `/api/flight-library/device-upload` with `X-Device-Api-Key`.

Second-pass diagnosis verified on BOS-HQ production:

- Backend v2.63.4 running, healthy. CF Access Intake app `bypass/everyone` on `/api/flight-library/device-*` — no IdP challenge. No WAF/Transform rule strips the device header.
- Direct POST with a valid key → HTTP 200 `FlightUploadResponse`. Direct POST with bogus key → 401 (not 403). Direct POST with missing header → 422 (not 403). The only 403 the stack produces on this URL is for a GET, not a POST.
- Backend access logs: zero device-upload attempts from Bill's RC Pro in the last 24h. His device is not reaching the backend.

**Actual root cause:** Capacitor `Preferences` state on Bill's RC Pro (stored `serverUrl` + `apiKey`) was wiped or cleared between 2026-04-19 and 2026-04-23. v2.61.5 ships with `DEFAULT_SERVER_URL = ''`, so a missing `serverUrl` resolves fetch() against the WebView origin and the request never leaves the device. The reported "403" is plausibly a misremembered network error.

**Remediation delivered** (no code change, no config change):

- Rotated `M4TD` `key_hash` in `device_api_keys` to `sha256('doc_m4td_i8Qt9OJDogxjbgXgz2LRH4a0MrzTSxcVa8ltHxoS0Us')` (prefix `85e88054`). Row UUID preserved (`962f631d-80db-4300-85be-8af5722d2635`); `last_used_at` history retained.
- End-to-end probe from HSH-HQ with the new raw value: `device-health` → 200 `connected M4TD`; `device-upload` with dummy multipart → 200 with structured parser error (expected; dummy fails DJI log prefix check).

**Operator action**: on Bill's RC Pro, DroneOpsSync → Settings → paste `API Key = doc_m4td_i8Qt9OJDogxjbgXgz2LRH4a0MrzTSxcVa8ltHxoS0Us`, confirm `Server URL = https://droneops.barnardhq.com`, Save, Test Connection (expect green), Sync Now. The 3 pending `DJIFlightRecord_2026-04-23_*.txt` files will upload.

v2.62.0 APK (pre-baked `DEFAULT_SERVER_URL`) is still the right preventive upgrade for next time a Preferences wipe happens on any device in the fleet — but it is **not required** to land today's 3 flight records.

Full second-pass evidence in `docs/adr/0002-droneopssync-upload-auth.md` §4.1.

---

## 2026-04-24 — DroneOpsSync upload auth + HTTPS-only base URL — v2.63.4 (backend) / v2.62.0 (companion) (ADR-0002)

Operator's personal DJI RC Pro (no camera) reported two-symptom failure uploading three post-flight logs (~17 MB total) to `http://droneops.barnardhq.com`:

1. `[HEALTH] GET /health → IOException: Use JsonReader.setLenient(true) ... line 1 column 1 path $`
2. `[UPLOAD] DJIFlightRecord_2026-04-23_[21-11-04].txt → HTTP 403 {"detail":"Not authenticated"}`

### Root cause

Three composing failures, none of them server-side:

- The APK on the controller is a **pre-v2.33.0 native Android build**, not the current v2.36.x Capacitor build. It hits legacy `/health` (unprefixed) with a Gson client at default `setLenient(false)`. CF's HTTP→HTTPS redirect returns an HTML body; Gson crashes at `line 1 column 1` before the response can authenticate anything.
- The stale APK's upload path hits a JWT-gated endpoint — `{"detail":"Not authenticated"}` is FastAPI's default response when `get_current_user` fails, not the device-api-key validator (which returns `"Invalid or revoked device API key"`).
- Operator-entered `http://` base URL is what triggers the HTML-body redirect. The current Capacitor client uses `fetch()` (which would silently follow the redirect) but the stale Gson client cannot.

The current server surface is already correct: `GET /api/flight-library/device-health` and `POST /api/flight-library/device-upload` both gate on `validate_device_api_key` (`backend/app/auth/device.py`), SHA-256-hash the inbound `X-Device-Api-Key`, look it up in `device_api_keys`, and 401 on miss. No endpoint change required; the client is stale.

### Fix shipped (aegis)

Companion-side (`companion/` → v2.62.0):

- Added `validateServerUrl()` in `companion/src/sync.ts` — rejects plaintext `http://` public URLs with the RFC-1918 + loopback + link-local carve-out (same shape as EyesOn's `isPrivateAddress`). Applied in `saveConfig`, `checkHealth`, and `uploadLogs`.
- Pre-baked `DEFAULT_SERVER_URL = "https://droneops.barnardhq.com"`. v2.34.0's `1544b9e` blanked this to stop leaking a private `10.x` IP; shipping the public FQDN reinstates out-of-box usability without reintroducing the leak.
- `App.tsx` `saveAndSync` now catches validation errors and surfaces them in the Settings test-status banner instead of swallowing them. Footer bumped to v2.62.0.
- Fresh APK will be cut by `.github/workflows/companion-apk.yml` on push (BOS-HQ self-hosted runner per ADR-0029) and published as release `companion-v2.62.0`.

Server-side (`backend/` → v2.63.4):

- Added `GET /health` top-level alias in `backend/app/main.py` returning JSON — matches what stale clients expect and lets CF tunnel / uptime probes succeed without hitting the SPA HTML.
- Structured-JSON INFO log on every `/api/flight-library/device-upload` call: `{event, device_label, device_id, file_count, total_bytes, imported, skipped, error_count}`. Raw API key never logged.
- Structured-JSON WARN log on every device-auth failure in `backend/app/auth/device.py`: `{event, key_prefix (8-char SHA-256 prefix), ip, user_agent, path}`.
- No schema change; no migration; additive route + log lines only.

Operator action (one-time):

- Install `DroneOpsSync-2.62.0.apk` once the release lands (watch <https://github.com/BigBill1418/DroneOpsCommand/releases>).
- Settings → Device Access on the server already has an `M4TD` row for the RC Pro (last used 2026-04-19). Re-use that raw key value (Bill has it stored); no rotation needed.
- Tap SAVE & SYNC — the three pending `DJIFlightRecord_2026-04-23_*.txt` files upload to BOS-HQ.

### Verified (end-to-end against production)

- `POST https://droneops.barnardhq.com/api/flight-library/device-upload` with a valid `X-Device-Api-Key` + a multipart file → **HTTP 200** with a well-formed `FlightUploadResponse`. The device-auth path is fully healthy; the bug was 100% the stale APK.
- `GET https://droneops.barnardhq.com/api/flight-library/device-health` with a wrong key → 401 `"Invalid or revoked device API key"` (correct).
- `GET https://droneops.barnardhq.com/api/flight-library/device-health` with a missing header → 422 validation error (correct; will become 401-on-auth when reached via the new `/health` alias once the alias is deployed).

Auth model recorded in `docs/adr/0002-droneopssync-upload-auth.md` — flipped **Proposed → Accepted**. `X-Device-Api-Key` header remains the primitive. Rejected JWT-bearer, mTLS, OAuth device-flow. Managed-tenant discovery deferred to first live tenant but forward path codified as a copy of EyesOn ADR-0020's `GET /api/discovery/pair/:code` pattern.

### Failover / resilience

- No schema change, no migration. Additive device-key row only.
- Compose-only on the server if the `/health` shim lands; no cross-service dependency.
- Blue-green swap and failover-engine untouched.
- Managed-tenant instances (when live) inherit the same model; no divergent code path.

Hardware constraint acknowledged per `feedback_dji_rc_pro_no_camera.md` — no QR, no visual pairing. ADR-0002 aligns with EyesOn ADR-0019's validated camera-less UX pattern.

## 2026-04-21 — ci: companion APK + claude auto-merge moved to BOS-HQ self-hosted runners (ADR-0029)

DroneOpsCommand CI no longer consumes paid `ubuntu-latest` minutes. Both workflows (`companion-apk.yml`, `auto-merge-claude.yml`) flipped to `runs-on: [self-hosted, linux, x64, bos]`. The runner is a single `--ephemeral` container (`runner-droneopscommand`) on BOS-HQ, behind its own Docker-in-Docker sidecar — a compromised CI job cannot reach the host's Swarm socket.

Post-cutover validation:
- `Build DroneOpsSync companion APK` succeeded in 1 m 32 s on `runner-droneopscommand` (vs 2 m 7 s on ubuntu-latest) — ~35 s faster with warm gradle cache in the per-runner named volume.
- Permanent `.github/workflows/self-hosted-smoke-test.yml` workflow added (`workflow_dispatch`-only, ~9 s runtime) for on-demand runner health-checks.

Security posture (this repo is public on GitHub):
- Existing triggers are `push` + `workflow_dispatch` only — **no `pull_request` trigger** — so outside-collaborator fork PRs cannot currently reach the self-hosted runner.
- GitHub "Require approval for all outside collaborators" setting is recommended as defense-in-depth (not load-bearing against current triggers). Setting lives at Settings → Actions → General → Fork pull request workflows from outside collaborators.

Commits: `652eee0` (smoke test workflow), `68b4bc4` (2 runs-on flips). No application version bump — CI infrastructure change, not application code (per CLAUDE.md versioning rule).

Authoritative decision record and runbook live in NOC-Master-Control-SWARM:
- ADR: `docs/adr/0029-gh-actions-self-hosted-on-bos-hq.md`
- Runbook: `docs/runbooks/gh-runners.md`
- Plan: `docs/plans/2026-04-21-gh-runners-on-bos-hq.md`

## 2026-04-20 — Maintenance type vocabulary unified (v2.63.3)

Overdue schedule alerts like "Compass Calibration" could not be cleared
because the frontend + backend disagreed on how `maintenance_type` was
encoded.

**Backend** (`routers/maintenance.py`) seeded `MaintenanceSchedule`
rows with Title-Case label strings from `DJI_MAINTENANCE_DEFAULTS`
(e.g. `"Compass Calibration"`).

**Frontend** (`pages/Maintenance.tsx::MAINTENANCE_TYPES`) offered
snake_case values (`compass_calibration` wasn't even present;
`gimbal_calibration`, `battery_check`, `firmware_update`, etc. were).

Two consequences:
1. The schedule-clear loop at `maintenance.py:120-129` matched
   `MaintenanceSchedule.maintenance_type == mtype` case-sensitive —
   Title-Case vs. snake_case never matched, so `last_performed` never
   updated. Every seeded DJI schedule eventually drifted into a
   permanent alert with no UI escape.
2. Six DJI categories (Compass Calibration, IMU Calibration, Battery
   Health Check, Firmware Review, Remote Controller Inspection, Sensor
   Cleaning) had no dropdown entry at all, so a user facing an overdue
   alert for any of them literally could not submit a matching record.

### Fix

- Replaced `MAINTENANCE_TYPES` with the canonical 12-entry Title-Case
  list that exactly mirrors `DJI_MAINTENANCE_DEFAULTS` plus
  `General Service` / `Other` catch-alls. Both `value` and `label` are
  the Title-Case string — drift can't recur because the UI value is
  the canonical key.
- Default form value flipped from `'general_service'` to
  `'General Service'` in the three places it was hard-coded.
- One-shot migration script `scripts/migrate_maintenance_type_vocab.py`
  rewrites existing `MaintenanceRecord.maintenance_type` rows from
  legacy snake_case to the new canonical Title-Case. Handles
  comma-separated multi-category values. Safe to re-run (already-Title
  values pass through).

### Deploy

1. NOC Master picks up the push on `main` and runs the build/up on
   HSH-HQ (prod) + CHAD-HQ (demo) per `verify-deploy.sh`.
2. After deploy, run the record-remap migration once per host inside
   the backend container:
   `docker compose exec backend python scripts/migrate_maintenance_type_vocab.py`
3. Log a new Compass Calibration record on each affected aircraft —
   `last_performed` now updates and the alert clears.

### Failover / resilience

- No schema change, no migration DDL. Migration is idempotent data-only.
- No port / connection-string / PG-replication impact.
- Blue-green swap and failover-engine untouched.
- `docker-compose.yml` untouched.

## 2026-04-19 — Zombie-leak fixes + Redis-heartbeat healthcheck

### Redis-heartbeat celery healthcheck (v2.63.2)

Celery worker docker healthcheck no longer spawns `celery ... inspect ping`
every 60s — that was re-importing the full OTel instrumentation chain on
each check (~3-5s of wasted CPU + memory churn per minute, 1440×/day).

**New design:** Celery's `worker_heartbeat` signal fires on the control
loop (~every 2s when the worker is alive). A tiny handler in
`backend/app/tasks/celery_tasks.py` writes a unix-timestamp key to Redis
(`droneops:worker:heartbeat`, 120s TTL). The docker healthcheck is now a
single Redis GET + age check (interval 30s, timeout 5s, start_period 30s).
Fresh key = worker control loop alive; stale/missing = frozen/crashed →
docker restarts.

**Failover/resilience:** PG replication, container recreation, blue-green
swap, and failover engine all unaffected. Repair quality: `redis-tools`
added to Dockerfile; roundtrip (SETEX + GET) verified against redis:7.

### Backend zombie-leak fix (init reaper)

Follow-up to the worker fix: `docker-compose.yml` backend service now has
`init: true` (tini PID 1 reaper). Investigation of the HSH-HQ high-load
incident found 3 fresh `<defunct>` curl children accumulating under the
uvicorn master. Same SIGCHLD reap leak pattern as the worker — backend
spawns curl via health-probe/outbound HTTP and loses the occasional reap.
`init: true` makes the leak structurally impossible. No application code
change, no version bump.

### Worker zombie-leak fix (init reaper + max-tasks-per-child)

The 2026-04-19 HSH-HQ high-load incident found 33 defunct celery children
of the worker master accumulating ~2/hr over 18h, contributing to
`HostZombieProcesses` alert. Root cause: the celery prefork master loses
occasional `SIGCHLD` reaps on Python 3.12 when child cleanup races with
task completion.

**Changes to `docker-compose.yml` worker service:**
- Added `init: true` (tini PID 1 reaper), which adopts and reaps any orphan
  process — making the reap leak structurally impossible regardless of
  celery internals.
- Added `--max-tasks-per-child=50` to the celery command for belt-and-suspenders:
  each child recycles after 50 tasks, so even a leaked child is short-lived.
  50 was chosen to amortize Sentry/OTEL init cost (~3-5s) fine across
  report+email tasks.

No application code touched, so no version bump.

See `~/noc-master/docs/incidents/2026-04-19-hsh-hq-high-load.md` (DF-1).

## v2.63.2 — 2026-04-19 — Redis-heartbeat celery healthcheck (replaces inspect ping)

Celery worker docker healthcheck no longer spawns `celery ... inspect ping`
every 60s — that was re-importing the full OTel instrumentation chain on
each check (~3-5s of wasted CPU + memory churn per minute, 1440×/day).

**New design:** Celery's `worker_heartbeat` signal fires on the control
loop (~every 2s when the worker is alive). A tiny handler in
`backend/app/tasks/celery_tasks.py` writes a unix-timestamp key to Redis
(`droneops:worker:heartbeat`, 120s TTL). The docker healthcheck is now a
single Redis GET + age check:

```
K=$(redis-cli -h redis -p 6379 get droneops:worker:heartbeat)
AGE=$(( $(date +%s) - K ))
[ $AGE -le 60 ] || exit 1
```

Interval 30s, timeout 5s, start_period 30s. Fresh key = worker control
loop alive; stale/missing = frozen/crashed → docker restarts.

**Failover/resilience review:**
1. PG replication — unaffected (Redis-only signal).
2. Container recreation — the seed write on `worker_ready` populates the
   key within seconds of startup; healthcheck's 30s start_period covers
   the boot gap.
3. Blue-green swap — unaffected (per-container health only).
4. Failover engine — unaffected.
5. Customer-facing — zero impact; healthcheck is an internal signal.

**Repair quality audit:**
- Dockerfile: `redis-tools` added to apt-get. `which redis-cli` confirmed
  missing in current container; the new image includes it.
- Secondary failures: if Redis is down, the write fails (caught, logged
  at DEBUG) and the healthcheck reads a stale/missing key → container
  marked unhealthy → docker restarts. Correct behavior; replaces what
  used to be a silent `inspect ping` timeout.
- Roundtrip verified: `SETEX` writes + `GET` reads tested against redis:7
  used by this stack.

Files: `backend/app/tasks/celery_tasks.py`, `backend/Dockerfile`,
`docker-compose.yml` (worker service healthcheck).


## [Ops] — 2026-04-19 — Backend zombie-leak fix (follow-up to worker fix)

### Changed
- `docker-compose.yml` backend service: added `init: true` (tini PID 1
  reaper) — matches the worker fix shipped earlier today.

### Why

Follow-up investigation to the HSH-HQ high-load incident found 3
fresh `<defunct>` curl children accumulating under the uvicorn master
(PID 3888871 = `droneops-backend-1`). Same SIGCHLD reap leak pattern
as the worker, different container — the earlier `init: true` fix
only covered the worker service. Backend spawns curl via health-probe
/ outbound HTTP and loses the occasional reap the same way.

`init: true` on backend makes the leak structurally impossible here
too. No application code change, no version bump.

See `~/noc-master/docs/incidents/2026-04-19-hsh-hq-high-load.md`.

---

## [Ops] — 2026-04-19 — Worker zombie-leak fix: init reaper + max-tasks-per-child

### Changed
- `docker-compose.yml` worker service: added `init: true` (tini PID 1
  reaper) and `--max-tasks-per-child=50` to the celery command.

### Why

The 2026-04-19 HSH-HQ high-load incident found 33 defunct celery
children of the worker master (PID 3888863) accumulating ~2/hr over
18h, contributing to the `HostZombieProcesses` alert. Root cause: the
celery prefork master loses occasional `SIGCHLD` reaps on Python 3.12
when child cleanup races with task completion. Without a PID-1 reaper
inside the container, those orphans pile up.

`init: true` injects tini as PID 1, which adopts and reaps any orphan
process — making the reap leak structurally impossible regardless of
celery internals. `--max-tasks-per-child=50` adds belt-and-suspenders:
each child recycles after 50 tasks, so even a leaked child is
short-lived. 50 was picked because report+email tasks are short and
child startup cost is dominated by Sentry/OTEL init (~3-5s); 50/child
amortizes that fine.

This is a compose-only change — no application code touched, so no
version bump.

See `~/noc-master/docs/incidents/2026-04-19-hsh-hq-high-load.md` (DF-1).

## [Ops] — 2026-04-18 — Worker healthcheck timeout raised post-observability (commit 7b33169)

### Changed
- `docker-compose.yml` worker healthcheck: `timeout 15s -> 30s`,
  `interval 30s -> 60s`, `start_period 30s -> 60s`.

### Why

After v2.63.1 deployed, `celery -A app.tasks.celery_tasks inspect ping`
started exceeding the 15s docker healthcheck window. Root cause: the
inspect subcommand spawns a fresh Python that re-imports
`app.tasks.celery_tasks`, which now triggers `init_sentry()` +
`init_otel()` and their downstream SQLAlchemy / httpx / Celery /
logging instrumentors. Measured ~22s end-to-end on the wall clock.

The worker itself responds `OK / pong` inside the window — it was the
subprocess boot that exceeded timeout. Bumping the timeout adds
headroom; the worker's failure-detection SLA is now "unhealthy after
3 minutes" (60s × 3 retries) instead of the previous ~90s. Acceptable
because a Celery worker's failure mode is queue-depth growth, not
request-path latency.

## [2.63.1] — 2026-04-18 — Sentry + OTel SDKs + compose labels (observability Phase 5)

### Added
- `backend/app/observability/` package with `sentry.py`, `otel.py`, and
  `pii.py`. Both SDK inits are DSN/endpoint-gated — unset env is a
  no-op, so self-hosted single-tenant installs keep working without the
  central plane on HSH-HQ.
- Backend deps in `backend/requirements.txt`:
  `sentry-sdk[fastapi,celery,sqlalchemy]>=2.18.0` + the full OTel
  SDK/exporter/instrumentor block (`opentelemetry-api`, `-sdk`,
  `-exporter-otlp`, instrumentors for fastapi, celery, sqlalchemy,
  httpx, logging).
- `init_sentry("droneops-api")` + `init_otel("droneops-api")` in
  `backend/app/main.py` and `init_sentry("droneops-worker")` +
  `init_otel("droneops-worker")` in
  `backend/app/tasks/celery_tasks.py`. FastAPI auto-instrumentation
  wired via `instrument_fastapi(app)` after `app = FastAPI(...)`.
- `frontend/src/lib/sentry.ts` — `initFrontendSentry()` bootstrap. Bails
  out when `VITE_SENTRY_DSN` is unset. Invoked from `frontend/src/main.tsx`
  before `createRoot` so it catches React error boundaries.
- `@sentry/react@^8.40.0` in `frontend/package.json`.
- `frontend/Dockerfile` build-args for `VITE_SENTRY_DSN`,
  `VITE_SENTRY_ENVIRONMENT`, `VITE_APP_VERSION` — Vite inlines these at
  build time.
- `com.barnardhq.*` labels (`project`, `env`, `tenant`, `stack`, plus
  per-service `service`) on every service in `docker-compose.yml` and
  `docker-compose.demo.yml` via YAML anchors, plus a shared `json-file`
  logging driver config so Alloy can discover + tail the streams.
- `docker-compose.demo.yml` override pins `com.barnardhq.env=demo`,
  `OTEL_EXPORTER_OTLP_ENDPOINT=http://10.99.0.2:4317` (CHAD-HQ Alloy),
  and `SENTRY_ENVIRONMENT=demo`.
- New env vars in `.env.example`: `SENTRY_DSN`,
  `SENTRY_TRACES_SAMPLE_RATE`, `SENTRY_ENVIRONMENT`,
  `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_SERVICE_NAME`, `TENANT`, `ENV`,
  `VITE_SENTRY_DSN`, `VITE_SENTRY_ENVIRONMENT`.

### Why

Closes Phase 5 of the BarnardHQ observability rollout. Prod DroneOps on
HSH-HQ now reports to GlitchTip project 10 + HSH-HQ Alloy; demo on
CHAD-HQ reports to GlitchTip project 11 + CHAD-HQ Alloy. Per the
topology reference, BOTH hosts are verified after every deploy.

### Resilience

Every init path is wrapped — a failed Sentry SDK import, a broken DSN, a
double-instrument attempt, or an unreachable collector will log a
WARNING and continue. DroneOps is first-responder tier-1; observability
must never be the reason a container fails to start.

## [2.63.0] — 2026-04-18 — Structured JSON logging (observability Phase 5 pre-req)

### Added
- `python-json-logger==3.2.1` in `backend/requirements.txt`.
- `_setup_json_logging()` in `backend/app/main.py` — replaces the plain
  `logging.basicConfig(format=...)` setup with a `pythonjsonlogger.json.JsonFormatter`
  that renames `asctime`→`timestamp`, `levelname`→`level`, and emits every
  log line as a parseable JSON object on the root logger.
- Celery signal hooks (`after_setup_logger`, `after_setup_task_logger`) in
  `backend/app/tasks/celery_tasks.py` — swap the formatter on worker
  bootstrap so the worker stream matches the API stream shape.

### Why

Alloy (the central Loki pump on HSH-HQ + CHAD-HQ) discovers containers by
`com.barnardhq.*` labels and stamps `service=droneops-api` / `droneops-worker`
on the stream. Downstream queries in Grafana need parseable fields, not
`YYYY-MM-DD HH:MM:SS [LEVEL] name: msg` prose. This is the pre-req before
the Sentry/OTel SDKs go in the next commit.

### Log shape change — operator notice

Any downstream consumer that greps plaintext `[INFO]` / `[WARNING]` level
prefixes in DroneOps container logs will need to migrate to JSON parsing
(`.level`, `.message`, `.name`, `.timestamp`). The FastAPI request-logger
middleware in `log_requests()` continues to use the same structlog message
keys — only the wire format changes.

## [Ops] — 2026-04-16 — Demo bootstrap.sh guard + explicit env_file on demo override

### Added
- **`bootstrap.sh`** at repo root — idempotent launcher for the demo stack.
  Refuses to start if `.env.demo` is missing or critical vars are empty
  (POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB, DATABASE_URL,
  JWT_SECRET_KEY, DEMO_ADMIN_USERNAME, DEMO_ADMIN_PASSWORD,
  CLOUDFLARE_TUNNEL_TOKEN). Symlinks `.env → .env.demo` if `.env` is
  absent so compose loads the right file even from a bare
  `docker compose up -d` call. `./bootstrap.sh --clean` for a no-cache
  rebuild.
- **Operator usage:** `cd ~/droneops-demo && ./bootstrap.sh` instead of
  `docker compose up -d` directly.

### Changed
- **`docker-compose.demo.yml`** — added `env_file: .env.demo` to every
  demo-override service (backend, frontend, cloudflared, db). Second
  layer of safety so even a plain `docker compose up -d` (without the
  `--env-file` flag) still loads demo credentials for each service.

### Why

Root cause of 2026-04-16 DroneOps-Demo 6h 26m outage: demo stack was
restarted without `--env-file .env.demo` and without a `.env` symlink in
place. Compose silently fell back to the base defaults
(`POSTGRES_USER=doc`, `POSTGRES_PASSWORD=changeme_in_production`), which
did not match the DB volume initialized on 2026-04-04 with `doc_demo`
credentials. Backend crash-looped on `password authentication failed for
user "doc"`. The fail-loud script + explicit `env_file` directives mean
this class of failure can't happen again without a human seeing an
explicit error message.

## [Ops] — 2026-04-16 — Watchtower scoped to `--label-enable` (opt-in)

### Changed
- **`docker-compose.yml` / watchtower service** — added
  `WATCHTOWER_LABEL_ENABLE=true` to the environment block. Watchtower now
  only updates containers explicitly labelled
  `com.centurylinklabs.watchtower.enable=true`. No container carries that
  label yet → Watchtower is a no-op until explicit opt-in. Rationale:
  infrastructure containers (cloudflared, postgres, anything on a Swarm
  manager) must not be auto-updated — a surprise pull could restart a
  critical container at any hour. Part of the 2026-04-16 incident
  remediation; see NOC Master ADR-0007 (Docker daemon is infrastructure)
  for the full argument. To re-enable auto-update on a specific app
  container later, add the label to its compose block and redeploy.

## [Ops] — 2026-04-16 — DroneOps autopull systemd timer disabled

### Changed
- **`droneops-autopull.timer` / `droneops-autopull.service`** — stopped and
  disabled (`systemctl disable --now droneops-autopull.timer`). NOC Master
  Control is the single canonical deployer for all BarnardHQ stacks
  (registered in `~/noc-master/data/config.yml` as
  `BigBill1418/DroneOpsCommand` → `/host-home/droneops` on `main`,
  `enabled: true`). The per-repo autopull script had been silently failing
  for 3 days (log last advanced 2026-04-13 01:22) because it doesn't respect
  `~/droneops/.deployer-disabled` — disabling the timer removes the dead
  schedule without deleting the unit files, which stay in
  `/etc/systemd/system/` for emergency re-enable.
- Matches the post-2026-04-09-incident rule: "single centralized deployer"
  for every stack. No behavioural change for production — the script had
  stopped doing useful work on 04-13 anyway.

### Resilience guard
- Pure systemd state change, no code change, no rebuild. Zero blast radius
  on replication, failover, blue-green.

## [2.62.0] — 2026-04-16 — Business-signals endpoint for Jarvis Innovation Engine

### Added
- **`GET /api/v1/business-signals`** — authenticated aggregate endpoint that
  returns a 30-day + 90-day snapshot of mission throughput, flight counts,
  invoice totals (created + paid), new-customer count, plus an "active_now"
  block for in-progress and in-review missions.
- Response shape is documented inline in
  `backend/app/routers/business_signals.py` and is the source of truth for
  the contract consumed by Project J.A.R.V.I.S.'s Innovation Engine signals
  collector (`app.innovation.signals._collect_droneops`).
- Every metric query is `_safe_scalar`-wrapped: a single broken SELECT
  returns `null` (Jarvis treats null as "unknown, not zero") instead of
  dropping the whole envelope.

### Resilience guard
- Read-only endpoint, no new tables, no migrations. Zero impact on
  replication, failover, or blue-green swap flow.
- No new environment variables — reuses existing JWT auth.
- Two queries (30d window + 90d window + active_now); no joins beyond
  the existing `MissionFlight → Mission` path.
