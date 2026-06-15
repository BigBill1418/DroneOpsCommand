# Plan — Device-upload async decoupling (audit P2-2 full leg)

- **Date:** 2026-06-15
- **ADRs:** DroneOpsCommand `docs/adr/0023-device-upload-async-celery-decoupling.md`
  (canonical contract); DroneOpsSync `docs/adr/0008-device-upload-async-poll-client.md`
  (client decisions).
- **Closes:** audit finding **P2-2** (`docs/plans/2026-06-11-ground-up-audit.md`),
  the last open item from FU-8.
- **Sequencing rule:** backend ships first and stays backwards-compatible; the
  DroneOpsSync client fast-follow (Stage C, the `aborted` fix) may ship in
  parallel with Stage A/B because it is backend-independent; the async client
  (Stage D) ships only after Stage A/B are live.

Reference implementation to mirror at every step: the v2.70.0 backup-job leg —
`backend/app/tasks/backup_jobs.py`, `run_backup_job_task`
(`celery_tasks.py:521`), the routes in `backup.py:728-800`, and the hermetic
tests in `backend/tests/test_backup_jobs.py`.

---

## Stage A — Backend: Redis job-state module for device-upload

**DoD:** a `device_upload_jobs.py` module exists mirroring `backup_jobs.py`,
with per-batch state read/write helpers and hermetic tests green. No route
wiring yet.

**Files:**
- **NEW** `backend/app/tasks/device_upload_jobs.py` — copy the shape of
  `backup_jobs.py`:
  - key prefix `droneops:flightupload:job:`; TTL 24 h
    (`_JOB_TTL_SECONDS = 24*60*60`).
  - `STATUS_QUEUED/RUNNING/COMPLETE/FAILED` (reuse vocabulary).
  - `write_batch_state(batch_id, *, status, phase, progress, per_file, error)`
    and `read_batch_state(batch_id)`. `per_file` is the list of
    `{name, state, imported, skipped, error}` dicts. Best-effort Redis
    (`socket_timeout=2`, swallow exceptions), exactly as `backup_jobs`.
- **NEW** `backend/tests/test_device_upload_jobs.py` — unit tests for the
  helpers with Redis faked by an in-process dict (the `fake_write`/`fake_read`
  monkeypatch idiom from `test_backup_jobs.py:78-88`).

**Test strategy:** pure helper tests; no broker, no Redis, no parser. Assert
round-trip of `per_file`, progress clamping (0..100), and `read` returning
`None` on absent/corrupt records.

---

## Stage B — Backend: async route + Celery parse task + status poll

**DoD:** `POST /api/flight-library/device-upload/async` returns 202 +
`{batch_id, files}`; a Celery task parses each file and writes per-file
progress; `GET /api/flight-library/device-upload/status/{batch_id}` returns the
documented envelope; the legacy synchronous route is **unchanged**; hermetic
tests green.

**Files:**
- `backend/app/routers/flight_library.py`:
  - **Keep** `device_upload_flights` (`:1000`) exactly as is; add a one-line
    deprecation note to its docstring pointing at the async pair (mirror
    `backup.py:319-323`).
  - **NEW** `async def device_upload_flights_async(...)` — same auth
    (`validate_device_api_key`), same `_spool_upload` streaming, run the
    **pre-parse SHA-256 dedup short-circuit** inline (mark already-present files
    `skipped` with no job), enqueue a parse job per non-duplicate file, seed the
    batch Redis record (`STATUS_QUEUED`), return `202` + `{batch_id, files}`.
    Reuse `_get_dji_api_key` for parser headers.
  - **NEW** `GET …/device-upload/status/{batch_id}` — two-tier read
    (`read_batch_state` overlay → Celery `AsyncResult` fallback), mirroring
    `get_backup_job` (`backup.py:755-800`).
  - Add `async_upload_available: True` to the `device-health` response dict.
- `backend/app/tasks/celery_tasks.py`:
  - **NEW** `@celery_app.task(name="parse_device_flight", bind=True)
    parse_device_flight_task(self, batch_id, tmp_path, filename, file_hash,
    parser_headers)` — invoke the parser (reuse the same sync/async parse call
    the route uses), dedup per parsed flight (`:1049-1054` logic),
    `_build_flight_from_parsed` for new flights, write per-file result into the
    batch Redis record, update batch rollup status/progress. Record
    `state=error` (do not raise the whole batch) on parser-down / parse error —
    mirror the route's existing per-file `except` (`:1061-1064`).
- **NEW** `backend/tests/test_device_upload_async.py`.

**Test strategy (hermetic, per `test_backup_jobs.py` + `test_backup_nonblocking.py`):**
- Mount only the `flight_library` router on a bare `FastAPI()`; override the
  device-key auth dependency with a fake (as `_build_app` overrides
  `get_current_user`).
- Fake `parse_device_flight_task.delay` to capture args + return a
  `SimpleNamespace(id=…)` (idiom from `test_backup_jobs.py:70-73`).
- Fake `write_batch_state`/`read_batch_state` with an in-process dict.
- Fake the parser HTTP call (monkeypatch `_SpooledUpload.parse` or the httpx
  client) to return deterministic `(200, {flights:[…]})`.
- Assert: async route returns **202** + a `batch_id` + seeded `queued` state;
  a duplicate file short-circuits to `skipped` with no `.delay`; the status
  route returns the Redis overlay when present and the Celery terminal fallback
  when absent (mirror `test_poll_returns_redis_overlay_when_present` +
  `test_poll_falls_back_to_celery_success/failure`).
- Exercise the **task body directly** (`.run(...)`) against the parser fake to
  prove it reuses `_build_flight_from_parsed`, dedups, and writes monotonic
  per-file progress ending `complete` — mirror
  `test_task_create_runs_dump_validate_hash_and_completes`.

**Deploy:** push to `main`; the NOC fleet deployer (`swarmpilot_deployer` on
HSH-HQ) builds + recreates on BOS-HQ. Watch
`https://noc-mastercontrol.barnardhq.com/deploys`. There is no per-repo
autopull (ADR-0018). Backwards-compatible — old clients keep using the legacy
route.

---

## Stage C — DroneOpsSync FAST-FOLLOW: socket-timeout is per-file (backend-independent)

**DoD:** a `SocketTimeoutException` on file *k* fails only file *k* and leaves
*k+1…M* attemptable; JVM unit test proves it; APK released. **Can ship in
parallel with Stage A/B.**

**Files:**
- `android/app/src/main/java/com/droneopssync/app/viewmodel/MainViewModel.kt`:
  - In `performUpload` `catch (SocketTimeoutException)` (`:721-725`): remove
    `aborted = true`. Keep `setStatus(log, UploadStatus.ERROR)`, `totalErrors++`,
    diag. Leave `UnknownHostException` (`:720`) and the 401/403 branch (`:690`)
    untouched.
  - **Refactor for testability:** extract the per-file outcome decision (the
    `when {…}` at `:686-715` plus the exception classification) into a pure
    function, e.g. `fun classifyUploadOutcome(response?, body?, throwable?):
    FileOutcome` returning `{newStatus, abortBatch, importedDelta,
    skippedDelta}`. The loop calls it; the test exercises it without Android.
- **NEW** `android/app/src/test/java/com/droneopssync/app/upload/UploadOutcomeTest.kt`
  — JVM-only (JUnit + plain assertions, the `KeyRotationParseTest` style):
  - socket-timeout → `ERROR`, `abortBatch == false`.
  - `UnknownHostException` → `ERROR`, `abortBatch == true`.
  - HTTP 403 → `ERROR`, `abortBatch == true`.
  - body with `errors` non-empty → `ERROR`, `abortBatch == false`.
  - `skipped > 0` → `DUPLICATE`; else → `SYNCED`.

**Release:** open the PR; **do not bump the version manually** — CI auto-bumps
on merge (suppression trap: a manual bump folds into the squash, HEAD reads
`[skip ci]`, "Bump patch version" never fires, no release). Operators update
the APK via the in-app updater.

---

## Stage D — DroneOpsSync: adopt the async route + retune timeouts

**DoD:** the client uses the async route when the server advertises it, drives
per-file status from the poll, falls back to the legacy route otherwise, and
tightens the upload/poll timeouts; JVM tests green; operator confirms
end-to-end. **Ships only after Stage B is live.**

**Files:**
- `android/app/src/main/java/com/droneopssync/app/api/DroneOpsSyncService.kt`:
  add `uploadFlightsAsync` (→ 202 body type) and `pollUpload(batchId)` (→ the
  status-envelope model). NEW Gson models `AsyncUploadResponse` +
  `UploadStatusResponse` (+ `PerFileStatus`).
- `android/app/src/main/java/com/droneopssync/app/api/ApiClient.kt`: split
  timeouts — upload client `readTimeout(30s)`, a poll client `readTimeout(15s)`,
  `connectTimeout(20s)` unchanged. Do this **only here**, in this release.
- `android/app/src/main/java/com/droneopssync/app/viewmodel/MainViewModel.kt`:
  read `asyncUploadAvailable` from the preflight; if true, POST async, get the
  `batch_id`, poll (2 s → back off to 5 s after 60 s, ceiling) and drive the
  per-file `UploadStatus` from `per_file[0].state`; else fall back to the
  legacy `uploadFlights` path (Stage C behaviour). Reuse the per-file
  `cleanupSafCacheFile` + delete-prompt flow on terminal `SYNCED`/`DUPLICATE`.
- `DeviceHealthResponse`: add `@SerializedName("async_upload_available") val
  asyncUploadAvailable: Boolean? = null` (forward-compat already proven).
- **NEW** `android/app/src/test/java/com/droneopssync/app/upload/PollEnvelopeTest.kt`
  — Gson-contract tests for `UploadStatusResponse` (mirror
  `KeyRotationParseTest`): parses full envelope, tolerates absent/extra fields,
  terminal-state reducer maps `per_file[0].state` → `UploadStatus`.

**Release:** same no-manual-bump discipline. Operator runs a real controller
upload (a multi-file sortie with one deliberately large record) and confirms in
Diagnostics that a slow file no longer blocks the others and the connection
returns immediately with a 202.

---

## Stage E — Documentation + close-out

**DoD:** CHANGELOG (both repos), PROGRESS (both repos), ROADMAP FU-8 note
updated to mark P2-2 in flight → done; audit plan P2-2 annotated as addressed.

**Files:** `droneops/CHANGELOG.md`, `droneops/PROGRESS.md`,
`droneops/ROADMAP.md` (FU-8 follow-up bullet), `DroneOpsSync/CHANGELOG.md`,
`DroneOpsSync/PROGRESS.md`, and a one-line annotation on P2-2 in
`droneops/docs/plans/2026-06-11-ground-up-audit.md`.

---

## Sequence summary

```
Stage A (backend redis module)  ─┐
Stage B (backend route+task+poll)─┴─► ship backend-first, backwards-compatible
Stage C (client aborted fix)     ──► ship in PARALLEL (backend-independent) — FAST-FOLLOW
Stage D (client async adopt)     ──► ship AFTER B is live
Stage E (docs)                   ──► per-stage + final close-out
```
