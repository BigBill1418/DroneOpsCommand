# DroneOpsCommand — In-Flight Work

Maintained alongside `CHANGELOG.md` and `docs/adr/`. `CHANGELOG.md` is
the ledger of shipped changes; this file tracks what's in-flight or
blocked.

## 2026-04-24 LATE — IN-FLIGHT: Performance audit fix series (ADR-0004/0005)

Executing the 5-fix plan from `docs/plans/2026-04-24-perf-audit.md`.
Fixes ship as 5 separate commits, each with its own version bump and
AFTER-measurement appended to ADR-0005.

- **FIX-1 — v2.63.7 — SHIPPED in this branch:** weather endpoint
  parallelized via `asyncio.gather` + 5-min Redis cache (failure-open).
  6 new tests, all green. ADR-0005 will receive the live AFTER numbers
  once BOS-HQ autopulls and the verification commands run.
- **FIX-2 — v2.63.8 — pending:** raise async DB pool 5+10 → 20+20 + add
  60s in-process cache around `get_current_user`.
- **FIX-3 — v2.63.9 — pending:** code-split 17 main pages + Vite
  `manualChunks` for vendor bundles.
- **FIX-4 — v2.63.10 — pending:** custom `useApiCache` hook + apply to
  Dashboard / Flights / Settings.
- **FIX-5 — v2.63.11 — pending:** docs final pass — flip ADR-0004 to
  `accepted`, append AFTER tables to ADR-0005.

## 2026-04-24 EVENING — SHIPPED PR: Zero-touch device API key rotation (ADR-0003 / FU-7)

Backend v2.63.6, paired with DroneOpsSync v1.3.25. Closes the manual key-paste step that the 2026-04-24 morning incident required.

**Branch:** `claude/zero-touch-key-rotation-backend` (PR open, **not merged** — operator reviews per the routine spec).

**What landed:**

- Two nullable columns on `device_api_keys` (`rotated_to_key_hash`, `rotation_grace_until`); additive, failover-safe per CLAUDE.md §Failover Guard.
- Dual-key auth in `validate_device_api_key` during grace.
- New admin endpoint `POST /api/admin/devices/{device_id}/rotate-key`.
- Redis side-channel for the raw new-key hint (`app.services.rotation_hint`); fail-closed if Redis is down.
- Device-health response emits `rotated_key` + `rotation_grace_until` ONLY for OLD-key auth during grace; transparent to existing clients.
- Celery finalizer task on 15-min beat (`finalize_key_rotations_task`).
- Single Pushover FYI per rotation; env-gated like the rest of ADR-0002 §5.
- 15 unit tests, all green; `backend/tests/` infrastructure bootstrapped (`pytest.ini`, `conftest.py`, `requirements-dev.txt`).

**Notable about the routine handoff:** Claude Code remote routine `trig_01KiBK88vqs6vtRf75rkxcw8` shipped an empty branch on its first run. aegis re-ran the spec from this conversation and produced both PRs. ROADMAP FU-7 closed.

**Not deployed yet** — operator merge → `update.sh` rebuild on BOS-HQ → Celery beat picks up the new task on next worker restart.

## 2026-04-24 — SHIPPED: DroneOpsSync prevention mechanisms + landscape lock (ADR-0002 §5)

Backend v2.63.5 / companion v2.62.1. Bill's uploads are recoverable per §4.1 (operator paste the rotated `M4TD` key on his RC Pro); this follow-up makes the class of failure non-recurrent.

**Landscape lock** — `patch-android.cjs` injects `sensorLandscape` + `configChanges` on every `<activity>` after `npx cap sync android`, with a build-time fail-hard if any `portrait` survives. DJI RC Pro is physically landscape-only; a rotate reflow would destroy the WebView.

**Layered silent-drift watchdog (all on by default):**
1. Companion pairing banner on launch via `checkPairing()` — persistent red banner when `serverUrl` or `apiKey` is missing/malformed. Blocks auto-sync that could only fail.
2. Companion preflight health gate via `preflightHealth()` — structured `{ok, code, message}`; failures surface as banner copy, not silent retries.
3. Server silence watchdog — hourly Celery beat (`check_device_silence_task`). Recently-active keys silent > 48h fire a Pushover alert, deduped 12h. New `beat` compose service.
4. First-401 Pushover alert — `validate_device_api_key` on any `/device-*` path, deduped 1h per `(key_prefix, ip)`.

Pushover alerting is env-gated: `PUSHOVER_TOKEN` + `PUSHOVER_USER_KEY`. Unset = structured JSON log only (still observable via Loki). No flag-gating anywhere.

**Open action for operator:**
- Drop `PUSHOVER_TOKEN` + `PUSHOVER_USER_KEY` into BOS-HQ `/opt/droneops/.env` to turn on phone alerts. Without them, the watchdog still runs (visible in `droneops-beat` logs + Loki) but Bill's phone stays quiet.
- Next APK install will apply the landscape lock + banner. Pending: GitHub Actions on `main` will publish `DroneOpsSync-2.62.1.apk` via the self-hosted BOS-HQ runner (ADR-0029).

## 2026-04-24 — Awaiting operator action on Bill's 3 pending flight records (ADR-0002 §4.1)

Status: server healthy, `M4TD` key rotated + verified (HTTP 200 end-to-end from HSH-HQ to BOS-HQ via CF). The stale-APK RCA in the v2.63.4 commit was wrong; the actual root cause is Capacitor `Preferences` state on Bill's RC Pro. Second-pass evidence in `docs/adr/0002-droneopssync-upload-auth.md` §4.1.

Pending: Bill paste `doc_m4td_i8Qt9OJDogxjbgXgz2LRH4a0MrzTSxcVa8ltHxoS0Us` into DroneOpsSync → Settings on his RC Pro, tap Test Connection (green = M4TD), tap Sync Now. The three `DJIFlightRecord_2026-04-23_*.txt` files upload. Follow-up telemetry: `M4TD.last_used_at` should advance past `2026-04-19 23:07:44` and three `device_upload` INFO log lines should appear in `droneops-backend-1`.

Follow-up (not blocking today's records): v2.62.0 APK install to pre-bake `DEFAULT_SERVER_URL = https://droneops.barnardhq.com` so future Preferences wipes can't silently break uploads on any device in the fleet.

## 2026-04-24 — DroneOpsSync upload auth + HTTPS-only base URL (ADR-0002)

Operator's personal DJI RC Pro (no camera) could not upload 3 post-flight
logs (~17 MB) to `http://droneops.barnardhq.com`. Two-symptom failure:

1. `/health` GET returned HTML (CF HTTP→HTTPS redirect body); stale APK's
   Gson client crashed at `line 1 column 1` because it ran with default
   `setLenient(false)`.
2. Upload POST returned `403 {"detail":"Not authenticated"}` — FastAPI's
   default `get_current_user` JWT rejection, i.e. stale APK hit a
   JWT-gated endpoint instead of the current `X-Device-Api-Key`-gated
   `POST /api/flight-library/device-upload`.

Root cause: the APK on the controller is pre-v2.33.0, pre-dates the
Capacitor rewrite, and uses a Gson-based client against legacy paths.
The current server surface is correct — device-health and device-upload
endpoints are already wired to `validate_device_api_key`
(`backend/app/auth/device.py`) with SHA-256 hash lookup.

**Status — SHIPPED 2026-04-24 by aegis.** Backend v2.63.4, companion
v2.62.0. Scope delivered:
- Companion: `validateServerUrl()` in `sync.ts` rejects plaintext public
  URLs with RFC-1918 + loopback carve-out; `DEFAULT_SERVER_URL`
  pre-baked to `https://droneops.barnardhq.com`;
  `App.tsx::saveAndSync` catches validation errors into the Settings
  test-status banner. Footer bumped. APK will be cut by
  `companion-apk.yml` on BOS-HQ self-hosted runner on push.
- Server: top-level `GET /health` alias (JSON, same payload as
  `/api/health`); structured INFO log on `/device-upload` and WARN log
  on device-auth failure (`key_prefix` only — never the raw key).
- Operator: existing `M4TD` row in `device_api_keys` is already valid
  (last used 2026-04-19); no rotation. Bill reuses the raw key value
  he already has.

**Pending operator action**: install `DroneOpsSync-2.62.0.apk` from the
upcoming release on the RC Pro; paste server URL + existing `M4TD` key;
tap SAVE & SYNC. The 3 pending DJIFlightRecord files upload.

Docs:
- **ADR-0002** (`docs/adr/0002-droneopssync-upload-auth.md`) — auth-model
  decision + HTTPS-only + forward path for managed-tenant discovery
  (deferred; pattern copy from EyesOn ADR-0020 when first tenant ships).
- **CHANGELOG** — 2026-04-24 entry above ADR-0029.
- **ROADMAP** — follow-up items (fleet audit, `/health` shim, Grafana
  stale-client tripwire) filed under "Observability + Fleet Hygiene".

Hardware constraint: DJI RC Pro has no usable rear camera for field
operation. No QR / visual pairing ever. Auth model aligns with EyesOn
ADR-0019 (keypad / non-visual enrollment). See
`feedback_dji_rc_pro_no_camera.md`.

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

---

## 2026-04-24 PM — Capacitor companion abandoned; Kotlin path restored; v1.3.25 queued

### Completed
- `companion/` directory deleted from this repo (commit `4b87e65`, 20 files / 5,306 lines). Abandoned Capacitor fork had zero device installs after 4 weeks of parallel-track work. ADR-0002 §7 added cross-linking DroneOpsSync ADR-0001. Workflow `.github/workflows/companion-apk.yml` removed.
- Three orphan GH releases (`companion-v2.61.5`, `companion-v2.62.0`, `companion-v2.62.1`) edited with `⚠ ABANDONED` banner pointing at DroneOpsSync repo.
- BOS-HQ Pushover watchdog verified end-to-end. `~/droneops/.env` already had `PUSHOVER_TOKEN` + `PUSHOVER_USER_KEY` set this morning (06:51Z); container `droneops-backend-1` has both vars loaded; `curl api.pushover.net/1/users/validate.json` returns `status:1, devices:["PrimaryPhoneS25"]`. Smoke-test notification `3eac9d51-fc67-4248-8a8a-038daac5a023` delivered to Bill's phone. ADR-0002 §5 layers 3+4 can now page the operator.

### Scheduled (not yet executed)
- Remote routine `trig_01KiBK88vqs6vtRf75rkxcw8` (https://claude.ai/code/routines/trig_01KiBK88vqs6vtRf75rkxcw8, fired 2026-04-24T18:58Z) will open PRs for zero-touch device API key rotation. This repo lands ADR-0003 + backend grace-window + dual-key auth + celery finalizer + Pushover FYI. Paired with DroneOpsSync v1.3.25 that parses the `rotated_key` field from preflight response.

### Decisions
- Real DroneOps companion app lives in `BigBill1418/DroneOpsSync` (native Kotlin), NOT in this repo. A future session that wants to "fix the companion" MUST work there. `feedback_verify_before_planning` applied — verifying which codebase actually runs on Bill's RC Pro before planning should have been step 1 this morning, not step N.
- Pushover token in `~/droneops/.env` is a dedicated DroneOps app (distinct from NOC's), giving DroneOps its own rate-limit bucket and notification identity on Bill's phone. USER_KEY matches NOC canonical.

### Evidence
- DroneOpsCommand `main` HEAD: `4b87e65` (companion removal).
- DroneOpsSync `main` HEAD: `832585c` (v1.3.24 released, 4 commits land since `cae8c30`).
- DroneOpsSync release v1.3.24: https://github.com/BigBill1418/DroneOpsSync/releases/tag/v1.3.24 — signer fingerprint identical to v1.3.23.

### Next
- Wait for `trig_01KiBK88vqs6vtRf75rkxcw8`; review + squash-merge PRs when they arrive.
- No backend changes land until those PRs merge. Current HEAD is production-stable.
