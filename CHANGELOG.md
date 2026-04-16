# Changelog

Notable changes to DroneOpsCommand. Dates are absolute (YYYY-MM-DD, UTC).

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
