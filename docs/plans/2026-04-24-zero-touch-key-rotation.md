# Plan — Zero-touch device API key rotation (DroneOpsSync v1.3.25)

- **Date:** 2026-04-24
- **Author:** aegis (remote routine `trig_01KiBK88vqs6vtRf75rkxcw8` re-run after empty-branch failure)
- **Repos:** DroneOpsCommand (`backend/`) + DroneOpsSync (Kotlin Android)
- **Related:**
  - DroneOpsCommand `docs/adr/0002-droneopssync-upload-auth.md` §6 (open question on key lifecycle)
  - DroneOpsCommand `docs/adr/0003-zero-touch-device-key-rotation.md` (this plan, formalized as ADR)
  - DroneOpsSync `docs/adr/0002-zero-touch-device-key-rotation-client.md`
  - Memory: `feedback_dji_rc_pro_no_camera.md`, `project_droneopssync_upload_fix_20260424.md`

---

## 1. Problem

When the operator rotates a device API key server-side (today this means: directly editing `device_api_keys.key_hash` in Postgres), the paired DJI RC Pro keeps trying the **old** key, gets HTTP 401 on every preflight, and the operator has to physically tap the controller to paste the new key. That tap is exactly the kind of operational interaction the camera-less-controller program (ADR-0019 in EyesOn, ADR-0002 in DroneOpsCommand) is supposed to eliminate. The 2026-04-24 incident proved this: Bill rotated `M4TD` to recover the upload path, then had to walk to the RC Pro to paste it.

Each controller has its own key (operator confirmed "none are shared"). The design is strictly per-device — never shared, never broadcast.

---

## 2. Goal

After an operator rotates a device's API key on the server, the paired controller picks up the new key automatically on its next preflight call, with zero physical interaction on the controller. The operator sees a transient "API key auto-updated" toast on the controller (when it next opens the app) and never has to type the key.

---

## 3. Design — server-side (DroneOpsCommand)

### 3.1 Schema additions

`device_api_keys` table gains two nullable columns:

| Column                  | Type         | Meaning                                                                                  |
|-------------------------|--------------|------------------------------------------------------------------------------------------|
| `rotated_to_key_hash`   | `VARCHAR(64)` | SHA-256 of the new raw key during the grace window. NULL outside grace.                  |
| `rotation_grace_until`  | `TIMESTAMP`  | UTC instant the grace window ends. NULL outside grace.                                   |

**Why hash, not raw.** The existing model never stores raw keys (only `key_hash`). The new column mirrors that invariant. The rotation endpoint returns the raw key **once**, in the response body, exactly the way `POST /api/settings/device-keys` already does on creation. The raw key is also held server-side **transiently** in a Redis SETNX for the grace window so the device-health endpoint can return it as a hint to the OLD-key-authenticated request — without that, the device has no way to learn the new value.

A SHA-256 hash is one-way; we cannot derive the raw key from `rotated_to_key_hash` alone. So the design uses a Redis side-channel:

- Key: `doc:rotation:hint:{device_id}`
- Value: the new raw key
- TTL: matches `rotation_grace_until - now` (24h maximum)

The hint is read **only** when the request authenticated via the OLD key on the device-health path. It is written **only** at rotation time. It is purged when:
1. The grace window expires (Redis TTL handles this).
2. The Celery finalizer promotes the new key (explicit DEL).
3. The rotation is cancelled (out of scope for v1; revoke is a normal `DELETE /api/settings/device-keys/{id}`).

The Redis dependency is acceptable: Redis is already a hard dependency of the backend (Celery broker). If Redis is unreachable the rotation endpoint fails closed (5xx + log) — no half-state.

### 3.2 Migration

Single Alembic-style migration delivered through the existing `_add_missing_columns` mechanism in `backend/app/main.py`. We do NOT bring up the Alembic toolchain in this PR — the project's pattern is additive `ALTER TABLE` at lifespan start, gated by `inspector.has_table` + column existence. Mirroring that pattern preserves blue-green replication safety: the standby will run the same additive ALTER on next promotion, no DDL desync.

Migration entries:

```python
"device_api_keys": [
    ("rotated_to_key_hash",  "ALTER TABLE device_api_keys ADD COLUMN rotated_to_key_hash VARCHAR(64)"),
    ("rotation_grace_until", "ALTER TABLE device_api_keys ADD COLUMN rotation_grace_until TIMESTAMP"),
],
```

Failover safety check (per repo CLAUDE.md §Failover Guard):

1. Streaming replication: only adds nullable columns, no PK/index changes, no port shifts. ✅
2. Container recreation: the migration is in `_add_missing_columns` which runs on every lifespan start. Idempotent. ✅
3. Blue-green swap: standby → primary promotion runs the same additive code. ✅
4. Failover engine: no quorum-relevant fields touched. ✅
5. Customer-facing during failover: rotation is operator-only, runs from the admin UI; flight-record uploads keep working under primary OR rotated_to keys. ✅

### 3.3 Auth dep changes (`backend/app/auth/device.py`)

`validate_device_api_key` becomes dual-key aware:

```
hash = SHA-256(X-Device-Api-Key)
SELECT * FROM device_api_keys
  WHERE is_active=true
    AND (key_hash = hash
         OR (rotated_to_key_hash = hash
             AND rotation_grace_until IS NOT NULL
             AND rotation_grace_until > now()))
```

We attach a flag to the returned `DeviceApiKey` ORM instance via SQLAlchemy `_authenticated_via_old_key` attribute (transient — never persisted). The device-health response handler reads this flag to decide whether to emit the `rotated_key` hint.

If `rotation_grace_until` has passed and only the OLD-key matches, we return 401 — grace expired means the rotation has been finalized.

### 3.4 Rotation endpoint

`POST /api/admin/devices/{device_id}/rotate-key`

- **Auth:** `_user: User = Depends(get_current_user)` — same admin auth as the existing `/api/settings/device-keys/*` endpoints (the project does not have role distinctions today; any authenticated user is admin-equivalent. ADR-0003 §6 flags this for follow-up RBAC work).
- **Path under** `/api/admin/...` per the routine spec — new router file `backend/app/routers/admin_device_rotation.py` registered in `main.py`.
- **Body:** none (no input required).
- **Behaviour:**
  1. Generate `new_raw_key = secrets.token_urlsafe(32)` formatted as `doc_<label>_<base64>` matching existing convention. (Inspect `routers/device_keys.py::create_device_key` — convention is just `secrets.token_urlsafe(32)`. We mirror that — the `doc_m4td_…` shape was a manual operator choice for the M4TD row, not a code convention.)
  2. Compute `new_hash = SHA-256(new_raw_key)`.
  3. Set `rotated_to_key_hash = new_hash`, `rotation_grace_until = now() + 24h`.
  4. SETEX `doc:rotation:hint:{device_id}` to `new_raw_key` with TTL = 24h.
  5. Commit.
  6. Fire informational Pushover (best-effort, never blocks).
  7. Log INFO at entry, success, failure.
  8. Return 200 with `{id, label, raw_key, rotation_grace_until}`. **The raw key is returned EXACTLY ONCE.**
- **Errors:**
  - 404 if device not found.
  - 409 if a rotation is already in flight (grace window still open) — operator must wait or revoke first.
  - 503 if Redis unreachable (we cannot deliver the hint to the device, so the rotation is functionally pointless — fail closed).

### 3.5 Device-health response hint

`GET /api/flight-library/device-health` currently returns:

```json
{ "status": "connected", "device_label": "…", "parser_available": true, "upload_endpoint": "…" }
```

Add — only when authenticated via OLD-key during grace:

```json
{
  …existing fields…,
  "rotated_key": "<new raw key>",
  "rotation_grace_until": "<iso8601 UTC>"
}
```

Source of `rotated_key` is the Redis hint (`doc:rotation:hint:{device_id}`). If Redis is down and we cannot read the hint, we omit both fields and log a WARN — the device just retries on the next preflight. Graceful degradation per repo CLAUDE.md §Logging.

We do NOT include the new key when authenticated via the NEW key. That is the steady-state path; the hint already landed.

### 3.6 Celery finalizer task

`finalize_key_rotations_task` — new Celery task in `backend/app/tasks/celery_tasks.py`:

```
SELECT * FROM device_api_keys
 WHERE rotation_grace_until IS NOT NULL
   AND rotation_grace_until < now()
```

For each row:

1. Compute new state: `key_hash = rotated_to_key_hash`, clear `rotated_to_key_hash` and `rotation_grace_until`.
2. DEL `doc:rotation:hint:{device_id}` from Redis (best-effort).
3. UPDATE the row.
4. Log INFO start/per-row/end.

Beat schedule: every 15 minutes, crontab `minute=*/15`. We avoid colliding with the existing `device-silence-watchdog` (minute=17) by simply running on `minute={0,15,30,45}`.

### 3.7 Pushover FYI on rotation

On `POST /rotate-key` success — single message:

- **Title:** `"DroneOps key rotated"`
- **Body:** `"Rotated device key for <controller_label>. Grace ends <iso8601>. Controllers will pick up the new key on next sync — no action needed."`
- **Priority:** 0
- **Dedup:** none (one rotation = one alert; if operator rotates twice that's worth knowing).
- **Failure handling:** swallow Pushover HTTP errors; log WARN; continue. Rotation success does not depend on Pushover delivery.

### 3.8 Tests

`backend/tests/test_device_key_rotation.py` covering:

1. `test_dual_key_auth_during_grace` — old + new key both authenticate while `rotation_grace_until > now()`.
2. `test_old_key_rejected_after_grace` — old key returns 401 once grace has expired.
3. `test_new_key_rejected_before_rotation` — random key returns 401.
4. `test_device_health_includes_rotated_key_for_old_key` — preflight on OLD key includes `rotated_key` + `rotation_grace_until`.
5. `test_device_health_omits_rotated_key_for_new_key` — preflight on NEW key omits both fields.
6. `test_finalize_promotes_after_grace` — Celery task promotes `rotated_to_key_hash` → `key_hash` and clears grace columns.
7. `test_finalize_skips_active_grace` — Celery task leaves rows alone while grace is still open.
8. `test_pushover_fired_on_rotation` — mock Pushover client; rotation endpoint dispatches one alert.
9. `test_rotation_endpoint_409_if_grace_active` — second rotation while first is in flight returns 409.

The existing repo has no `backend/tests/` infrastructure (`find -name pytest.ini` returns empty). This PR bootstraps it:

- Add `backend/tests/__init__.py` (empty marker)
- Add `backend/tests/conftest.py` — async pytest fixture using SQLite in-memory + `aiosqlite` (project uses `asyncpg` in prod; SQLite avoids requiring a live Postgres for unit tests). The auth dep is DB-agnostic.
- Add `backend/requirements-dev.txt` — `pytest`, `pytest-asyncio`, `aiosqlite`, `httpx[http2]` for AsyncClient. Pin to versions compatible with `sqlalchemy[asyncio]==2.0.36`.
- Add `backend/pytest.ini` with `asyncio_mode=auto`.

We use a Redis fake (fakeredis) so the hint flow can be exercised without a live broker. fakeredis is a one-line dependency, very stable, the de-facto standard in async-Python tests.

### 3.9 Version bump (per repo CLAUDE.md)

Current `backend/app/main.py` ships `version="2.63.5"`. New version: `2.63.6`. Bumped in:

1. `README.md` — `**Version 2.63.6**`
2. `frontend/package.json`
3. `backend/app/main.py` — `version="2.63.6"`
4. `frontend/src/components/Layout/AppShell.tsx` — `v2.63.6`

### 3.10 Documentation

- `docs/adr/0003-zero-touch-device-key-rotation.md` — full ADR (problem / decision / consequences / migration / failover impact / cross-link to ADR-0002).
- `CHANGELOG.md` — top entry under `## [2.63.6] — 2026-04-24` with summary + ADR cross-ref.
- `ROADMAP.md` — close FU-7.
- `PROGRESS.md` — new 2026-04-24 EVENING section.

---

## 4. Design — device-side (DroneOpsSync, Kotlin)

### 4.1 Typed model

Replace `Response<Map<String, Any>>` on `DroneOpsSyncService.deviceHealth` with `Response<DeviceHealthResponse>`:

```kotlin
data class DeviceHealthResponse(
    @SerializedName("status")               val status: String,
    @SerializedName("device_label")         val deviceLabel: String? = null,
    @SerializedName("parser_available")     val parserAvailable: Boolean? = null,
    @SerializedName("upload_endpoint")      val uploadEndpoint: String? = null,
    @SerializedName("rotated_key")          val rotatedKey: String? = null,
    @SerializedName("rotation_grace_until") val rotationGraceUntil: String? = null,
)
```

Gson silently ignores unknown JSON fields by default — the model is **forward-compatible**, future server-added fields don't crash the parse.

### 4.2 ViewModel pickup logic

In `MainViewModel.preflightHealth(...)`:

After `response.isSuccessful` and `response.body()` is non-null:

```kotlin
val body = response.body()
val newKey = body?.rotatedKey
val graceUntil = body?.rotationGraceUntil
if (newKey != null
    && newKey.startsWith("doc_")        // sanity prefix
    && newKey.length >= 40              // sanity length (server emits 43+ chars)
    && newKey != apiKey) {              // don't churn if server echoes our own key
    persistRotatedKey(newKey)
    diag(DiagLevel.INFO, "ROTATE",
         "API key auto-rotated; grace expires at $graceUntil")
    _toastEvents.tryEmit("API key auto-updated")
}
```

`persistRotatedKey`:
1. `prefs?.edit()?.putString(PREF_API_KEY, newKey)?.apply()`
2. `_apiKey.value = newKey`
3. `ApiClient.invalidate()` — next call rebuilds the Retrofit service with the new key in the header.
4. `refreshPairing()` — banner clears if previously unpaired.

Rejecting bad payloads:
- `newKey == null` → no-op.
- `newKey == ""` → no-op (no warn; legitimate "no rotation" payload).
- `newKey.startsWith("doc_") == false` OR length < 40 → WARN log, no-op.
- `newKey == currentKey` → no-op (idempotency: the server might re-emit during the grace window if we keep sending old-key requests too, but we shouldn't churn).

### 4.3 One-shot toast signal

`MutableSharedFlow<String>` named `_toastEvents` (replay = 0, extraBufferCapacity = 1, onBufferOverflow = DROP_OLDEST). Pattern matches the existing `_promptDelete` one-shot-style state in the same VM.

### 4.4 UI wiring

`HomeScreen`: collect `_toastEvents` via `LaunchedEffect(Unit)` + `viewModel.toastEvents.collect { ... }`. Display via Android `Toast.makeText(...).show()` (simpler than introducing a SnackbarHost — no scaffold-level SnackbarHostState in this app).

### 4.5 Tests

`android/app/src/test/java/com/droneopssync/app/api/KeyRotationParseTest.kt` — pure JVM unit tests around the Gson parse contract (no Android framework needed):

1. `parses rotated_key + rotation_grace_until from JSON`
2. `tolerates absent rotated_key (no exception)`
3. `tolerates unknown extra fields`
4. `parses a body with all DeviceHealthResponse fields populated`

We do NOT unit-test the ViewModel's prefs/toast wiring — `SharedPreferences` and `viewModelScope` would require Robolectric, which the project does not currently use. The Gson parse is the load-bearing logic; the rest is straightforward integration code that the operator (Bill) verifies on the controller. This matches the project's pragmatism: `android/app/src/test` does not exist today, no Robolectric, no instrumented tests in CI.

### 4.6 Documentation

- `docs/adr/0002-zero-touch-device-key-rotation-client.md` — short Kotlin-side ADR cross-linking to the DroneOpsCommand ADR-0003.
- `CHANGELOG.md` `## [Unreleased]` entry.
- `ROADMAP.md` — tick the v1.3.25 success-criteria checkbox for "PR opened against DroneOpsSync `main`".
- `PROGRESS.md` — 2026-04-24 EVENING entry.

### 4.7 Version bump

Per repo CLAUDE.md: **DO NOT manually edit `android/version.properties`.** The CI workflow `version-bump.yml` increments to `1.3.25` automatically when the PR is squash-merged.

---

## 5. Failure modes and rollback

| Failure | Detection | Mitigation |
|---|---|---|
| Migration runs but old code is still up | Old code never reads new columns; safe additive. | None needed. |
| Redis unreachable at rotation time | `POST /rotate-key` returns 503. | Operator retries when Redis is healthy. No half-state because we never wrote the DB row. |
| Redis evicts the hint mid-grace | Old-key preflight gets no `rotated_key` field; controller keeps trying old key. | Old key still authenticates during grace, so uploads are not blocked. Operator can retrigger `/rotate-key` to re-set the hint. |
| Controller offline for 24h+ during grace | Grace expires; finalizer promotes new key. Old key now 401s. | Operator pastes new key manually, same as today. The mechanism degrades to today's UX — not worse. |
| Pushover delivery fails | Logged WARN. | Operator sees the rotation in the admin UI / DB; alert is informational, not critical. |

Rollback: revert the PR, run `_add_missing_columns` (idempotent — leftover columns don't break anything; we leave them in place), restart. The dual-key auth path is the only behaviour change; reverting `validate_device_api_key` returns to single-key matching. Any device that successfully picked up a new key keeps using it (the new key was promoted to `key_hash`).

---

## 6. Out of scope

- Role-based access on the rotate-key endpoint (today: any authenticated user is admin-equivalent; ADR-0003 §6 flags for RBAC follow-up).
- Bulk rotation (one-device-at-a-time per call; operator can script).
- Keyless rotation via discovery (would need ADR-0020-style managed-tenant work; deferred until first managed DroneOps tenant).
- Audit log table (rely on structured INFO logs + Loki for now).

---

## 7. Operator review checklist

Both PRs must be reviewed (no auto-merge per the routine spec):

- [ ] DroneOpsCommand PR opens, CI green
- [ ] DroneOpsSync PR opens, CI green
- [ ] ADR-0003 reads cleanly, links to ADR-0002
- [ ] Migration is additive only
- [ ] Pushover flow tested or mocked
- [ ] Operator squash-merges DroneOpsCommand first (deploy via `update.sh` on BOS-HQ)
- [ ] Operator squash-merges DroneOpsSync second; CI bumps to v1.3.25 + ships APK
- [ ] First real-world rotation after merge: paste server-rotated key into UI; observe RC Pro Toast on next preflight
