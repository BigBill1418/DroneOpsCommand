# DroneOpsCommand — Ground-Up Technical Audit (2026-06-11)

**Status:** Analysis complete — findings report. No code modified.
**Auditor:** Terry (research/architect). Deep-dives by 3 parallel sub-agents (backend perf/mem, frontend perf, reliability/compose) + direct integration & code-quality review.
**Scope:** `/home/bbarnard065/droneops` (DroneOpsCommand) + `/home/bbarnard065/DroneOpsSync` + the integration between them.
**Live host:** BOS-HQ (10.99.0.4), blue-green standby topology, PostgreSQL streaming replication.
**HEAD version:** v2.68.6 (`backend/app/main.py:414`).
**Trigger:** Bill reports the stack is "slow and unresponsive at times"; wants it audited and improved "ground up — clean and more efficient."

> **Out of scope / already being fixed (do NOT re-fix):** tonight's image-upload OOM
> (whole-file-in-RAM + PIL decode of ~45 MB DJI images under backend `mem_limit: 1g`).
> Bill is fixing that path personally. This audit notes the **same memory/streaming
> anti-pattern where it recurs in OTHER code paths** (backup upload/restore, the four
> flight-log upload handlers, email PDF attach) and treats the 1 GiB backend cgroup as
> the binding constraint behind a *class* of incidents, not a one-off.

> **Prior baselines:** ADR-0004/0005 (2026-04-24 perf audit — weather gather+cache,
> pool 5+10→20+20, `get_current_user` cache, all shipped). ADR-0019 (flight-library LIST
> defers heavy JSON). ADR-0020 (report-gen geo-buffer simplify). These are **done** and
> excluded except where the same pattern recurs elsewhere.

---

## Failover & Resilience Guard note (per CLAUDE.md)

Every fix below is tagged **FAILOVER-SENSITIVE** if it touches PG streaming replication,
container-recreation survival, the blue-green swap, the failover engine, or customer-facing
service during a site failover. Two findings carry this flag: **B-1** (startup DDL/seed on the
live primary) and **R-4** (host-port collision on a same-host blue-green). The recommended
fixes for both are designed to *increase* resilience (recovery-guard + versioned override),
not weaken it.

---

## The headline diagnosis (why it feels slow/unresponsive)

The "slow and unresponsive at times" symptom is **not** steady-state latency — ADR-0005
already drove the hot endpoints to sub-second. It is **event-loop freezes and OOM-kills
under specific operations**, which present to the operator as "the whole app hangs / 502s
for a minute, then recovers." Four independent freeze/kill sources, all on the single
uvicorn worker behind the 1 GiB cgroup:

1. **Blocking work in async handlers** freezes the *entire* event loop (every concurrent
   request, including healthchecks) for the duration: `pg_dump`/`pg_restore` subprocess
   (backup), the synchronous Stripe SDK (every payment + webhook), PIL decode (logo/signed-TOS),
   PDF merge. → findings **B-2, B-4, B-5, B-6**.
2. **Eager-loading heavy GPS JSON** via the `MissionFlight.flight` `lazy="selectin"`
   relationship pulls full `flights` rows (with `gps_track`) into *every* mission/financials
   path — the same memory class that OOM-killed flight-library and report-gen, now latent on
   the Mission Hub + Financials dashboards. → findings **B-1-mem, B-3**.
3. **Whole-file-in-RAM uploads** beyond the image path: the **four near-duplicate flight-log
   upload handlers** and the **backup upload/restore** all `await file.read()` the entire file,
   then re-buffer it for the parser/hash. → findings **I-1, B-7**.
4. **The 1 GiB backend cgroup** is sized for steady-state (~325 MiB) but is the ceiling that
   turns each of the above into a hard OOM-kill rather than a slow request. → finding **R-2**.

Fix those four and the "hangs at times" symptom largely disappears. The frontend is already
in good shape (ADR-0004's structural P0s are resolved); its remaining items are mid-tier.

---

# Prioritized findings

Severity: **P0** blocker · **P1** high · **P2** medium · **P3** nice-to-have.
Within each severity, ordered by ascending effort (cheapest leverage first).

## P0 — fix first

### P0-1 — `financials_summary` eager-loads ALL billable missions + full Flight rows (gps_track) — unbounded OOM class
- **Severity:** P0 · **Effort:** Low-Med
- **Evidence:** `backend/app/routers/financials.py:23-32` — `select(Mission).where(is_billable==True).options(selectinload(Mission.flights)...)` with **no limit**. `MissionFlight.flight` is `lazy="selectin"` (`backend/app/models/mission.py:187`), so loading `Mission.flights` *also* selectin-loads every full `Flight` row — including `flights.gps_track` / `telemetry` / `raw_metadata` JSON — even though this endpoint only reads `flight.aircraft.model_name`.
- **Impact:** The Financials dashboard decompresses every TOASTed GPS track for the entire billable history on each load. This is the exact >1 GiB blow-up class that ADR-0019/0020 fixed elsewhere; under `mem_limit: 1g` it OOM-kills uvicorn as flight history grows. Customer-facing (dashboard hang/502).
- **Fix:** (a) Quickest: add `lazyload(MissionFlight.flight)` to this query — financials never touches `mf.flight`. (b) Best: replace the ORM-graph load + Python loop with SQL aggregates (`func.sum`/`group_by` joining `missions`→`invoices`→`line_items` and a per-drone/per-source aggregate). PG does this faster and without materializing tracks.

### P0-2 — Backup create/restore/validate run `pg_dump`/`pg_restore`/`psql` synchronously in async handlers (event-loop freeze up to 600 s)
- **Severity:** P0 · **Effort:** Low
- **Evidence:** `backend/app/routers/backup.py` — `_run_pg_command` uses `subprocess.run(..., timeout=600)` (`:38-43`) and is called from `async def` handlers: `create_and_download` (`:98`), `restore_from_upload` (`:259`), `run_backup_now` (`:478`), `_validate_archive` (`:177`), plus a post-restore `psql` (`:282`). None use `run_in_executor`.
- **Impact:** A single backup/restore freezes the **whole** uvicorn event loop for the full dump/restore duration — every other request (health, client portal, API) stalls. Healthchecks fail mid-backup → container restart mid-restore. This is a prime "the app went unresponsive for a minute" cause.
- **Fix:** Wrap every `_run_pg_command` / `_compute_sha256` / `_validate_archive` call in `await asyncio.get_running_loop().run_in_executor(None, ...)`. Long-term: move backup/restore to a Celery job returning a job id.

### P0-3 — Backup upload reads the entire DB dump into RAM (`await file.read()` + sync `open().write()`)
- **Severity:** P0 · **Effort:** Low
- **Evidence:** `backend/app/routers/backup.py:169-171` (`validate_upload`) and `:222-224` (`restore_from_upload`): `content = await file.read()` loads the whole uploaded dump (a full DB backup — can be hundreds of MB) into one bytes object, then `hashlib.sha256(content)` + a synchronous `open().write()`. Same whole-file-in-RAM anti-pattern as the image OOM, on a *much larger* file.
- **Impact:** Under `mem_limit: 1g`, a moderate dump upload OOM-kills the worker before restore even begins. Self-inflicted outage during the one operation you run when recovering.
- **Fix:** Stream the upload to a temp file in chunks (`while chunk := await file.read(1<<20): f.write(chunk)`), hashing incrementally over the same chunks; do the file write in an executor.

---

## P1 — high

### P1-1 [FAILOVER-SENSITIVE] — Schema DDL + seed/backfill writes run on EVERY backend boot, against the live promoted primary
- **Severity:** P1 · **Effort:** Low (guard) → High (Alembic)
- **Evidence:** `backend/app/main.py:302-304` lifespan runs `Base.metadata.create_all` + `_add_missing_columns` (a large `CREATE TABLE` / `ALTER TABLE ADD COLUMN` / `ALTER TYPE ADD VALUE` / `ALTER COLUMN ... TYPE TEXT` / `ADD CONSTRAINT` batch, `main.py:67-253`), then `seed_database()` (`:309`), demo seed (`:315`), admin auto-create (`:325-333`), aircraft-image copy (`:343-362`), and a flight→aircraft **DB backfill commit** (`:375-396`). `backend/app/database.py:6` builds one engine from `settings.database_url`, which on BOS-HQ routes to `droneops-standby-db` (the promoted primary, `CLAUDE.md:109`). **No Alembic** exists (`find` confirms no `alembic/`/`migrations/`); this imperative startup block is the de-facto migration mechanism. **No `pg_is_in_recovery()` guard** anywhere.
- **Impact (all five Guard questions):**
  - **Crash-loop on standby:** if `DATABASE_URL` ever resolves to a real read-only standby (mid-failover/misconfig), `engine.begin()` + `create_all` raises *"cannot execute … in a read-only transaction"* → lifespan aborts → backend crash-loops → **customer-facing outage during failover** (Q4/Q5).
  - **Blue-green hazard:** a standby-first deploy boots the NEW image and mutates schema *before* the swap; the OLD (still-serving) image now talks to mutated schema. Additive-only today, but one non-additive change (column rename, NOT NULL without default, type narrow) ships breaking DDL straight into the live primary at container-recreate (Q3).
  - **Replication hazard:** `ALTER COLUMN ... TYPE TEXT` (`main.py:222`) takes ACCESS EXCLUSIVE + full table rewrite; under load it blocks primary reads and lags the CHAD-HQ failback standby. `ALTER TYPE ADD VALUE` (`:110`) is non-rollback-able (Q1).
- **Fix (ordered):**
  1. *Cheap/immediate:* wrap the DDL+seed+backfill block in `SELECT pg_is_in_recovery()`; if true, skip and just serve. ~10 lines around `main.py:302`. Stops crash-loop-on-standby today.
  2. *Medium:* gate DDL behind `RUN_MIGRATIONS=true` so only one designated container (or a one-shot deploy job) mutates schema — not every backend/worker/beat boot.
  3. *Structural:* adopt Alembic with reviewed, blue-green-aware (expand/contract) migrations; retire `_add_missing_columns`.

### P1-2 — `MissionFlight.flight` `lazy="selectin"` drags full Flight rows (gps_track) into list_missions / get_mission / reports / PDF
- **Severity:** P1 · **Effort:** Med
- **Evidence:** `backend/app/models/mission.py:187` (`MissionFlight.flight = relationship(..., lazy="selectin")`). `list_missions` (`backend/app/routers/missions.py:90-99`) returns **all** missions with `selectinload(Mission.flights)` and **no pagination**; `MissionResponse` serializes `MissionFlight.flight_data_cache` (heavy JSON duplicating the GPS track per attached flight) *and* the selectin pulls the full `flights` row. The mission-list table view never needs GPS tracks.
- **Impact:** Mission Hub memory + payload grow with both mission count and per-flight track size — the same eager-heavy-JSON class as ADR-0019, now on the missions surface. Primary "Mission Hub feels slow" candidate. Shares root cause with P0-1.
- **Fix:** (a) Add `limit`/`offset` pagination to `list_missions`. (b) Set `lazyload(MissionFlight.flight)` on the list query (the `flight_data_cache` already holds display fields). (c) `defer(MissionFlight.flight_data_cache)` on the list and return a lean summary schema; load the full cache only in `get_mission`. **Highest-leverage single change** — fixing the relationship default + per-path eager-load addresses P0-1 and P1-2 together.

### P1-3 — Synchronous Stripe SDK calls block the event loop on every payment + webhook
- **Severity:** P1 · **Effort:** Low
- **Evidence:** `backend/app/services/stripe_service.py:137` (`stripe.checkout.Session.create` in `async def`); `backend/app/routers/client_portal.py:499` (`_stripe.checkout.Session.retrieve` on every Pay click, idempotency path); `backend/app/routers/stripe_webhook.py:135,137` (`PaymentIntent.retrieve` + `PaymentMethod.retrieve` in async). The synchronous `stripe` SDK makes blocking HTTPS round-trips to api.stripe.com.
- **Impact:** Every customer payment and every Stripe webhook freezes all concurrent requests for the Stripe round-trip (100 ms–several s). Webhook retry storms compound it. Customer-facing.
- **Fix:** Wrap each Stripe call in `run_in_executor`, or migrate to the async Stripe client (`*_async` methods / `StripeClient`). At minimum the three call sites above.

### P1-4 — Logo upload + signed-TOS render do PIL/PDF work synchronously in async handlers (inconsistent with missions.py/aircraft.py)
- **Severity:** P1 · **Effort:** Low
- **Evidence:** `backend/app/routers/system_settings.py:200-219` — `PILImage.open(io.BytesIO(content))` + resize/save + sync `open(...).write()` inline in the async handler. `backend/app/routers/intake.py:484-620` (`get_signed_tos`) — `Image.open`, `PdfReader`, per-page `merge_page`, reportlab `canvas`, `writer.write` all inline, none offloaded. The sibling image handlers in `missions.py:607` and `aircraft.py:34` correctly use `run_in_executor` — these two were missed.
- **Impact:** CPU-bound PIL decode/encode + PDF merge freeze the event loop per logo upload / per "view signed TOS." Same class as the image-OOM path, smaller blast radius.
- **Fix:** Extract the PIL/PDF blocks into module-level sync helpers and call via `run_in_executor`, mirroring `aircraft.py`/`missions.py`.

### P1-5 — Missing indexes on hot FK/filter columns (no schema indexes exist anywhere except the TOS audit table)
- **Severity:** P1 (for the hot ones) · **Effort:** Low-Med
- **Evidence:** `grep index=True backend/app/models/` returns hits only in `tos_acceptance.py`. No `sa.Index`/`op.create_index` anywhere; schema is `create_all` + idempotent ALTERs. The following are filtered/joined/ordered on hot paths:
  - **`mission_flights.mission_id`** (`models/mission.py:173`) — joined on every mission detail/report/financials path. *(P1-grade)*
  - **`flights.aircraft_id`** (`models/flight.py:48`) — `GROUP BY` in `maintenance.py:504` (full `flights` scan on every dashboard `/status`). *(P1-grade)*
  - **`flights.start_time`** (`models/flight.py:32`) — default list sort/filter (`flight_library.py:346,387`); full sort.
  - **`line_items.invoice_id`** (`models/invoice.py:330`) — every `Invoice.line_items` selectin.
  - **`customers.email`** (`models/customer.py:539`) — client-portal login lookup (`client_portal.py:159`); login-path full scan. *(P1-grade)*
  - **`maintenance_records.aircraft_id`**, **`maintenance_schedules.aircraft_id`**, **`mission_images.mission_id`**, **`missions.customer_id`/`status`/`mission_date`** — dashboard/list/windowed-aggregate filters.
  - Already covered (PG auto-creates unique index): `invoices.mission_id` and `reports.mission_id` are `unique=True`. No action.
- **Impact:** Full-table scans/sorts on every dashboard and login. Cheap to remove; grows worse with data.
- **Fix:** Add `index=True` to the columns above and create them via a forward-safe `CREATE INDEX IF NOT EXISTS` (ideally `CONCURRENTLY` outside a txn) consistent with the existing `_add_missing_columns` idempotent pattern so standby promotion stays failover-safe. Prioritize the four P1-grade ones.

---

## P2 — medium

### P2-1 [INTEGRATION] — Four near-duplicate flight-log upload handlers, each whole-file-in-RAM (same OOM class as the image path)
- **Severity:** P2 (memory leg is P1-adjacent under the 1 GiB cap) · **Effort:** Med
- **Evidence:** `backend/app/routers/flight_library.py` has **four** near-identical ingest bodies: `device-upload` (`:806`, DroneOpsSync path), `reprocess` (`:1146`), `upload` (`:1472`, JWT/web path), and the ODL ingest (`~:1700`). Each does `content = await upload.read()` (whole file in RAM, `:836`/`:1169`/`:1491`), `hashlib.sha256(content)`, `_save_original_file(file_hash, content, ...)` (synchronous `dest.write_bytes(content)`, `:153`), then re-buffers the *same* `content` into the parser POST `files={"file": (..., content)}` with `httpx.AsyncClient(timeout=120)`. The parse → dedup → `Flight(...)` → `_track_battery` block is copy-pasted across all four (~120 lines each, ~480 lines of duplication — a large share of the 2144-line file).
- **Impact:** (a) **Memory:** each DJI flight record (2.8–8.9 MB typical, larger for long missions) is held in RAM at least twice (read buffer + parser POST buffer) per file, looped over a multi-file batch — the same whole-file-in-RAM class Bill is fixing for images, on the operator's *primary field workflow*. ADR-0019 already showed this path can drive the 1 GiB cgroup OOM. (b) **Maintainability:** a fix or dedup-logic change must be applied 4× and has already drifted (only some paths `break` on parser-down). (c) **Latency coupling:** see P2-2.
- **Fix:** Extract one `_ingest_flight_log(content_or_path, filename, db, parser_headers) -> IngestResult` helper; have all four handlers call it. Stream-hash and stream to disk in chunks rather than holding `content` whole; pass the saved file path (or a streaming body) to the parser instead of re-buffering bytes. This collapses ~480 duplicated lines and removes the double-buffering memory hit on the field path.

### P2-2 [INTEGRATION] — Upload handler ↔ flight-parser ↔ Sync client timeout chain is brittle (120 s end-to-end, in-request)
- **Severity:** P2 · **Effort:** Low
- **Evidence:** Backend parser POST uses `httpx.AsyncClient(timeout=120)` (`flight_library.py:850,1094,1175,1506`); the DroneOpsSync client OkHttp `readTimeout(120s)`/`writeTimeout(120s)` (`DroneOpsSync/.../api/ApiClient.kt:115-117`). Parsing happens **synchronously inside the upload request** — the controller holds an HTTP connection open for the full parse of a multi-file batch. There is no async/job-id handshake; a slow parse on a large batch can hit the 120 s ceiling on either side and abort, and the partial DB writes from earlier files in the loop are already committed (flush per flight) while later files are lost.
- **Impact:** On large batches or a slow parser, the field upload times out; the operator sees a partial/failed sync and must retry, re-uploading already-ingested files (dedup saves the DB but not the bandwidth/time). Fragile on the operator's primary workflow.
- **Fix:** Decouple: accept the upload (stream to disk, return 202 + a sync-batch id), parse in a Celery task, expose `GET /device-upload/status/{batch_id}`. The Sync client already has a diagnostics/history surface to poll it. Short of that, lower the per-file in-request work and return per-file status so a timeout on file N doesn't lose files N+1…M.

### P2-3 — `maintenance_status` loads ALL maintenance records then dedups in Python; same handler does an unindexed `flights` GROUP BY
- **Severity:** P2 · **Effort:** Low
- **Evidence:** `backend/app/routers/maintenance.py:524-534` — `select(MaintenanceRecord).order_by(performed_at desc)` with no limit, then a Python `(aircraft_id, type) → latest` map over the whole table; plus the unindexed `GROUP BY Flight.aircraft_id` at `:504`. Both on every dashboard `/status` hit.
- **Impact:** Full-table loads that grow with history, on a frequently-polled dashboard endpoint.
- **Fix:** Postgres `DISTINCT ON (aircraft_id, maintenance_type) ... ORDER BY aircraft_id, maintenance_type, performed_at DESC` for latest-per-group; add the `flights.aircraft_id` index (P1-5).

### P2-4 — Frontend: `useApiCache` exists but only 2 of 19 pages use it
- **Severity:** P2 · **Effort:** Low-Med (mechanical, per-page)
- **Evidence:** `frontend/src/hooks/useApiCache.ts` (30 s TTL, request dedup, `invalidate(prefix)`) is imported only by `Dashboard.tsx` and `Flights.tsx`. The other 17 pages use raw `api.get()` in `useEffect` (e.g. `Maintenance.tsx:107`, `Customers.tsx`, `Batteries.tsx`, `Airspace.tsx:110`, `MissionDetail.tsx`). Navigating away and back re-issues every round-trip; shared keys (`/aircraft`, `/pilots`) are not deduped across pages.
- **Impact:** Redundant network + backend load on every navigation; re-load flash on revisited pages. Magnified because the backend is the slow tier.
- **Fix:** Migrate read-only GETs on high-traffic pages (Settings, Maintenance, Batteries, Customers, MissionDetail) to `useApiCache`. Shared `/aircraft` + `/pilots` collapse to one round-trip fleet-wide.

### P2-5 — Frontend: `Settings.tsx` is 2715 lines, fires 19 uncached parallel GETs on mount, zero memoization, 13 `useForm()` instances
- **Severity:** P2 · **Effort:** Med
- **Evidence:** `frontend/src/pages/Settings.tsx:192-213` — one `useEffect([])` issues **19** `api.get()` calls (34 total in file); `grep -c useMemo|useCallback|memo` = 0; `grep -c useForm` = 13, all mounted simultaneously. (Note: the 19 are fire-and-forget *parallel*, so latency is bounded by the slowest call — better than ADR-0004's "34 sequential" framing — but uncached and many duplicate other pages' endpoints.)
- **Impact:** Settings mount = the app's worst single-page request burst + a very large single render tree; every keystroke in any form re-renders 2715 lines.
- **Fix:** (a) route the 19 GETs through `useApiCache`; (b) split into lazy per-tab subtrees so only the active tab fetches/renders; (c) ideally a single backend aggregate endpoint for the settings bundle.

### P2-6 — No CPU limits on any service except ollama
- **Severity:** P2 · **Effort:** Low · **Runtime-only (not failover-sensitive)**
- **Evidence:** Across all four compose files only `ollama` has `cpuset: "0-5"` (`docker-compose.yml:95`). Backend, worker, beat, db, redis, flight-parser have no `cpus`/`cpu_shares`. MEMORY (`project_hsh_hq_high_load_systemplane_20260604`) + the 2026-04-19 incident show CPU starvation is a real failure mode on these hosts.
- **Impact:** A runaway Celery report task or busy-loop can monopolize all non-ollama cores and starve PG/redis on the same box — degrading the customer path with no resource fence (only OOM fences memory today).
- **Fix:** Add modest `cpus:` limits to backend (~2.0) and worker (~2.0).

---

## P3 — nice-to-have

### P3-1 — Backend `/api/health` couples liveness to Stripe → an external outage can restart a healthy API
- **Severity:** P3 · **Effort:** Low
- **Evidence:** `backend/app/main.py:647-651` — a cached Stripe `Account.retrieve` error sets `degraded=True` → `/api/health` returns **503** → Docker healthcheck (`docker-compose.yml:199`) marks unhealthy after 5 retries → `restart: unless-stopped` recreates a perfectly-serving API because *Stripe* had an outage or a bad key.
- **Impact:** Self-inflicted restart vector tied to a third party. DB+Redis in the gate is correct; Stripe should not be able to restart the API.
- **Fix:** Keep Stripe in the health *body* for observability; exclude it from the `degraded` flag that drives the 503 (only DB+Redis gate container health).

### P3-2 [FAILOVER-SENSITIVE, informational] — `db` host-port bindings can collide on a same-host blue-green/failback; safety lives in an un-versioned host override
- **Severity:** P3 · **Effort:** Low
- **Evidence:** `docker-compose.yml:43-44` binds `127.0.0.1:5434:5432` + `10.99.0.1:5434:5432`; `docker-compose.standby.yml:21` binds `5434:5432`; demo-standby `5437`. On BOS-HQ the base `db` is "neutralized to a sleeping alpine" — but that neutralization + the `DATABASE_URL` reroute to `droneops-standby-db` (`CLAUDE.md:109`) live in a **host-side override not tracked in the repo**. If that override is ever absent during a deploy, base `db` tries to bind `5434` and collides with the running standby/primary → stack bring-up fails.
- **Impact:** Latent blue-green foot-gun; resilience depends on tribal host knowledge.
- **Fix:** Commit the BOS-HQ override (e.g. `docker-compose.bos-prod.yml`) so the neutralization + reroute are versioned and reviewable; document the port map in CLAUDE.md.

### P3-3 — Backend `start_period: 45s` is shorter than the worst-case dependency-wait + DDL window
- **Severity:** P3 · **Effort:** Low
- **Evidence:** `docker-compose.yml:203` `start_period: 45s`, but lifespan does `_wait_for_db` (≤30 s) + `_wait_for_redis` (≤30 s) + `create_all` + seed + image copy + flight backfill before `yield` (`main.py:256`+). On a cold/slow standby promotion those waits alone can exceed 45 s.
- **Impact:** First healthchecks during a slow-dependency boot fail and burn retries; a genuinely slow standby promotion could flap. (Has slack from `retries: 5 × 30s`, but tight.)
- **Fix:** Bump backend `start_period` to ~90 s.

### P3-4 — Frontend: `Airspace.tsx` polls every 10 s with no tab-visibility guard
- **Severity:** P3 · **Effort:** ~15 min
- **Evidence:** `frontend/src/pages/Airspace.tsx:179` `setInterval(fetchAircraft, 10000)` with no `document.visibilityState` guard — keeps hitting the external-data (OpenSky-backed, rate-limited) airspace endpoint every 10 s even when the tab is backgrounded. The most aggressive poll in the app; `MissionDetail.tsx:123-126` already has the correct guard to copy.
- **Impact:** Continuous background polling burns backend calls + external rate limit.
- **Fix:** Add `if (document.visibilityState !== 'visible') return;` inside the tick (+ optionally back off to 15–20 s).

### P3-5 — Backup history page SHA-256s every dump file synchronously; email PDF attach reads whole file on the loop; `_get_stored_file_path` scans the whole dir
- **Severity:** P3 · **Effort:** Low
- **Evidence:** `backend/app/routers/backup.py:430` (`_compute_sha256` per dump in an async loop — reads + hashes every backup file, hundreds of MB, on the event loop); `backend/app/services/email_service.py:132-133` (`f.read()` whole PDF on the loop — small, modest); `backend/app/routers/flight_library.py:142-148` (`_FLIGHT_LOGS_DIR.iterdir()` O(N) scan to find one hash).
- **Fix:** Offload the backup hashing to an executor and/or cache checksums in a sidecar `.sha256` at creation; offload the email read; glob the known extension set directly instead of iterating the dir.

### P3-6 — `get_client_mission` lazy-counts `mission.images` after the await boundary (latent MissingGreenlet / extra query)
- **Severity:** P3 · **Effort:** Low
- **Evidence:** `backend/app/routers/client_portal.py:271-278` — `select(Mission)` with no `.options()`, then `len(mission.images)`. `Mission.images` is `lazy="selectin"`; accessing the un-loaded collection post-await forces an implicit lazy emit on the async session (raises or does a second round-trip).
- **Fix:** Use a scalar count `select(func.count()).select_from(MissionImage).where(...)` (the pattern already used correctly at `missions.py:616`).

---

# Code-quality / cleanliness

- **`backend/app/routers/flight_library.py` is 2144 lines** — the largest backend file, driven primarily by the **4 duplicated ingest bodies** (P2-1). Splitting it into `flight_library.py` (list/stats/reprocess-status) + `flight_ingest.py` (the one shared `_ingest_flight_log` + the 4 thin route wrappers) would cut it roughly in half and remove the drift risk.
- **`backend/app/routers/client_portal.py` is 1010 lines** — mixes customer auth, payment (deposit/balance/legacy-alias), mission detail, and invoice serving. The legacy `/pay` alias re-enters `_load_pay_context` (double recalc + flush per payment, `:752-758`) — see fix in P2 notes. Candidate to split auth/payment/mission concerns.
- **`Settings.tsx` (2715), `MissionWizardLegacy.tsx` (1441, legacy soak-fallback route only), `Dashboard.tsx` (1207), `Flights.tsx` (1165)** are the largest frontend files; only `Settings.tsx` is a genuine render/fetch concern (P2-5).
- **Inconsistent `run_in_executor` discipline** is the single clearest cleanliness theme: `missions.py`/`aircraft.py` offload image resize correctly; `backup.py` (subprocess), `stripe_service.py` (SDK), `system_settings.py` (logo PIL), `intake.py` (PDF merge) do not. A "offload all blocking work" sweep across these four is the highest-leverage consistency fix and directly removes the event-loop-freeze sources behind the "unresponsive" symptom.

---

# Things that are already CLEAN (verified — no action)

- **Frontend bundle/splitting:** ADR-0004's 1.9 MB monolith is **gone** — `App.tsx:21-59` lazy-loads all 17 pages; `vite.config.ts:51-82` `manualChunks` per heavy vendor; router shell `index-*.js` is 88 KB. PDF (`react-pdf` 413 KB) + leaflet (154 KB) are isolated lazy chunks; `pdf.worker` loaded off-thread. Uses `dayjs` not moment. No React Context → no wide context-cascade re-renders.
- **Auth:** the v2.38.x 401-swallow is **fixed** — `frontend/src/api/client.ts:56-62` logs before redirect and single-flights concurrent 401 refreshes. `reset_admin_password` is **fully removed** (grep-confirmed; the every-restart-overwrite bug is gone). `seed.py:167` uses a PG advisory lock to serialize seeding across workers.
- **Healthchecks:** backend (`main.py:595`, real DB+Redis probe through the pool) and worker (`docker-compose.yml:239-245`, Redis heartbeat-key age) are genuinely good — they would NOT stay green during DB-pool exhaustion or a frozen control loop. Only the Stripe coupling (P3-1) needs trimming.
- **Error handling:** the `except Exception: pass` blocks are all genuinely best-effort/safe (EXIF transpose, parser health probe, device `last_used_at`, weather station lookup, ollama readiness retry which *does* raise on final failure). Stripe webhook verifies signature, distinguishes error types, alerts via ntfy, returns 400. DB session commits/rolls-back/closes correctly with `pool_pre_ping` + `pool_recycle` (survives container recreation — Guard Q2 ✓).
- **DroneOpsSync client:** `ApiClient.kt` correctly upgrades plaintext public URLs to HTTPS (the ADR-0002 CF-redirect fix), preserves LAN `http://`, uses a host-only baseUrl, single shared OkHttp client with sane connect timeout. Sound.
- **Memory limits:** every long-running service has a `mem_limit` (2026-06-05 BOS-HQ sweep). ollama (10g + 8g reservation + cpuset 0-5) is correctly sized.

---

# Recommended execution order (severity × effort)

1. **P0-2 + P0-3** — backup: offload `pg_*` subprocess + stream the dump upload. *(Low effort, removes a whole-app-freeze + a restore-time OOM.)*
2. **P0-1 + P1-2** — fix the `MissionFlight.flight` eager-load (relationship default + per-path `lazyload`/`defer` + paginate `list_missions`). *(One root cause, two dashboards.)*
3. **P1-3 + P1-4** — `run_in_executor` sweep across Stripe SDK, logo PIL, signed-TOS PDF (with backup from step 1 = the full offload-blocking-work sweep).
4. **P1-5** — add the four P1-grade indexes (`mission_flights.mission_id`, `flights.aircraft_id`, `customers.email`, `line_items.invoice_id`) via `CREATE INDEX IF NOT EXISTS`.
5. **P1-1** *(FAILOVER-SENSITIVE)* — add the `pg_is_in_recovery()` guard around startup DDL/seed/backfill (cheap, stops crash-loop-on-standby); then gate DDL to one container; Alembic as the structural follow-up.
6. **R-2 (R-#2 in reliability set)** — raise backend `mem_limit` to 1.5–2 GiB and/or cap `MultiPartParser.max_file_size` + stream-resize (pairs with Bill's in-flight image fix). *Runtime-only, ship anytime.*
7. **P2-1 + P2-2** *(integration)* — extract the one shared `_ingest_flight_log` helper, stream uploads, and decouple parse into a Celery job with a status poll.
8. **P2-4 + P2-5 + P3-4** — frontend: roll `useApiCache` across high-traffic pages, split Settings, add the Airspace visibility guard.
9. **P3-1, P3-2, P3-3, P3-5, P3-6** — health-gate trim, version the BOS override, start_period bump, executor offloads for backup-hash/email, scalar image count.

> **Documentation discipline:** each fix above should land as its own commit with a version
> bump (4 files per CLAUDE.md) and a CHANGELOG entry; the FAILOVER-SENSITIVE ones (P1-1, P3-2)
> warrant a new ADR each (next number: 0021). The index additions (P1-5) and the ingest-helper
> refactor (P2-1) also merit ADRs given their blast radius.
