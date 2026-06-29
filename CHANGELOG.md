> **Maintained automatically by NOC doc-autogen.** This file is refreshed twice daily (04:00 + 16:00 UTC) by `~/noc-master/scripts/doc-autogen.py`, which summarizes recent commits via Claude Haiku 4.5 and commits with a `[skip-deploy]` trailer so no container rebuilds are triggered. See [NOC-Master ADR-0013](https://github.com/BigBill1418/NOC-Master-Control-SWARM/blob/main/docs/decisions/ADR-0013-docs-only-deploy-skip.md). Manual edits are preserved — the generator diffs against existing content before writing.

# Changelog

Notable changes to DroneOpsCommand. Dates are absolute (YYYY-MM-DD, UTC).

## 2026-06-29 — fix(flights): DJI duration from authoritative header airtime + unique, start-ordered names — v2.75.0

The "Savannah Bananas Games" report showed an **impossible single-airframe
overlap** (one Mavic airborne on two overlapping intervals). DB evidence
**inverted** the "start_time is wrong" premise — `start_time` is correct (matches
the DJI filename token + 7 h PDT→UTC). Two other bugs were the cause. See
**ADR-0027**.

* **Duration (root cause #1):** the Rust parser (`flight-parser/src/dji.rs`)
  discarded DJI's authoritative airtime (`details.total_time`,
  `raw_metadata->>'header_duration'`) and estimated `frames.len() / 10.0` — a
  hard-coded 10 Hz divisor. Mavic 4 Pro logs at **15 Hz** → durations inflated
  exactly **×1.5**; DJI FPV ~5 Hz → **halved**. The parser now prefers the header
  whenever present, falling back only when absent to a model-agnostic estimate
  derived from **actual frame timestamps** (`osd.fly_time` / `custom.date_time`),
  never a constant. Extracted as a unit-tested pure `choose_duration()`.
* **Data re-stamp (migration 0004, idempotent):** `flights.duration_secs` ←
  header for **59** `dji_txt` flights diverging > 1 % (36 Mavic 4 Pro, 13 Mavic 3
  Pro, 4 Matrice 4TD, 2 Matrice 30T, 2 DJI FPV, 2 Avata 2). Also re-stamps **26**
  `mission_flights.flight_data_cache` durations — the report sums that snapshot,
  not live `duration_secs`. `start_time` and distance are untouched.
* **Names (root cause #2):** `_generate_flight_name` counted by `created_at`
  inside a `start_time`-day window, fleet-wide — collapsing to `_0001`
  collisions and descending junk sequences. Now the sequence is the **start-time
  rank within the (label, operator-local day) group** with a conflict-bump;
  migration 0004 recomputes the **69** existing auto names that need it and adds
  a partial UNIQUE index `uq_flights_autoname`. Operator-typed names
  (`Batt Maint`, `Maintenance Check Flight`) are left alone.
* **Ingest guard (new):** every import now flags (never rejects) implausible
  `point_count/duration` cadence and single-airframe time overlaps, paging ntfy
  `high` on `droneops-flight-overlap`. Would have caught the inflated durations
  at upload.
* **Savannah result:** airtime corrects **11.59 h → 8.54 h** (Mavic 9.15→6.10,
  Matrice 2.44 unchanged); distance 62.29 mi, start_time, flight count 27 all
  unchanged; report left unsent for review.

## 2026-06-29 — fix(reports): de-duplicate flights + accurate, unit-correct metrics — v2.74.0

The AI mission report for **"Savannah Bananas Games"** showed badly inflated
numbers (50 flights / 1,660 min / 160.33 mi against a true 27 / 695 / 62.29).
Root cause: a **retry storm** during the 2026-06 OOM window inserted the same
flights many times — 50 `mission_flights` rows for 27 unique flights (one flight
×13) — because the single-attach path had **zero dedup** and there was **no DB
constraint**. The report engine summed over every row, so totals inflated
100-160%. Area was ≈correct (geometry union is insensitive to duplicates). See
**ADR-0026** for the full decision record.

* **Data repair (one-time, reversible):** the 23 duplicate savannah rows were
  backed up and deleted, keeping the earliest `added_at` per flight → 27 rows.
  A fleet-wide scan found **no other affected mission** (blast radius = 1).
* **Structural guard (RC-1):** migration **0003** adds partial UNIQUE indexes
  `(mission_id, flight_id) WHERE flight_id IS NOT NULL` and
  `(mission_id, opendronelog_flight_id) WHERE opendronelog_flight_id IS NOT
  NULL`. The migration dedups first (idempotent) then constrains. Applied live.
* **Idempotent single-add:** `POST /api/missions/{id}/flights` now returns the
  existing row if the flight is already attached (re-clicking is a safe no-op),
  with an `IntegrityError` backstop for the concurrent-attach race.
* **Defensive report dedup (RC-1):** `reports.py` aggregates iterate over
  flights uniqued by identity key, so a stray duplicate can never re-inflate a
  report's count / time / distance.
* **Area on full-resolution geometry (RC-2):** coverage acreage is now measured
  on the full-res track (shape-preserving Douglas-Peucker only), streamed one
  flight at a time to preserve the ADR-0025 OOM fix — not the 2000-vertex
  strided render decimation.
* **Altitude units fixed (credibility):** the report printed raw cache values
  with a bare "ft" label, but DJI logs altitude in **metres** (flight-parser
  `dji.rs`). "190.8 ft" was actually **190.8 m AGL (626 ft)**. Altitude is now
  formatted in code with the correct unit (metres + explicit feet), and the LLM
  is told the source unit and instructed never to convert or relabel.
* **Ghost / aborted-launch filter:** flights under 30 s AND 10 m (e.g. two
  ~7 s, ~0 m aborted launches in savannah) are excluded from altitude ranges so
  they no longer drag the minimum to 0.

## 2026-06-29 — feat(missions): bulletproof large-mission flight handling + bulk attach — v2.73.0

Building a mission with far more flights than ever before ("savannah") errored
out on open, and adding flights one click at a time was painfully slow. Both
were the **same OOM class** as ADR-0019/0020: every full-mission read
serialized the entire GPS track for every attached flight — O(N_flights × ~19k
points) — which blew past the 1536 MiB backend cgroup cap and OOM-killed
uvicorn mid-response (502/520). See **ADR-0025** for the full decision record.

* **Root data-model fix (A2):** attaching a native flight now stores **scalar
  display fields only** in `flight_data_cache` — never the GPS track. The track
  lives once on `Flight.gps_track` and is loaded on demand. The legacy-ODL
  attach path (rows with no `Flight`) is unchanged.
* **Detail read path (A1):** `GET /api/missions/{id}` (and every POST/PUT/PATCH
  re-query) strips `track`/`gps_data`/`coordinates`/`telemetry` from each
  flight cache **before serialization** — outbound only, the stored rows are
  never mutated (no write-on-read, no legacy data loss). The detail payload is
  now O(rows).
* **Report + map paths (A3):** a new bounded loader
  (`services/mission_tracks.load_bounded_flight_tracks`) pulls each flight's
  track **one at a time** and decimates it to the render vertex cap, so neither
  `POST /report/generate` nor the `/map*` endpoints ever hold all raw tracks at
  once. `maps.py` no longer eager-loads every linked `Flight` in full.
* **Fast multi-add (B):** new `POST /api/missions/{id}/flights/bulk` attaches
  many flights in **one transaction**, idempotently (skips already-attached by
  `flight_id`/`opendronelog_flight_id`), deriving aircraft server-side and
  storing scalar caches. The editor now has **checkboxes + "Select all" +
  "Add selected (N)"**; the per-row ADD routes through the same bulk endpoint.
* **Picker scale (C):** the editor requests `/flight-library?limit=2000` so
  >500 library flights are reachable (was silently capped at the 500 default).
* **Frontend resilience (D):** `loadMission`/add failures now surface the real
  HTTP status in the notification instead of a generic message.
* **Tests:** 7 new backend tests (detail-strip O(rows), no-track-at-attach,
  bulk one-txn/idempotent/scalar/ODL-preserved, bounded track loader) +
  updated/added frontend bulk + multi-select tests. Full backend suite green
  (441 passed, 3 skipped); frontend green; `tsc` clean.
* **Resilience guard:** no schema change, no port/replication/blue-green
  impact — app-layer read/write behaviour only; failover-safe.

## 2026-06-24 — fix(device-upload): async route fails fast if original not on shared store before 202 — v2.72.2

Hardening follow-up to v2.72.1 (ADR-0023 §6). `_store_original_from_path` is
fail-soft, so the async route could return `202` (file `pending`) even when the
original never landed in the shared hash store — deferring an unserviceable
ENOENT to the worker.

* **Fix:** the route now verifies `_get_stored_file_path(file_hash)` resolves a
  file on the shared store **before** enqueueing. If not, the file is reported
  `state=error` in the `202` body and **no job is enqueued**, with an
  operator-actionable log (`check the app_data volume / disk`). The in-request
  fail-fast counterpart to the worker's defensive guard.
* **Scope:** `_spool_upload`'s fail-soft contract is unchanged — the legacy
  synchronous route parses in-process and is unaffected.
* **Tests:** `test_async_upload_store_write_failure_errors_and_skips_enqueue`.
  Full backend suite green (434 passed, 3 skipped).
* No DB/replication/blue-green/failover impact.

## 2026-06-24 — fix(device-upload): async parse worker reads shared hash store, not cross-container /tmp — v2.72.1

Bugfix: the async device-upload path (ADR-0023) failed for **every** real
upload because the API container spooled the file to its private `/tmp` and
handed the **path** to the Celery `worker`, which runs in a **separate
container** that cannot see that `/tmp`. Field-reported on a **DJI Mavic 4
Pro** flight log (2026-06-24): client got `202` + poll `complete/100`, then
`[Errno 2] No such file or directory: '/tmp/flight_upload_*'`.

* **Root cause:** `/tmp` is per-container ephemeral, not a shared volume; the
  worker's `open(tmp_path)` raised `FileNotFoundError`. Not Mavic-4-Pro-specific
  — it was the first async upload to cross the API→worker container boundary.
  (See ADR-0023 §6 amendment.)
* **Fix:** the worker now resolves the file from the **shared hash store**
  (`/data/uploads/flight_logs/{hash}`, on the `app_data:/data` volume both
  containers mount) via `_get_stored_file_path(file_hash)` — where the original
  bytes were already persisted at spool time — falling back to `tmp_path` only
  when present, and emitting a clear diagnosable error if neither exists (never
  a bare ENOENT). The route now closes the redundant `/tmp` spool immediately
  (it was also **leaking** on the backend, since the worker's `unlink` ran in
  the wrong container). The canonical stored original is never deleted.
* **Tests:** two regression tests reproduce the cross-container topology the
  old hermetic harness hid (`test_task_reads_shared_store_when_tmp_spool_absent`,
  `test_task_errors_clearly_when_artifact_missing_everywhere`). Full backend
  suite green (433 passed, 3 skipped).
* **Resilience:** reads from the persistent `app_data` volume instead of
  ephemeral `/tmp` — strictly more recreation-safe. No DB/replication/blue-green/
  failover impact.
* **Client:** DroneOpsSync needs **no change** — it behaved correctly end to end.
* **Next (non-blocking):** consider hard-failing the async spool if the
  shared-store write fails, so a 202 is never returned for an unreadable file.

## 2026-06-20 — feat(financials): customer contact on summary missions[] for marketing review engine — v2.72.0

Additive: `GET /api/financials/summary` mission detail rows now carry
`customer_email` and `customer_phone` alongside the existing `customer_name`
(ADR-0024, extends ADR-0016).

* **Why:** the marketing service-revenue bridge
  (`marketing/api/droneops-financials.js`) consumes `summary.missions[]` and
  needs a way to reach the customer to send a **post-job Google review
  request**. The rows carried `customer_name` but no contact handle.
* **Source:** pulled straight off the already eager-loaded `Mission.customer`
  relationship (`Customer.email` / `Customer.phone`). No new data source, no
  extra query, no migration, no loader change — the ADR-0019/P0-1 OOM-safe
  loader contract is untouched.
* **PII posture (ADR-0024):** minimal (only the two contact fields), not
  logged at the endpoint, opt-out honored downstream by the marketing review
  engine. Same JWT-gated trust boundary the bridge already uses.
* **Gotcha:** a billable mission with no attached customer record — or a
  customer with no email/phone on file — emits `null` for the field (never
  raises). Marketing must skip null-`customer_email` rows.
* Backwards-compatible: unknown-key-tolerant consumers (Financials dashboard,
  revenue bridge) are unaffected.

## 2026-06-15 — feat(flight-ingest): async device-upload (202 + Celery parse + poll) — v2.71.0

Backend leg of the device-upload async decoupling (audit P2-2, the last open
FU-8 item; ADR-0023, plan `docs/plans/2026-06-15-device-upload-async-decoupling.md`).
**Backwards-compatible and additive — the legacy synchronous route is
byte-for-byte unchanged**, so every existing DroneOpsSync APK keeps working.

* **The problem it removes:** the legacy `POST /api/flight-library/device-upload`
  parses each DJI flight log *synchronously inside the HTTP request* (holds the
  connection open while POSTing to the flight-parser with a 120s timeout). On
  field cellular/wifi a long parse trips the client's matching 120s read-timeout,
  and the old client then aborts the rest of the sortie's batch. The connection
  was doing the waiting.
* **New async pair (ADR-0023):**
  * `POST /api/flight-library/device-upload/async` — streams each file to disk
    via the existing OOM-safe `_spool_upload`, runs the SHA-256 dedup
    short-circuit inline (already-present files return `skipped` with no job),
    enqueues a Celery `parse_device_flight_task` per new file, seeds a Redis
    batch record, and returns **`202 + {batch_id, files:[{name,state}]}`
    immediately** — the connection is held only for the byte-stream.
  * `GET /api/flight-library/device-upload/status/{batch_id}` — two-tier read
    (Redis overlay → Celery `AsyncResult` fallback) returning
    `{batch_id, status, phase, progress, per_file:[{name,state,imported,skipped,error}]}`.
  * `device-health` gains an additive `async_upload_available: true` so new
    clients self-detect and old clients are unaffected.
* **Pattern reuse:** `parse_device_flight_task` runs the route's existing async
  parse/dedup/`_build_flight_from_parsed` helpers verbatim inside a fresh
  `task_event_loop()` (NullPool engine) — same sync-Celery-context idiom as
  `send_payment_reminders_task`; no parse-logic re-implementation, no code-path
  drift. A parser-down/parse error records `state=error` for that file and never
  fails the batch.
* New `backend/app/tasks/device_upload_jobs.py` (Redis batch-state, 24h TTL,
  mirrors `backup_jobs.py`). Tests: `test_device_upload_jobs.py` (10) +
  `test_device_upload_async.py` (10). Suite **429 passed, 0 failed**.
* Failover: runtime-only; Celery+Redis already in the stack (proved on the
  backup-job leg across CHAD/BOS); legacy route + `FlightUploadResponse`
  unchanged, so standby-first deploys serving old APKs are unaffected.
* **Client leg ships separately** as a DroneOpsSync APK (async adoption +
  the per-file socket-timeout fix); until operators update, they keep using the
  unchanged synchronous route.

## 2026-06-11 — fix(demo): drop obsolete watchtower override from demo compose

The v2.70.x audit removed the `watchtower` service from the base
`docker-compose.yml`, but `docker-compose.demo.yml` still carried its
`deploy: replicas: 0` disable-override. With no base definition left, the
merged demo project contained a `watchtower` service with neither an image
nor a build context, and `docker compose up` for the demo stack failed with
"invalid compose project" — blocking the demo's update to v2.70.1. Removed
the dead override; demo stack composes and deploys again.

**Demo deployed to v2.70.1 the same night** (`~/droneops-demo` on BOS-HQ,
v2.68.4 → v2.70.1). Two further blockers cleared during the bring-up, both
recorded in `docs/incidents/2026-06-11-deploy-rename-conflicts-demo-bringup.md`:
a label-less legacy `droneops-demo-backend-1` (created outside compose) had to
be removed before compose could own the name, and recreating the cloudflared
sidecar exposed a stale `CLOUDFLARE_TUNNEL_TOKEN` in `.env.demo` ("Invalid
tunnel secret" → public 530); refreshed from the Cloudflare API. Verified:
all 6 services healthy, migrations brownfield-stamped + upgraded per ADR-0022
with data preserved, `https://command-demo.barnardhq.com` → 200.

**Prod post-deploy cleanup:** the v2.70.0/v2.70.1 prod deploys were reported
"failed" by the NOC deployer because `compose up` hit container-rename
conflicts mid-recreate — the new images actually went live (verified by image
build timestamps + in-container checks). The two leftover rename-prefixed
containers (`<hex>_droneops-worker-1`, `<hex>_droneops-flight-parser-1`) were
`docker rename`d back to canonical so the next deploy doesn't re-conflict.

## 2026-06-11 — perf(frontend): Settings split into lazy tabs, async backup UI, cache rollout — v2.70.1

Audit findings P2-4, P2-5, P3-4 (the last open items). Frontend suite:
**52 passed, 0 failed** (incl. one stale test fixed); build clean.

* **Settings.tsx: 2715-line monolith → 134-line shell + 11 lazy per-tab
  subtrees** (`src/pages/settings/`). Mount burst drops from ~19 parallel
  GETs + 13 simultaneously-mounted forms to **1 GET** (active tab only,
  `keepMounted={false}`); every tab is its own lazy chunk. Tab list,
  labels, fields, and submit payloads preserved exactly.
* **Backup UI uses the v2.70.0 job API**: start job → poll every 2s with
  phase/progress bar → fetch result; restore passes the validated
  temp_path. Graceful fallback to the old sync endpoints on 404 so deploy
  version-skew can never break backups.
* **useApiCache rollout (P2-4):** shared reference reads (`/customers`,
  `/aircraft`, `/rate-templates`, `/settings/weather`) now cached +
  deduped across Dashboard/Flights/Customers/Maintenance/Batteries/
  Airspace/Settings-Fleet, with write-then-invalidate audited on every
  mutating handler. Primary list payloads that pages mutate locally were
  deliberately left imperative (loading/refresh semantics unchanged).
* **Airspace poll (P3-4):** tab-visibility guard added (house pattern from
  MissionDetail) + interval backed off 10s → 15s; UI labels updated.
* **Test fix:** `MissionInvoiceEdit.test.tsx` mocked the long-gone
  per-item POST/DELETE loop; the editor has used atomic
  `PUT /invoice/items` since the non-transactional loop was replaced.
  Mocks updated to the real contract — the save-then-cancel guard test
  now actually exercises the save path.

## 2026-06-11 — feat(platform): Alembic migrations, async backup jobs, lean mission list, Stripe client isolation — v2.70.0

Finish-all pass over every remaining audit finding (FU-8 #1-#6, P2-2/-3/-6,
P3-1/-2/-3/-5/-6). ADR-0022. Backend suite: **409 passed, 0 failed**.

* **Alembic adopted (ADR-0022).** Startup schema management moved from
  `create_all` + ad-hoc ALTERs to versioned migrations run programmatically
  in the lifespan (executor-offloaded, still inside the ADR-0021
  primary-only recovery guard). Baseline `0001` reproduces the exact legacy
  schema by construction; brownfield prod DBs (schema present, no
  `alembic_version`) are auto-stamped then upgraded; fresh DBs build from
  scratch. **Validated against a real Postgres 16 container in both modes —
  empty autogenerate diff each way.** Migration `0002` adds 7 more
  query-justified indexes (`flights.start_time`, `flights.created_at`,
  `missions.customer_id`, `missions.status`, `mission_images.mission_id`,
  `maintenance_records.aircraft_id`, `maintenance_schedules.aircraft_id`);
  unjustifiable candidates rejected in the ADR.
* **Backups became background jobs (additive).** `POST /api/backup/jobs`
  (202 + job_id) + `GET /api/backup/jobs/{id}` with phase/progress in Redis,
  executed by the Celery worker — a 600 s pg_dump no longer occupies an API
  request at all. Old sync endpoints kept working (deprecated). Restore
  temp-path hardened against traversal/symlink escape. Backup history now
  reads `.sha256` sidecars instead of re-hashing every dump per page view.
* **Mission list payload went from O(track points) to O(rows).** The list
  endpoint returned every mission's `flights[].flight_data_cache` — each a
  ~19k-point GPS track copy — that the list page never rendered (verified:
  sole consumer reads only scalars; client portal + DroneOpsSync confirmed
  non-consumers). New `MissionListItemResponse` + `noload` options; detail
  endpoint byte-identical.
* **Stripe key isolation.** Per-call `StripeClient` (SDK 11.4.1) replaces
  module-global `stripe.api_key` mutation — closes the key-rotation
  interleave window; executor offload (v2.68.8) preserved.
* **Health gate (P3-1):** `/api/health` 503s only on DB/Redis now — a
  Stripe outage can no longer restart a healthy API (status still reported
  in the body).
* **maintenance `/status` (P2-3):** latest-per-(aircraft,type) via PG
  `DISTINCT ON` instead of loading the whole table into Python.
* **flight_library finish:** `/reprocess` reuses the shared flight builder
  (unified import log line), dead `_save_original_file` deleted, stored-log
  lookup is a direct glob instead of an O(N) dir scan, and per-file batch
  isolation is pinned by tests (a parser timeout on file N can't lose
  files N+1…M).
* **client_portal (P3-6):** image_count via scalar COUNT — removes a latent
  MissingGreenlet lazy-load.
* **email service (P3-5b):** PDF attachment reads offloaded.
* **Compose (P2-6/P3-3):** `cpus: 2.0` fences on backend + worker;
  backend healthcheck `start_period` 45s → 90s.
* **BOS-HQ override versioned (P3-2, failover-sensitive).** The db-
  neutralization + DATABASE_URL reroute that prevent a same-host port-5434
  collision lived only on the host; now committed secret-free as
  `docker-compose.bos-prod.yml` (credential via `${BOS_PROMOTED_DATABASE_URL}`
  in the host .env, seeded) with a drift-check command; port map documented
  in CLAUDE.md.

## 2026-06-11 — feat(resilience): standby-safe startup + hot-path indexes + streaming flight ingest — v2.69.0

Phases 2–3 of the 2026-06-11 ground-up audit (findings #4 FAILOVER-SENSITIVE,
#7, #9). ADR-0021.

* **Backend no longer crash-loops when its database is a standby (ADR-0021,
  failover-hardening).** Schema DDL + seed + backfill previously ran on
  EVERY boot with no replica guard — booting against a read-only standby
  (mid-failover, or standby-first blue-green) died on "cannot execute … in
  a read-only transaction" during the exact window an outage is least
  acceptable. The lifespan now probes `pg_is_in_recovery()` first: on a
  standby it skips all writes with a WARNING and serves read traffic; on a
  primary it runs the sync as before (probe failure fails safe to
  primary). Restart-after-promotion re-runs the sync (documented).
  Filesystem-only steps still run regardless. 8 new tests.
* **Four hot-path indexes** (`CREATE INDEX IF NOT EXISTS`, primary-only via
  the same guard, WAL-replicates to the standby): `mission_flights.
  mission_id`, `flights.aircraft_id`, `customers.email` (login lookup),
  `line_items.invoice_id`. Each justified against a real query pattern in
  ADR-0021; candidates already covered by unique constraints rejected.
  CONCURRENTLY trade-off documented (revisit past ~1M rows).
* **Flight-log uploads stream to disk instead of buffering whole logs in
  RAM** — the operator's primary field workflow shared the OOM class fixed
  for images (v2.68.7) and backups (v2.68.8). `/upload`, `/device-upload`,
  and `/reprocess` now spool→stream with incremental SHA-256, and the
  parser POST sends a file object (httpx chunks it). ~480 lines of drifted
  duplication consolidated into shared helpers (`_spool_upload`,
  `_build_flight_from_parsed`, `_store_original_from_path`) with route
  contracts byte-identical — dedup-before-parse ordering, response shapes,
  error strings, audit logs, and `/reprocess`'s divergent update-in-place
  branch all preserved (the latter deliberately left inline rather than
  risk a log-line change). Audit's "4th duplicate handler" was a
  misread — `/import/opendronelog` takes no upload; untouched. 9 new
  tests incl. a load-bearing whole-file-read guard.
* Suite: **364 passed, 0 failed** (+17 from v2.68.8).

## 2026-06-11 — perf(stack): event-loop unblocking sweep + eager-load fixes from the ground-up audit — v2.68.8

Phase 1 of the 2026-06-11 ground-up audit (`docs/plans/2026-06-11-ground-up-audit.md`,
findings #1/#2/#3/#5/#6/#8 + test hygiene). Root cause of "slow and
unresponsive at times": specific operations froze or OOM-killed the single
uvicorn worker. All fixes are runtime-only (failover guard: clean).

* **Backups no longer freeze the whole API (P0).** `pg_dump` / `pg_restore` /
  `pg_restore --list` / SHA-256 hashing ran synchronously inside async
  handlers — up to **600 s of total event-loop freeze** (every request,
  including healthchecks, stalled). All offloaded to worker threads with
  identical timeout/exception semantics. Backup **uploads** also read the
  entire dump into RAM (`await file.read()`) — now streamed to disk in
  1 MiB chunks with incremental hashing (same OOM class as the v2.68.7
  image fix). 6 new tests incl. an event-loop-violation detector.
* **Financials dashboard + Mission Hub stop loading GPS tracks they never
  display (P0/P1).** The mapper-level `lazy="selectin"` on
  `MissionFlight.flight` made `selectinload(Mission.flights)` cascade into
  full `Flight` rows — gps_track (~19k points/flight), telemetry,
  raw_metadata — on `GET /api/financials/summary` (ALL billable missions,
  unbounded) and every `GET /api/missions` list/detail. `MissionResponse`
  never serializes `flight` (display data comes from `flight_data_cache`),
  so the graph is now scoped per-query: `raiseload(MissionFlight.flight)`
  (fails loudly if ever re-touched) + `defer(flight_data_cache)` /
  `raiseload(Mission.images)` where unused. Response shapes byte-identical;
  16 new tests incl. AST guard + booby-trapped serializer test.
* **Stripe SDK calls no longer block the event loop (P1).** Every payment
  and webhook made synchronous HTTPS round-trips to api.stripe.com on the
  loop thread (checkout create, session retrieve, PaymentIntent/
  PaymentMethod retrieve). New `_stripe_call()` executor helper wraps all
  four network sites; `Webhook.construct_event` stays inline (CPU-only
  HMAC). 9 new tests.
* **Logo upload (PIL) + signed-TOS PDF render offloaded (P1)** — the last
  two inline CPU-bound blocks in async handlers, now matching the
  missions.py/aircraft.py executor pattern. 5 new tests.
* **Test suite: 347 passed, 0 failed (was 14 failing).** Py3.12
  event-loop pollution fixed at source (`asyncio.get_event_loop()` →
  `asyncio.run()` in flight-attribution/weather tests; one earlier
  `asyncio.run()` anywhere poisoned `get_event_loop` suite-wide), and the
  stripe health-probe tests patched a stale target (`app.config.settings`
  → `app.main.settings`) — the app's env fallback was always correct.

## 2026-06-11 — fix(missions): memory-safe image uploads, 60 MB cap, working previews — v2.68.7

* **Mission-editor image uploads no longer kill the backend; the cap is now
  60 MB (was 50 MB).** Operator uploaded a batch of 40–46 MB DJI stills at
  02:28 UTC; the kernel cgroup OOM-killer killed uvicorn twice (02:29:14,
  02:31:34 — `dmesg`, `CONSTRAINT_MEMCG`, anon-rss ≈ 1 GiB), failing every
  in-flight upload and restarting the backend (RestartCount=5). Same 1 GiB
  worker OOM class as v2.68.5/v2.68.6, third code path. Three compounding
  causes, all fixed:
  * **Multipart spool pinned uploads in RAM.** v2.39.3 set
    `MultiPartParser.max_file_size = 200 MB` believing it was a size cap; in
    Starlette it is the `SpooledTemporaryFile` roll-to-disk threshold and file
    parts are never rejected on size (`formparsers.py:204`). Now 4 MB — big
    uploads spool to disk during parse.
  * **Route buffered + decoded full-resolution.** `await file.read()` held the
    whole file as bytes, then PIL decoded the full 48 MP image (~150 MB) plus
    an EXIF-transpose copy. The route now streams from the spooled temp file,
    uses JPEG **draft-mode decode** (scale ≥2× the 1920 px target → ~35 MB),
    and bounds concurrent decodes with a 2-slot semaphore. Per-upload transient
    drops from ~400 MB to <40 MB.
  * **Headroom:** backend `mem_limit` 1g → 1536m (RSS ratchets under malloc
    fragmentation; BOS-HQ has >20 GiB available).
  * **Thumbnails after upload were 404.** The editor built `/uploads/<basename>`
    but mission images are stored at `<upload_dir>/<mission_id>/<file>`; the
    static route joins its path arg onto `upload_dir`. URL now includes the
    mission-id segment.
  * Cheap rejections (content-type, size) now run **before** any byte
    processing; a 413 leaves nothing on disk.
  * Tests: `backend/tests/test_mission_image_upload.py` — 8 cases through the
    full ASGI stack incl. a 55 MB accept (old cap regression), 61 MB → 413,
    EXIF orientation, raw-copy fallback, and a guard that fails if anyone
    reintroduces a whole-file `UploadFile.read()`.

## 2026-06-11 — fix(reports): simplify GPS tracks before buffering — stops "Generate Report → Cloudflare 520" OOM — v2.68.6

* **Clicking "Generate Report" no longer OOM-kills the backend worker; the
  Cloudflare 520 origin-error page is gone (ADR-0020).**
  `POST /api/missions/{id}/report/generate` runs the GPS-geometry pipeline
  synchronously before dispatching the Celery job. `calculate_area_acres`
  (`backend/app/services/map_renderer.py`) built one `MultiLineString` from the
  raw flight tracks and `.buffer(30)`-ed it. A real mission had 3 flights /
  **33,830 GPS points**; buffering that many near-collinear vertices makes GEOS
  allocate **>900 MB**, which — on top of the live worker baseline — blew the
  **1 GiB** container cgroup and the kernel OOM-killed uvicorn mid-response.
  Cloudflare received an incomplete response → 520, surfaced to the operator as
  a red "Generation Failed" notification. Same failure class as the v2.68.5
  mission-picker OOM (heavy GPS data + 1 GiB worker), different code path.
  * **Fix:** Douglas-Peucker-simplify each track in projected UTM space at a 2 m
    tolerance, then buffer each line independently and `unary_union` the result —
    every intermediate geometry stays small. Acreage is stable to **<1%**
    (live convergence: 68.51 ac @5 m → 69.11 ac @0.5 m). 5 tests; build clean.
