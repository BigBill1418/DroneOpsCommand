# ADR-0003 — Zero-touch device API key rotation

- **Status:** **Accepted** — shipped 2026-04-24. Backend v2.63.6. Paired Kotlin client release: DroneOpsSync v1.3.25.
- **Date:** 2026-04-24
- **Authors:** aegis (remote routine `trig_01KiBK88vqs6vtRf75rkxcw8` re-run after empty-branch failure)
- **Scope:** DroneOpsCommand `backend/` device-API-key auth + admin rotation endpoint + Celery finalizer
- **Related ADRs:** [`0001-observability.md`](./0001-observability.md), [`0002-droneopssync-upload-auth.md`](./0002-droneopssync-upload-auth.md). Cross-repo: DroneOpsSync `docs/adr/0002-zero-touch-device-key-rotation-client.md`.
- **Plan:** [`docs/plans/2026-04-24-zero-touch-key-rotation.md`](../plans/2026-04-24-zero-touch-key-rotation.md)
- **Memory:** `feedback_dji_rc_pro_no_camera.md`, `project_droneopssync_upload_fix_20260424.md`

---

## 1. Context

ADR-0002 §6 left "device key lifecycle" as an open question: the only mechanism today is revoke-on-demand. The 2026-04-24 incident made the cost of that gap concrete: when Bill rotated his RC Pro's `M4TD` key to recover the upload path (Capacitor `Preferences` drift had silently broken the controller), he then had to walk to the controller and manually paste the new key. That tap is exactly the kind of operator interaction the camera-less-controller program is built to remove.

The constraint set is the same one ADR-0002 §1.4 enumerates:

1. DJI RC Pro has no usable rear camera. No QR, no visual pairing.
2. No hands-free mid-mission inputs. Pairing setup is desk-time only.
3. Pre-flight workflow is **post-flight batch upload** — the controller already runs preflight calls before each upload attempt; that channel is the right place to deliver any new credentials.
4. Managed-tenant parity: nothing about the design may add taps for future managed-DroneOps customers.

Each controller has its own key (operator confirmed "none are shared"). Design must be strictly per-device — never shared, never broadcast.

---

## 2. Decision

### 2.1 24-hour grace window with dual-key auth

Add two nullable columns to `device_api_keys`:

| Column                  | Type          | Meaning                                                           |
|-------------------------|---------------|-------------------------------------------------------------------|
| `rotated_to_key_hash`   | `VARCHAR(64)` | SHA-256 of the new raw key during the grace window.               |
| `rotation_grace_until`  | `TIMESTAMP`   | UTC instant the grace window closes.                              |

While `rotation_grace_until > now()`, **either** the primary `key_hash` **or** the `rotated_to_key_hash` authenticates a request. The auth dep tags the matched row with a transient `_authenticated_via_old_key` attribute so the device-health endpoint can decide whether to deliver the new key as a hint in the response body.

24h is the chosen grace window because it covers one full operational day for a controller that may sit idle between flights, and is short enough to contain compromise (a leaked old key has at most 24h of validity).

### 2.2 Raw new key — Redis side-channel, not DB

The DB never holds raw keys (only SHA-256 hashes). To deliver the new raw key to the OLD-key-authenticated request, we stash the raw value in Redis under `doc:rotation:hint:{device_id}` with a TTL matching the grace window. Redis is already a hard dependency of the backend (Celery broker), so this introduces no new infrastructure.

The hint is read **only** by the device-health endpoint, **only** when the request authenticated via the OLD key. It is written **only** by the rotation endpoint. It is deleted by the Celery finalizer (best-effort; the TTL handles eviction otherwise).

If Redis is unreachable at rotation time, the rotation endpoint **fails closed** (HTTP 503, no DB write) — without the hint the device cannot pick up the new key, so writing the DB row would be functionally pointless and would produce a confusing half-state.

### 2.3 Admin endpoint — `POST /api/admin/devices/{device_id}/rotate-key`

- Auth: `Depends(get_current_user)` — same gate the existing `/api/settings/device-keys/*` endpoints use. The project does not have role distinctions; §6 flags RBAC as follow-up.
- Body: none.
- Returns: `{id, label, raw_key, rotation_grace_until}` — the raw key is returned exactly **once**.
- Side effects: Redis SET with TTL, DB UPDATE, single Pushover FYI (best-effort).
- Errors:
  - 404 if device not found
  - 409 if a rotation is already in flight (overlapping rotations are a UX trap; operator must wait or revoke + recreate)
  - 503 if Redis unreachable

### 2.4 Device-health hint — `GET /api/flight-library/device-health`

When the request authenticated via the OLD key during grace, the response body includes:

```json
{
  ...existing fields...,
  "rotated_key": "<new raw key>",
  "rotation_grace_until": "<iso8601 UTC>"
}
```

Otherwise the fields are omitted. Existing clients that don't know about these fields keep parsing the response unchanged (Gson on the Kotlin side ignores unknown fields by default).

If Redis can't be read at hint-emission time, the fields are omitted and the device retries on the next preflight tick. Graceful degradation per repo CLAUDE.md §Logging.

### 2.5 Celery finalizer — `finalize_key_rotations_task`

Beat schedule: every 15 minutes (crontab `minute='*/15'`, offset from the silence watchdog at minute=17). For each row where `rotation_grace_until < now()`:

1. Promote `rotated_to_key_hash` → `key_hash`.
2. Clear both grace columns.
3. Delete the Redis hint (best-effort).

After this task runs, the OLD key no longer authenticates. Any controller that didn't pick up the new key during grace will get 401s (and ADR-0002 §5.4 first-401 Pushover) — the same fail-loud surface that exists today.

### 2.6 Pushover FYI

Single informational notification per rotation:

- Title: `DroneOps key rotated`
- Body: `Rotated device key for <controller_label>. Grace ends <iso8601>. Controllers will pick up the new key on next sync — no action needed.`
- Priority: 0
- No dedup (every rotation is worth knowing about).
- Best-effort: Pushover failure does not fail the rotation.

Env-gated identically to ADR-0002 §5: requires `PUSHOVER_TOKEN` + `PUSHOVER_USER_KEY`.

---

## 3. Consequences

### 3.1 Positive

- Operator interaction on the controller for a server-side key rotation drops from "walk-to-device + paste" to **zero**. The controller picks up the new key on its next preflight call.
- Old key continues to authenticate for 24h, so an in-flight upload during the rotation never breaks.
- The mechanism is per-device — rotating one controller's key does not affect any other.
- Compatible with future managed-tenant work (ADR-0020 in EyesOn): the dual-key path is independent of the tenant routing.
- Audit trail via existing structured INFO logs (`rotate_key_*`, `rotate_key_finalized`).
- Failure mode degrades to today's behaviour — operator can still paste manually if the device was offline for the full grace window.

### 3.2 Negative / cost

- Adds two nullable columns to `device_api_keys`. Additive only; failover-safe per CLAUDE.md §Failover Guard.
- Adds one Redis dependency at rotation time (pre-existing dependency at the runtime level; not a new one).
- The new key value is in the device-health response body during grace — TLS is mandatory (already enforced — companion v2.62.0 `validateServerUrl()` and DroneOpsSync `ApiClient.normalizeUrl()` both upgrade public hosts to HTTPS).

### 3.3 Risks and mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Two operator-initiated rotations within the same grace window | Low | Medium (would lose track of the new key) | Endpoint returns 409 if `rotation_grace_until > now()`; operator must wait or revoke + recreate |
| Redis evicts the hint before the controller picks it up | Low | Medium (controller keeps using old key during grace) | Old key still authenticates during grace; operator can re-trigger `/rotate-key` to re-set the hint |
| Controller offline for the full 24h grace | Low | Medium (controller back online to find old key 401s) | Falls back to today's behaviour (paste manually). ADR-0002 §5.4 first-401 Pushover catches the case for the operator |
| New key leaks via the device-health response body | Very low | High | TLS-only transport (enforced in client); no INFO logging of the raw key (only `new_key_prefix`); response is gated on `_authenticated_via_old_key=True` so a random unauthenticated client cannot probe for it |
| Migration adds columns but old code is up | Negligible | None | Old code never reads the new columns; behaviour unchanged for non-rotation paths |

---

## 4. Implementation map

| Concern                              | File                                                   |
|--------------------------------------|--------------------------------------------------------|
| Schema columns                       | `backend/app/models/device_api_key.py`                 |
| Migration (additive ALTER)           | `backend/app/main.py::_add_missing_columns`            |
| Auth dep (dual-key match)            | `backend/app/auth/device.py`                           |
| Rotation endpoint                    | `backend/app/routers/admin_device_rotation.py`         |
| Redis hint side-channel              | `backend/app/services/rotation_hint.py`                |
| Device-health hint emission          | `backend/app/routers/flight_library.py::device_health` |
| Celery finalizer + beat              | `backend/app/tasks/celery_tasks.py`                    |
| Tests                                | `backend/tests/test_device_key_rotation.py`            |

15 unit tests (`pytest tests/test_device_key_rotation.py -v`) — all green.

---

## 5. Failover implications

Per CLAUDE.md §Failover Guard:

1. **Streaming replication** — only adds nullable columns, no PK/FK/index changes. Standby promotes with the same idempotent `_add_missing_columns` code path.
2. **Container recreation** — DDL is run from `_add_missing_columns` at lifespan start; idempotent.
3. **Blue-green swap** — grace state is durable in DB; survives a swap. Redis hint is per-grace-window-only and would be lost in a Redis-only failover, but the DB grace columns remain — operator can re-trigger rotation if needed.
4. **Failover engine** — no quorum-relevant fields touched.
5. **Customer-facing during failover** — rotation is operator-only (admin endpoint); upload paths keep working under either old or new key during grace.

✅ Failover-safe.

---

## 6. Open questions / follow-ups

- **RBAC on the rotation endpoint.** Today any authenticated user is admin-equivalent. Future ADR should add a roles model and restrict `/api/admin/*` to admins.
- **Bulk rotation.** This ADR is one-device-at-a-time. A "rotate all" admin action would benefit fleets at scale (e.g. operator response to a credential leak).
- **Audit log table.** Today we log to structured JSON (Loki). A first-class `device_api_key_audit` table would be more queryable for compliance.
- **Client behaviour on rotation pickup.** Documented in DroneOpsSync `docs/adr/0002-zero-touch-device-key-rotation-client.md`.
