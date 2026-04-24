# DroneOpsCommand — Roadmap

Forward-looking work items. Shipped scope is tracked in `CHANGELOG.md`;
in-flight scope is tracked in `PROGRESS.md`. This file holds only
not-yet-started work with a clear trigger, scope, and ADR/decision
reference where applicable.

## Observability + Fleet Hygiene (follow-ups from ADR-0002, 2026-04-24)

**Context.** ADR-0002 shipped the primary fix for the operator's DJI RC
Pro flight-record upload failure (HTTPS-only base URL + fresh Capacitor
APK + existing `X-Device-Api-Key` auth). The follow-ups below are
legitimately deferred — not load-bearing for the primary fix, but
necessary to prevent the class of failure from recurring silently on
a different controller.

### FU-1 — Fleet APK version audit

- **Scope.** Enumerate every DJI RC Pro / DJI Pilot 2 / DJI Fly device
  that has ever paired with DroneOps. Cross-reference against
  `device_api_keys.last_used_at` and, where possible, any version/UA
  hint the server logged on upload.
- **Trigger.** Before any fleet-wide OTA push of the new v2.36.x
  DroneOpsSync APK can be planned.
- **Deliverable.** `scripts/audit-device-fleet.py` + a one-page
  operator report listing each device label, last-seen timestamp, last
  observed APK version (if known), and upgrade plan (OTA-capable vs
  sideload-required).
- **Owner.** TBD. Likely ~1 eng day.

### FU-2 — Unauthenticated `GET /health` shim — ✅ SHIPPED v2.63.4 (2026-04-24)

- Delivered as a plain JSON alias (same payload as `/api/health`).
  Reasoning for deviating from the spec'd "update-required" banner: a
  pre-v2.34 Gson client with `setLenient(false)` would choke on any
  payload that doesn't match its expected shape, so custom banner
  fields buy nothing on the failing client and are confusing to
  modern clients. The WARN log on auth-failure in
  `backend/app/auth/device.py` is the actual stale-client tripwire
  (key_prefix + IP + user-agent + path); FU-3's Grafana panel consumes
  that stream directly. FU-2 rate-limiting not needed since `/health`
  never triggers the WARN path.

### FU-3 — Grafana stale-client tripwire

- **Scope.** Add a panel on the DroneOps Grafana dashboard for
  "Device auth failures (24h)" sourced from the structured WARN log
  emitted by `validate_device_api_key` in
  `backend/app/auth/device.py` (fields: `key_prefix`, `ip`,
  `user_agent`, `path`). Pushover alert at ≥5 hits/24h from ≥2
  distinct IPs (filters out a single responder testing an old phone).
- **Trigger.** v2.63.4 is live. Panel can be shipped any time.
- **Deliverable.** Grafana JSON + alert rule in `~/noc-master` config.
- **Owner.** TBD. ~0.5 eng day.

### FU-4 — Device-key lifecycle policy

- **Scope.** Decide whether `device_api_keys` rows should have an
  automatic expiry/rotation (e.g., 90-day TTL with a 7-day grace
  window where both old+new keys are accepted), or stay indefinite
  revoke-on-demand. Current model is revoke-on-demand only.
- **Trigger.** Before the first real managed-tenant ships (managed
  operators are less likely to tolerate indefinite keys).
- **Deliverable.** One-page decision doc as ADR-0003 or ADR-0004
  (pick next free number), plus schema migration if rotation is
  adopted.
- **Owner.** TBD.

### FU-5 — Managed-tenant discovery (EyesOn ADR-0020 parity)

- **Scope.** When a first DroneOps managed customer is committed,
  port EyesOn's `GET /api/discovery/pair/:code` fan-out pattern +
  tenant-side `GET /api/companion/pair/:code/exists` boolean-only
  endpoint to DroneOps. Companion types a 6-digit code, hits
  discovery on the primary, adopts the tenant URL. No manual URL
  entry, matching `feedback_managed_customer_seamless.md`.
- **Trigger.** First DroneOps managed customer signed / deployment
  scheduled.
- **Deliverable.** Server endpoints + companion integration +
  `MANAGED_TENANT_URLS` env var plumbing on the primary instance's
  `docker-compose.yml`. Copy-paste-with-rename from
  `eyeson-managed`/`EyesOn` repos; estimated 1-2 eng days.
- **Owner.** TBD.

### FU-6 — End-to-end test for `device-upload` auth path

- **Scope.** Integration test that provisions a `DeviceApiKey`,
  hits `/api/flight-library/device-health` with the raw key, then
  `/api/flight-library/device-upload` with a sample DJI flight
  record fixture, and asserts the `Flight` row is created with the
  expected `source_file_hash`. Today the backend has unit coverage
  for the auth dependency; the full upload pipeline is untested
  end-to-end.
- **Trigger.** Any time; good hygiene regardless of ADR-0002's
  immediate fix.
- **Deliverable.** `backend/tests/test_device_upload.py` +
  fixture log file in `backend/tests/fixtures/flight-records/`.
- **Owner.** TBD. ~0.5 eng day.

---

## Older roadmap items

None yet captured here. When a new forward-looking plan is drafted,
append it under its own heading with the same Scope / Trigger /
Deliverable / Owner block structure.
