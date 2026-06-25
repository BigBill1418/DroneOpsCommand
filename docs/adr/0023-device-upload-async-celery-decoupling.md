# ADR-0023 — Decouple device flight-log upload from in-request parsing (Celery + status poll)

- **Status:** **Proposed** — design only, no code shipped. Closes the deferred
  full leg of audit finding **P2-2** (the last open item from the 2026-06-11
  ground-up audit; see ROADMAP FU-8 closure note).
- **Date:** 2026-06-15
- **Authors:** Terry (research/architect). Implementation handoff to aegis
  (backend leg) + fleet-mobile-engineer/aegis (DroneOpsSync client leg).
- **Scope:** DroneOpsCommand FastAPI `flight-library` device-upload endpoint
  **and** the DroneOpsSync native Kotlin companion's upload path. The contract
  spans both repos; this ADR is the canonical record and lives in
  DroneOpsCommand because DroneOpsCommand owns the HTTP endpoint. The
  DroneOpsSync client decision (poll loop + the independent timeout fixes) is
  recorded in DroneOpsSync `docs/adr/0008-device-upload-async-poll-client.md`,
  which cross-references this ADR — mirroring the ADR-0003 ↔ DroneOpsSync
  ADR-0002 cross-repo pairing.
- **Related ADRs:** `0002-droneopssync-upload-auth.md` (device-key auth +
  HTTPS coercion), `0003-zero-touch-device-key-rotation.md` (the prior
  cross-repo pair; rotation hint rides the same `device-health` preflight),
  `0019-flight-library-list-defers-heavy-json-columns.md` (the same OOM class
  this path touches), `0021-startup-recovery-guard-and-hot-indexes.md` +
  `0022-alembic-adoption-and-health-gate-trim.md` (the FU-8/v2.70.0 pass that
  shipped the **backup-job** Celery pattern this ADR mirrors exactly).
- **Reference implementation (mirror this):** the v2.70.0 async backup work —
  `backend/app/tasks/backup_jobs.py` (Redis job-state helpers, key prefix
  `droneops:backup:job:`, 24 h TTL), `run_backup_job_task` in
  `backend/app/tasks/celery_tasks.py:521`, the `POST /api/backup/jobs` +
  `GET /api/backup/jobs/{job_id}` routes in `backend/app/routers/backup.py:728-800`,
  and the hermetic test pattern in `backend/tests/test_backup_jobs.py`.
- **Plan:** `docs/plans/2026-06-15-device-upload-async-decoupling.md`.

---

## 1. Context

### 1.1 The brittleness (verified 2026-06-15)

`POST /api/flight-library/device-upload` —
`async def device_upload_flights` at
`backend/app/routers/flight_library.py:1000` — streams each uploaded file to a
disk-backed temp file (`_spool_upload`, `:243`, OOM-safe per audit #9 / ADR-0019)
and then **parses synchronously inside the request**. For each file in the
multipart batch it calls `spooled.parse(parser_headers)` (`_SpooledUpload.parse`,
`:214`), which POSTs the file to the flight-parser sidecar with
`httpx.AsyncClient(timeout=120)` (`:219`) and **awaits the full parse** before
moving on. The HTTP response (`FlightUploadResponse {imported, skipped, errors,
flights}`) is only returned after every file in the batch is parsed.

The companion is native Kotlin (DroneOpsSync,
`android/app/src/main/java/com/droneopssync/app/`). Its OkHttp client sets
`connectTimeout(20s)` / `readTimeout(120s)` / `writeTimeout(120s)`
(`api/ApiClient.kt:113-115`). `MainViewModel.performUpload()`
(`viewmodel/MainViewModel.kt:610-766`) uploads **one file per request** in a
`for (log in pending)` loop (`:660`), after a preflight `deviceHealth` gate
(`:634`).

Two concrete defects fall out of the held-connection design:

- **B1 — the "hang."** The connection is held open for the entire parse. Field
  cellular/Wi-Fi reaps long-held idle-feeling connections; a parse exceeding
  ~120 s on either side trips a timeout and aborts a request whose work the
  server may still be doing.
- **B2 — one slow file kills the rest of the batch.**
  `MainViewModel.performUpload` catches `SocketTimeoutException` at `:721-725`
  and sets `aborted = true`. The loop guard at `:661-665` then marks **every
  remaining file** `UploadStatus.ERROR` *without attempting them*. One slow
  file on the controller fails files N+1…M. (`UnknownHostException` at `:716`
  and HTTP 401/403 at `:690` also set `aborted` — those are **correct**: a dead
  host or a bad key applies to the whole batch. Only the `SocketTimeout` →
  `aborted` coupling is wrong.)

### 1.2 What is NOT broken

- **No data loss today.** Dedup is server-side by SHA-256 (`:1034`,
  `Flight.source_file_hash`), so any retry — same file, same batch, next
  session — is idempotent. The original bytes are persisted to the hash-named
  store by `_spool_upload` (`:259`) before parse. The cost of the brittleness
  is **wasted bandwidth + a confusing half-failed-sync UX**, not lost flights.
- **The OOM class is already closed** on this path (streaming spool, ADR-0019).
  This ADR is purely about the **latency-coupling / timeout-chain** half of
  P2-2, not memory.

### 1.3 The deployment constraint that shapes everything

DroneOpsSync ships as a **native Android APK** via GitHub release + the in-app
updater (`GitHubClient.kt`, `UpdateState.kt`). There is **no OTA/EAS channel**.
A client change therefore requires a real release and each field operator
updating the APK on their controller — an unbounded lag window. The backend
**must keep serving old synchronous clients indefinitely** until every field
device is updated. Backwards compatibility is not a nicety here; it is the
gating requirement.

Release gotcha (fleet memory, restated for the rollout section): **never
manually bump the version in a DroneOpsSync PR.** GitHub folds the manual bump
into the squash, the HEAD commit reads `[skip ci]`, and the "Bump patch
version" workflow is suppressed — the release never fires. CI auto-bumps on
merge. Let it.

---

## 2. Decision

### 2.1 New asynchronous protocol (additive)

Introduce an async ingest handshake that mirrors the proven v2.70.0 backup-job
pattern field-for-field:

```
POST /api/flight-library/device-upload      (UNCHANGED for old clients)
POST /api/flight-library/device-upload/async   ← new, 202 + {batch_id}
GET  /api/flight-library/device-upload/status/{batch_id}   ← new, poll
```

**Submit (new async route).** Client POSTs file(s) exactly as today (multipart,
`X-Device-Api-Key`). The handler:
1. Streams each file to disk via the **existing** `_spool_upload` (no change —
   same OOM-safe path, same original-bytes persistence, same SHA-256).
2. Runs the cheap **pre-parse dedup short-circuit** synchronously: if
   `source_file_hash` already exists, mark that file `skipped` immediately
   (no parse, no job needed for it) — preserves today's behaviour and keeps
   the common "already synced" case instant.
3. Enqueues a Celery parse job per submitted (non-duplicate) file.
4. Seeds a **batch record** in Redis (status `queued`) keyed by a server-minted
   `batch_id` (a UUID, exactly like the backup `job_id`).
5. Returns **`202 Accepted`** + `{batch_id, files: [{name, state}]}` immediately.

**Poll (new status route).** Client polls
`GET /api/flight-library/device-upload/status/{batch_id}` →

```jsonc
{
  "batch_id": "…",
  "status": "queued|running|complete|failed",   // batch-level rollup
  "phase":  "queued|parsing|done",
  "progress": 0..100,                            // files-done / files-total
  "per_file": [
    {"name": "DJIFlightRecord_….txt",
     "state": "pending|parsing|imported|skipped|error",
     "imported": 0, "skipped": 0, "error": null}
  ]
}
```

The Redis record is the rich progress overlay; the Celery `AsyncResult` set is
the terminal fallback (same two-tier read as `get_backup_job`,
`backup.py:755-800`). Job state self-expires (TTL, §2.4).

### 2.2 batch_id granularity: ONE batch_id per submit, keep one-file-per-request (recommended)

The client today uploads **one file per request**. The lowest-friction,
lowest-risk path is:

- **Keep one-file-per-request.** Each POST to `…/device-upload/async` returns a
  `202 + {batch_id}` for that single file, and the client polls that
  `batch_id`. The `per_file` array has one entry. The client's existing
  per-file status model (`UploadStatus` per `LogFile`) maps 1:1 — minimal UI
  churn.
- The contract **also supports a multi-file submit** (the `per_file` array is
  already plural and the parser is invoked per file), so a future client can
  batch without a backend change. We do **not** ask the first client release to
  adopt multi-file submit.

**Why not force multi-file batching now?** The field-UX + reliability tradeoff
favours per-file:
- **Reliability:** one connection per file means a dropped connection costs one
  file's re-submit, not a whole batch. With dedup, re-submit is free. A
  multi-file submit re-introduces the very "one event affects many files"
  coupling B2 is removing — just moved from the parse phase to the upload
  phase.
- **Field UX:** per-file 202s give the operator immediate, granular
  "uploading / parsing / done" feedback per row, which is what the current
  screen already renders. A single batch_id over many files would stall the
  whole list on the slowest parse.
- **Cost of the choice:** marginally more HTTP round-trips (one preflight is
  already shared; the upload POSTs were always per-file). Negligible on the
  small file counts this workflow sees (a sortie is typically 1–10 records).

### 2.3 Backwards compatibility: a SEPARATE async route (recommended) — not a capability header

**Decision: add a new route `POST …/device-upload/async`; leave
`POST …/device-upload` untouched and synchronous forever.**

Considered alternatives:
- **(A) Same route + `X-DOS-Async: 1` capability header.** The handler branches
  on the header: present → enqueue + 202; absent → today's synchronous parse.
  *Rejected* — the response *shape and status code* differ (200 +
  `FlightUploadResponse` vs 202 + `{batch_id}`). Content/behaviour negotiation
  on a header makes one URL return two contracts; harder to test, document, and
  reason about; a proxy or retry that drops the header silently falls back to
  the slow path. It also complicates the OpenAPI/`response_model`.
- **(B) Content negotiation (`Accept`).** Same objection as (A), weaker signal.
- **(C) Separate route (chosen).** Two routes, two clean contracts, two
  `response_model`s. The old route is *self-documenting* as the legacy path.
  Old clients hit the old URL (the URL is hard-coded in `DroneOpsSyncService`
  `:21`); new clients hit the new URL. No branching inside one handler. The
  shared ingest internals (`_spool_upload`, dedup, `_build_flight_from_parsed`)
  are reused by both, so there is no logic duplication — only the
  request/response envelope differs.

This matches how the backup leg did it: `POST /api/backup/jobs` is a **new**
route; `restore-from-upload` was **kept working unchanged** and marked
deprecated in its docstring (`backup.py:319-323`). We do the same: the old
`device-upload` docstring gains a deprecation note pointing at the async pair,
but its behaviour never changes while any field device runs an old APK.

The preflight `device-health` response (`flight_library.py` device-health
handler) gains an additive boolean `async_upload_available: true` so a new
client can *confirm* the server supports the async route before using it
(Gson/forward-compat already tolerates unknown fields, per ADR-0002 §2.1 and
`KeyRotationParseTest`). This is a hint, not a gate — the route's existence is
the real contract.

### 2.4 Poll cadence, TTL, terminal states, client-death

- **Poll cadence:** client polls every **2 s** while a batch is non-terminal,
  with a soft cap (e.g. back off to 5 s after 60 s, give up the *foreground*
  poll after a generous ceiling — the job still completes server-side). 2 s
  matches a human-perceptible "it's working" cadence without hammering the API.
- **batch_id TTL:** **24 h**, identical to the backup job TTL
  (`backup_jobs._JOB_TTL_SECONDS`). Comfortably outlives any parse; bounds the
  Redis keyspace; a crashed worker never leaks state.
- **Terminal states:** `complete` (all files reached `imported`/`skipped`) and
  `failed` (the job raised before writing a per-file result — distinct from a
  file whose `state=error` inside a *complete* batch; a batch can complete with
  some files in `error`).
- **Error surfacing:** per-file `error` strings carry the same text the
  synchronous path produces today (`"<name>: parser returned <code>"`,
  `"<name>: flight-parser service unavailable"`), so the client's existing
  `body.errors` rendering (`MainViewModel:680`) is reused verbatim.
- **Client dies mid-poll:** **nothing is lost.** The Celery job runs to
  completion server-side regardless of whether anyone is polling; results land
  in the DB; the Redis overlay persists for 24 h. On next launch the client
  re-scans, finds the (now-parsed) files already on the server, and the
  **SHA-256 dedup** returns `skipped` instantly — the controller marks them
  `DUPLICATE` and offers the delete prompt. Re-submitting a file whose job is
  still in flight is also safe: the pre-parse dedup short-circuit (§2.1 step 2)
  or the per-flight dedup inside the task (`:1051`) absorbs it.

### 2.5 Secondary client fixes — independent, ship FIRST

These are correctness fixes to the **existing synchronous** client and do not
depend on the async backend. They should ship as a **standalone fast-follow
APK release before** the async leg (see §2.6 rationale):

- **(a) `SocketTimeoutException` must NOT set `aborted = true`.** At
  `MainViewModel.kt:721-725`, drop the `aborted = true` line. A socket timeout
  is a *per-file* condition (this file's parse was slow), not a *whole-batch*
  condition. Mark the current file `ERROR`, increment `totalErrors`, and
  **continue the loop**. Leave the `aborted = true` on `UnknownHostException`
  (`:720`) and on HTTP 401/403 (`:690`) — those are genuinely batch-wide. This
  alone converts "one slow file loses the rest of the sortie" into "one slow
  file fails alone; the operator long-presses to retry it."
- **(b) Re-evaluate the 120 s `readTimeout`.** Once the held-connection parse is
  gone (async leg), the upload POST returns in the time to *stream bytes to
  disk* — seconds, not minutes. For the **async client**, drop `readTimeout` to
  ~30 s (upload) and use a separate short timeout for the poll GET (~15 s). For
  the **interim synchronous fast-follow (a)**, *keep* 120 s — the synchronous
  parse still happens in-request until the async leg ships; lowering it first
  would *increase* B1 timeouts. Tighten the timeout **in the same release that
  adopts the async route**, never before.
- **(c) Preflight/poll interaction.** The existing `deviceHealth` preflight
  (`:634`) stays exactly as is — it still gates the batch on reachability + key
  validity before any upload, and still carries the ADR-0003 rotation hint. The
  new poll loop is *additional* and only runs after a successful 202. The
  preflight is **not** replaced by the poll; they serve different phases
  (pre-upload gate vs post-upload progress).

### 2.6 Recommendation on sequencing the secondary fixes

**Yes — ship the secondary client fixes (§2.5a, and the §2.5c no-op
confirmation) as a standalone fast-follow APK before the full async leg.**
Rationale:
- Fix (a) is a **one-line deletion** with an outsized reliability payoff on the
  operator's primary field workflow, and it is **independent** of any backend
  change — it works against today's synchronous endpoint.
- It de-risks the async rollout: field devices get the "don't nuke the rest of
  the batch" behaviour immediately, while the larger async contract is built,
  reviewed, and deployed backend-first.
- It is testable in isolation with the existing JVM unit-test harness (§4 of the
  plan), no backend dependency.
- Fix (b) is explicitly **deferred to the async release** (lowering the timeout
  before the parse moves off-request would backfire), so the fast-follow is
  purely (a) + (c)-confirmation. Clean, small, safe.

---

## 3. Consequences

### 3.1 Positive

- The upload connection is held only for the byte-streaming, not the parse —
  B1 ("the hang") is structurally eliminated for async clients.
- A slow/failed parse on one file never affects another file — B2 eliminated on
  both the new (per-file jobs) and the interim (fix §2.5a) paths.
- Reuses the **entire** backup-job mechanism (Redis helpers, Celery wiring,
  two-tier poll, TTL, hermetic test pattern) — minimal new surface, proven
  failure modes.
- No data-loss risk introduced; dedup already makes every path idempotent.
- Old APKs keep working forever (separate route).

### 3.2 Negative / cost

- A second async surface to maintain (route pair + a `device_upload_jobs.py`
  Redis module). Mitigated by mirroring `backup_jobs.py` so the shapes can't
  drift.
- The client gains a poll loop + state machine (more client code than a single
  blocking call). Mitigated by the per-file-batch_id choice mapping 1:1 onto
  the existing `UploadStatus`.
- Two upload code paths on the backend until the legacy route can be retired
  (which cannot happen until telemetry shows zero old-APK traffic — likely
  never formally retired; just left dormant, as `restore-from-upload` is).

---

## 4. Risks and mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **APK-rollout lag** — old clients keep hitting the synchronous route for an unbounded window | Certain | Medium (B1/B2 persist on old devices) | Separate-route backwards-compat keeps them working; ship fix §2.5a first so even un-upgraded behaviour is *less* destructive; `async_upload_available` hint lets new clients self-detect |
| **Parser-sidecar failure mid-job** (`httpx.ConnectError`) | Medium | Low | Task records that file `state=error` with the same `"flight-parser service unavailable"` text; batch still completes; dedup makes re-submit free. Unlike the old sync `/reprocess` path, do **not** `break` — record per-file and continue (the device-upload loop already continues today, `:1061`) |
| **Redis eviction / wedge** drops the job-state overlay before the client polls | Low | Low | Two-tier read: Celery `AsyncResult` is the terminal fallback (mirrors `get_backup_job:786-800`); and even with both gone, dedup means a re-scan finds the flights already imported. 24 h TTL + `socket_timeout=2` bound the blast radius (mirrors `backup_jobs._redis_client`) |
| **Failover / blue-green** during a job | Low | Low | Celery worker + Redis are already in the stack (backup jobs proved this on CHAD-HQ primary / BOS-HQ standby). Job state in Redis survives an API-container recreate; an in-flight task on a killed worker is retried per Celery acks-late posture or simply re-submitted by the client (dedup-safe). No new infra. |
| **Partial-batch semantics confuse the operator** (batch `complete` with some `error` files) | Medium | Low | `per_file[].state` is authoritative; the batch-level `status=complete` means "all jobs terminal," not "all succeeded." Client renders per-row state exactly as today; `error` rows keep the existing long-press-retry affordance |
| **Client polls forever** if a job never reaches terminal | Low | Low | Foreground poll has a soft ceiling then stops *displaying* progress; the job + Redis TTL clean up server-side; next launch reconciles via dedup |
| **Lowering `readTimeout` too early** re-amplifies B1 | Medium (process risk) | Medium | §2.5b explicitly pins the timeout drop to the *async-adopting* release; the fast-follow keeps 120 s |

---

## 5. Validation (definition of done for the decision)

The ADR is satisfied when the staged plan
(`docs/plans/2026-06-15-device-upload-async-decoupling.md`) ships its stages in
order: backend async route + status poll (backwards-compatible, hermetic
tests green) → DroneOpsSync fast-follow fix §2.5a (JVM tests green) →
DroneOpsSync async-client adoption + timeout tightening (JVM tests green,
operator confirms end-to-end on a controller). No stage may regress the
synchronous route while any field device runs an old APK.

---

## 6. Amendment (2026-06-24) — cross-container temp-file handoff regression (v2.72.1)

**Status of the decision:** shipped (the async route + Celery task are live).
This amendment records a **production defect in the shipped implementation** and
its fix. The architecture stands; the bug was an implementation detail of the
file handoff between the API and the worker.

### Symptom

The first real-world async device upload (a **DJI Mavic 4 Pro** flight log,
field-reported **2026-06-24 21:43 PDT**) failed. The DroneOpsSync diagnostic
showed the full client path succeeding — SAF scan OK, `POST .../device-upload/async`
→ **202**, status poll → `complete / done / 100` — then a per-file error:

```
async per-file error: FlightRecord_..._.txt: [Errno 2] No such file or directory: '/tmp/flight_upload_bj_i2gq7'
```

### Root cause

The async route spools the upload to a **local `/tmp` file**
(`_spool_upload` → `tempfile.mkstemp(prefix="flight_upload_")`) and passed that
**path string** to `parse_device_flight_task.delay(...)`. The Celery **`worker`
runs in a different container** than the **`backend`** (both `build: ./backend`,
but distinct services in `docker-compose.yml`). `/tmp` is each container's own
ephemeral layer — **not** a shared volume — so the worker's `open(tmp_path)`
raised `FileNotFoundError`. The task caught it, recorded the file `state=error`,
and **completed the batch** (the §2.4 "complete-with-error" split), which is why
the client saw `complete/100` *and* a red row.

This is **not** Mavic-4-Pro-specific — it was the first async upload to cross the
API→worker process/container boundary at all. Any aircraft would have hit it.
The DJI v13+ AES decryption dependency (`X-DJI-Api-Key` from the `dji_api_key`
SystemSetting) is real but downstream: **the file never reached the parser.**

A secondary latent defect: because the worker's `os.unlink(tmp_path)` ran in the
wrong container, the backend's `/tmp` spool **leaked forever** on the API
container (a slow disk-fill).

### Why the tests didn't catch it

`tests/test_device_upload_async.py::_run_task` created a **real local temp file
in the same process** and drove `parse_device_flight_task.run(...)` against it —
the cross-container filesystem boundary was never exercised, so the suite was
green while production was broken.

### Fix (v2.72.1)

The original bytes are **already** persisted at spool time to the hash-named
store `/data/uploads/flight_logs/{hash}{ext}`, which lives on the **shared
`app_data:/data` volume** both containers mount. So:

1. **Worker** (`parse_device_flight_task`): resolve the file via
   `flight_library._get_stored_file_path(file_hash)` on the shared store; fall
   back to `tmp_path` only when it actually exists (same-container/dev, or a
   legacy in-flight message); if **neither** exists, record a clear, diagnosable
   per-file error (`"upload artifact not found on shared store …"`) and still
   complete the batch — never a bare ENOENT. The canonical stored original is
   **never** unlinked.
2. **Route** (`device_upload_flights_async`): `spooled.close()` immediately
   after enqueue (not just on the dedup short-circuit), releasing the redundant
   `/tmp` spool on the backend instead of leaking it. `tmp_path` is still passed
   for signature stability + the same-container fallback.
3. **Regression tests:** `test_task_reads_shared_store_when_tmp_spool_absent`
   (proves the worker reads the shared store when `/tmp` is absent — the exact
   cross-container reality) and `test_task_errors_clearly_when_artifact_missing_everywhere`
   (clear error, batch still completes). The `_run_task` harness gained
   `tmp_exists` / `use_shared_store` knobs that model the real file topology.

### Failover & Resilience Guard

Reading from the persistent `app_data` volume instead of ephemeral `/tmp` is
**strictly more** resilient to container recreation. No change to PostgreSQL
replication, port bindings, the blue-green swap, or the failover engine.

### Follow-up — RESOLVED in v2.72.2

`_store_original_from_path` is fail-soft (logs + swallows on error). With the
worker now depending on that store for the async path, the original ADR-0023
implementation would return `202` (file `pending`) even when the store write
failed — deferring an unserviceable ENOENT to the worker.

**Hardened in v2.72.2:** the async route now verifies the post-condition
explicitly — `_get_stored_file_path(file_hash)` must resolve a file on the
shared store *before* the file is enqueued. If it does not, the route records
that file `state=error` in the `202` body and **skips the enqueue** (no job for
a file the worker can never read), with an operator-actionable log
(`original not persisted to shared store … check the app_data volume / disk`).
This is the in-request, fail-fast counterpart to the worker's defensive
"artifact not found" guard. `_spool_upload`'s fail-soft contract is unchanged
(the legacy synchronous route still parses in-process and is unaffected).
Regression test: `test_async_upload_store_write_failure_errors_and_skips_enqueue`.
