# ADR-0002 — DroneOpsSync upload auth model + HTTPS-only base URL

- **Status:** **Accepted** — shipped 2026-04-24. Backend v2.63.4, companion v2.62.0. See §4 "Implementation checkpoint" for the actual delivered scope and references.
- **Date:** 2026-04-24
- **Authors:** Terry (research/architect); implementation handoff to aegis (companion + backend)
- **Scope:** DroneOpsSync companion app + FastAPI flight-library upload endpoints
- **Related ADRs:** `0001-observability.md` (DroneOps). Cross-repo: EyesOn `ADR-0017` / `ADR-0019` (camera-less companion UX), EyesOn `ADR-0020` (managed-tenant discovery). Memory: `feedback_dji_rc_pro_no_camera.md`, `feedback_managed_customer_seamless.md`.
- **Related commits:** `b6d4319` (device-api-key auth + `/device-upload` endpoint, introduced), `d563ad6` (v2.36.0 Capacitor native rewrite), `1544b9e` (v2.34.0 LAN-only fix), `7bf62b7` (v2.32.1 device upload API restoration — operator-corrected from v2.30.0 removal).

---

## 1. Context

### 1.1 Incident (2026-04-24)

Operator's personal primary controller — DJI RC Pro (no usable camera; behaves like `samsung SM-S938U1`-class Android 10 hardware) — running the DroneOpsSync companion app reported two symptom lines against `http://droneops.barnardhq.com`:

```
[HEALTH] GET http://droneops.barnardhq.com/health
        → IOException: Use JsonReader.setLenient(true) to accept malformed JSON at line 1 column 1 path $
[SCAN]   3 file(s) found in /storage/emulated/0/DJI/com.dji.industry.pilot/FlightRecord (2.8 MB + 5.3 MB + 8.9 MB)
[AUTO] Network connected — auto-sync triggered
[UPLOAD] DJIFlightRecord_2026-04-23_[21-11-04].txt
        → HTTP 403 body: {"detail":"Not authenticated"}
```

Three flight records totalling ~17 MB sat on the controller unable to upload. This is the operator's primary field workflow — flight records feed the aircraft lifecycle log, battery maintenance schedule, and the per-flight financial / mission reporting tree.

### 1.2 Why the server is NOT at fault in terms of missing surface

The current backend already has the correct device-auth endpoint surface in `backend/app/routers/flight_library.py`:

- `GET  /api/flight-library/device-health` — device-api-key auth (`X-Device-Api-Key` header → SHA-256 hash lookup in `device_api_keys` table), returns `{status, device_label, parser_available, upload_endpoint}`.
- `POST /api/flight-library/device-upload` — same device-api-key auth, accepts `UploadFile[]` and dedupes by SHA-256.

The auth dependency is `validate_device_api_key` at `backend/app/auth/device.py` — it is correctly wired, correctly hashes the inbound key, correctly enforces `is_active`, and correctly 401s on miss. **That surface works.** It is exercised by the current Capacitor companion in `companion/src/sync.ts`:

- `checkHealth(serverUrl, apiKey)` at line 174 calls `${serverUrl}/api/flight-library/device-health` with `X-Device-Api-Key: <key>`.
- `uploadLogs(serverUrl, apiKey, files, ...)` at line 197 calls `${serverUrl}/api/flight-library/device-upload` with the same header.

The Capacitor client also uses `fetch()` (which silently follows 301/302, accepts any content-type for a 200, and does not use Gson), so a CF HTTP→HTTPS redirect would be handled transparently.

### 1.3 What the symptoms actually prove

Map each log line to the likely cause:

| Symptom | Root cause |
|---|---|
| `GET http://droneops.barnardhq.com/health` (note: no `/api/` prefix) | The deployed APK on the controller is a **pre-v2.33.0 native Android build**, not the current v2.36.x Capacitor build. That old build uses the legacy `/health` path and a Gson-based HTTP client. |
| `Use JsonReader.setLenient(true)` | Confirms a Gson `JsonReader` with default `setLenient(false)`. No Gson anywhere in the current Capacitor tree. The client chokes because CF returned an HTML body (301 redirect to HTTPS, CF block page, or 404 HTML) — not a runtime bug in the HTTP handler. |
| `[UPLOAD] ... → HTTP 403 {"detail":"Not authenticated"}` | FastAPI's default message when **`get_current_user`** (JWT cookie/bearer) fails. Not the device-api-key dependency, which returns `"Invalid or revoked device API key"`. Means the old APK is hitting a JWT-gated endpoint (legacy `/api/flight-library/upload` or similar), not `/device-upload`. |
| Plaintext `http://` base URL | CF 80 → 443 redirect returns an HTML body by default. Any non-redirect-following or non-JSON-tolerant client falls over on response parsing before it can authenticate. |

**Net:** this is not one bug. It is three composing failure modes: (a) stale APK with a stale endpoint contract, (b) stale APK with an HTML-intolerant JSON client, (c) operator-entered `http://` URL that CF rewrites to a body the old client cannot parse. The server-side auth model is fine; the client delivery pipeline is stale.

### 1.4 Hardware + fleet constraints (non-negotiable)

The same constraints that forced EyesOn `ADR-0019` apply to DroneOpsSync and must govern any redesign:

1. **DJI RC Pro has no usable rear camera for field operation.** Any UX requiring the operator to aim the controller at a QR code, a screen, or a paired device is rejected. See `feedback_dji_rc_pro_no_camera.md`.
2. **No hands-free inputs while flying.** Pairing and authorization must be completable in a single setup sitting (pre-flight, on the ground), not mid-mission. Fortunately flight-record upload is a post-flight batch operation — the companion scans idle files and pushes them when the controller reaches Wi-Fi. Auth setup is one-time-per-device and can happen at desk.
3. **Managed customer parity.** Per `feedback_managed_customer_seamless.md`, any auth model that adds taps or manual URL entry for Bill's managed customers (relative to the primary instance) is rejected. As of 2026-04-24 DroneOps has only a gateway-only managed topology (see memory `project_droneops_managed_bos_20260420.md`) with **no live managed tenants** — so the managed-parity constraint is deferred, not absent. Design must not *preclude* matching EyesOn `ADR-0020`-style discovery later.
4. **OTA rules (`feedback_mobile_ota.md`, `feedback_mobile_ota_always.md`).** Runtime version + channel + branch must match. The current companion ships via GitHub Actions on `main` (workflow `companion-apk.yml`, BOS-HQ self-hosted runner per `CHANGELOG.md` 2026-04-21). Fleet-wide rollout of a new client build is an APK rebuild + push — already the established workflow.

### 1.5 Why not just tell the operator to update the APK?

Update-on-demand fails closed here because:

- The stale APK's health check hits `/health` (unprefixed), which never existed on the FastAPI surface — the CF tunnel's fall-through routes root `/health` to the frontend nginx which returns an HTML page. The old client cannot parse the response, cannot confirm server reachability, and will not invoke an OTA check (if OTA even exists in that old build — pre-v2.33.0 Capacitor rewrite, it likely does not).
- Even a fresh v2.36.x APK install without a valid **`X-Device-Api-Key`** fails: `DEFAULT_SERVER_URL` was blanked at v2.34.0 (commit `1544b9e`) to stop leaking internal IPs, and the settings screen requires the operator to paste both a URL and an API key. There is no discovery path. If Bill does not remember to also provision a device key in Settings → Device Access on the server, the upload 401s the same way.
- Older APKs on the operator's personal device (and potentially on dispatched fleet controllers) cannot be uninstalled by the server. The path forward must both ship a corrected client and make the older client's failure diagnostic rather than silent.

---

## 2. Decision

### 2.1 Auth model — keep `X-Device-Api-Key`, formalize its enrollment

The SHA-256-hashed `X-Device-Api-Key` header is the correct model for DJI RC Pro flight-record uploads. It is:

- **Non-interactive** — no human login, no OAuth flow, no refresh token dance. The controller is unattended post-flight and runs auto-sync when it reaches Wi-Fi.
- **Revocable** — `is_active` flag + last-used-at timestamp already in `device_api_keys`. One device lost, one row updated.
- **Fleet-safe** — per-device labels let an admin see "DJI RC Pro #3 — Bill personal" in the UI and revoke without disrupting the other 20 controllers.
- **Managed-compatible** — nothing about the scheme changes between primary and managed instances. An enrollment endpoint can be added later that matches EyesOn `ADR-0020`'s discovery pattern without schema change.

**Rejected alternatives:**

- **JWT bearer from a service account.** Requires periodic refresh. Controllers fly in LTE-roaming conditions; a refresh that fails mid-flight-record-upload would have to be retried without operator attention. Added complexity with no gain — the device key already has a revocation primitive.
- **mTLS client certs.** Certificate provisioning on DJI RC Pro / DJI Pilot 2 context is fragile; revocation is painful. Same end-state as the API key without the operational ergonomics.
- **OAuth device-flow.** Requires an interactive browser step on the controller, which under DJI Pilot 2 foreground ownership is impractical. And unnecessary — the trust boundary is device-level, not user-level.

### 2.2 Base URL — HTTPS-only, pre-baked default, no plaintext accepted

Two changes:

1. **Companion must refuse `http://` base URLs.** `saveConfig` in `companion/src/sync.ts` should reject any URL whose scheme is not `https:` (outside `localhost`/`127.0.0.1` for developer builds). The `testConnection` path and the `uploadLogs` path both need the same guard. Operator-entered `http://droneops.barnardhq.com` is the trigger for the CF redirect / HTML body → parser-crash chain. Removing the allowance removes the chain.
2. **Pre-baked `DEFAULT_SERVER_URL = "https://droneops.barnardhq.com"` for the primary instance**, matching the EyesOn `ADR-0019` `BuildConfig.DEFAULT_SERVER_URL` pattern. Commit `1544b9e` blanked the default to stop leaking an internal LAN IP (`10.50.0.5`-style) in public APKs; that rationale is correct but was implemented too aggressively. Pre-baking the **public** DroneOps URL leaks nothing — it is already on `barnardhq.com` DNS, already the landing page, and already the only sensible target for a responder who picks up a controller and taps "Start Sync".

For managed tenants (when the first one goes live): the discovery pattern from EyesOn `ADR-0020` should be reused. The main server exposes `GET /api/discovery/pair/:code` and fans out to every tenant URL in `MANAGED_TENANT_URLS`. Each tenant exposes `GET /api/companion/pair/:code/exists` (boolean-only, no PII). A responder types a 6-digit code → companion hits discovery → adopts the tenant URL. No manual URL entry. This is deferred (no tenants live) but **codified as the forward path**, not "we will decide later".

### 2.3 Client reliability — kill Gson, kill old endpoint paths

The current Capacitor companion already avoids Gson (uses `fetch()` + JSON). Two enforcement points:

1. **Ship a v2.36.x APK to the operator's controller immediately.** Aegis's fix is producing this; the artifact is the CI workflow `companion-apk.yml` publishing to `downloads/droneopssync.apk` (the DroneOps analog of EyesOn's `downloads/eyeson-companion.apk`).
2. **Server-side compatibility shim for the stale `/health` path** — add a thin handler in `backend/app/main.py` that responds to unauthenticated `GET /health` with a valid `application/json; charset=utf-8` body `{"status":"update-required","upgradeUrl":"<APK URL>","minCompanionVersion":"2.36.0"}`. A stale Gson client will still crash if the response lies outside its grammar, but a lenient one will display the banner; more importantly, the server log line tells us a stale client is in the field. Caveat: the old client's Gson `setLenient(false)` will still choke on valid JSON if it expects a specific shape — so this shim is defensive logging and graceful-path for anything in between, not a revival of the old endpoint.

### 2.4 Observability — audit trail for uploads + stale-client detection

- Log every device-upload call at INFO: `{event:"device_upload", device_label, key_prefix, file_count, total_bytes, imported, skipped, errors}`. The `key_prefix` is the first 8 chars of the SHA-256 hash (not the raw key) — enough to correlate without leaking the secret.
- Log every `validate_device_api_key` failure at WARN: `{event:"device_auth_failed", key_prefix, ip, user_agent}`. Already partially present; formalize the schema to align with `docs/adr/0001-observability.md` Phase 5 JSON logging.
- Add a Grafana panel on the DroneOps dashboard: "Device upload success rate (1h)", "Active device keys (last 24h)", "Stale client hits on `/health`". The stale-client panel is the tripwire for this kind of incident recurring on a different controller.

---

## 3. Consequences

### 3.1 Positive

- The operator's 3 stuck flight records land on the server as soon as the new APK installs and a device key is provisioned. No infrastructure change required; the endpoints already exist.
- Future stale-APK incidents are caught by the `/health` shim log, not by the operator noticing broken sync after a flight.
- HTTPS-only + pre-baked URL eliminates the CF-redirect-HTML-body class of failure for every controller, present and future.
- The auth model is already managed-tenant-compatible when the first DroneOps managed customer ships — no schema change needed, only a discovery endpoint (deferred per §2.2).
- The scheme mirrors EyesOn's validated ADR-0019/0020 stack, so operators holding both products get a consistent onboarding story.

### 3.2 Negative / cost

- Any field controller still running a pre-v2.36.x APK must be physically updated (OTA or sideload). Fleet audit is needed — how many field controllers exist, what version each runs. Likely small (operator's personal + a handful of dispatched FD controllers), but must be catalogued.
- The `saveConfig` `http://` rejection is technically a breaking change for the one degraded-test-LAN scenario; mitigated by allowing `http://localhost` and `http://127.0.0.1` for dev builds.
- Observability additions touch the same Python logging path as `ADR-0001` — no conflict, but the two dashboards should be versioned together.

### 3.3 Risks and mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| New APK ships but the operator's device key is missing or stale | Medium | High (upload still 401s, operator sees no win) | Aegis fix must include a pre-ship verification step: key provisioned + labeled "Bill personal RC Pro" + Settings → Device Access screenshot captured in PROGRESS.md |
| Stale-client log-noise overwhelms INFO rate limit | Low | Low | Sample at 1/min/IP after first hit. Stale-client alert should fire once per IP per day, not once per request. |
| HTTPS-only rejection breaks legitimate LAN-only deploys | Low | Medium | Allow `http://10.0.0.0/8`, `http://172.16.0.0/12`, `http://192.168.0.0/16`, `http://localhost`, `http://127.0.0.1`. Reject everything else. Same RFC-1918 carve-out EyesOn's `isPrivateAddress` helper already implements. |
| Managed-tenant discovery delayed until first live tenant, and the first tenant is urgent | Low (no tenants in pipeline) | Medium (if urgent) | §2.2 explicitly references the EyesOn `ADR-0020` pattern as the forward path; a copy-paste-with-rename job estimated at 1-2 eng days when needed. |

---

## 4. Implementation checkpoint

Delivered 2026-04-24 by aegis. Backend v2.63.4, companion v2.62.0. Single commit on `main`.

Shipped scope:

- **Server** (`backend/` v2.63.4):
  - `GET /health` top-level alias in `backend/app/main.py` (matches stale-client + CF-probe expectations; returns same JSON as `/api/health`).
  - Structured INFO log on `/api/flight-library/device-upload`: `{event, device_label, device_id, file_count, total_bytes, imported, skipped, error_count}`. Zero raw-key exposure.
  - Structured WARN log in `backend/app/auth/device.py` on auth failure: `{event, key_prefix, ip, user_agent, path}`.
- **Companion** (`companion/` v2.62.0):
  - `validateServerUrl()` exported from `companion/src/sync.ts`. Applied in `saveConfig`, `checkHealth`, `uploadLogs`. RFC-1918 + loopback + link-local carve-out for LAN deploys.
  - `DEFAULT_SERVER_URL = "https://droneops.barnardhq.com"` (pre-baked).
  - `App.tsx::saveAndSync` surfaces validation errors in the Settings banner.
  - Footer version bumped to v2.62.0.
- **Operator** (one-time): install `DroneOpsSync-2.62.0.apk` once GH Actions publishes it; reuse the existing `M4TD` device key raw value (already in DB, last-used 2026-04-19; no rotation).

End-to-end verification against production (`droneops.barnardhq.com` via CF tunnel on BOS-HQ):

- `POST /api/flight-library/device-upload` with valid key + multipart file → HTTP 200 + well-formed `FlightUploadResponse`. Confirms the device-auth + upload path is healthy; the user-visible failure was 100% stale-APK.

**Deferred / out of scope for this commit** (explicitly noted per ADR-0002 §5):

- Fleet audit of all DJI controllers and their APK versions.
- Managed-tenant discovery endpoint (no tenants live).
- Device-key rotation policy (revoke-on-demand remains the only mechanism).
- The ADR §2.3 "update-required" JSON banner on `/health` — rejected in favor of a plain JSON alias. A stale pre-v2.34 APK with a strict Gson client cannot consume a nonstandard banner field anyway; the diagnostic path is the WARN log on auth-failure, not the `/health` body.

---

## 5. Open questions

- **Fleet audit** — how many DJI RC Pro / DJI Pilot 2 controllers are in the field, and what APK version does each run? Needed before a fleet-wide OTA push can be planned.
- **Managed tenant timeline** — is any DroneOps managed customer in the pipeline that would force §2.2's discovery endpoint to move from deferred to blocker?
- **Device key lifecycle** — is there an expiry or rotation policy for `device_api_keys`? Current model is revoke-on-demand only; 90-day rotation with a grace window might be worth adding, but is out of scope for this incident.
