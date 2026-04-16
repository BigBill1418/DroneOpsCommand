# Changelog

Notable changes to DroneOpsCommand. Dates are absolute (YYYY-MM-DD, UTC).

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
