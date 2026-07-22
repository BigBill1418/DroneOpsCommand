> **Maintained automatically by NOC doc-autogen.** This file is refreshed twice daily (04:00 + 16:00 UTC) by `~/noc-master/scripts/doc-autogen.py`, which summarizes recent commits via Claude Haiku 4.5 and commits with a `[skip-deploy]` trailer so no container rebuilds are triggered. See [NOC-Master ADR-0013](https://github.com/BigBill1418/NOC-Master-Control-SWARM/blob/main/docs/decisions/ADR-0013-docs-only-deploy-skip.md). Manual edits are preserved — the generator diffs against existing content before writing.

# Changelog

Notable changes to DroneOpsCommand. Dates are absolute (YYYY-MM-DD, UTC).

## 2026-07-22 — ops(backups): automated quarterly R2 restore drill [skip-deploy]

Ops-script only (no app/version change). Closes the last discipline gap in the
backup lane: the quarterly restore drill documented in `scripts/snapshot.sh`
was manual — it relied on an operator remembering every ~92 days.

* **`scripts/restore-drill.sh`** — downloads the *newest R2 dump* (the off-host
  copy that matters in a disaster, not the local file), verifies gzip integrity
  and that the dump is <48 h old, restores it into a throwaway
  `droneops_restore_drill` database on `droneops-standby-db`, sanity-checks
  restored row counts (`flights` ≥90 % of live, `battery_logs` /
  `tos_acceptances` non-empty), then drops the scratch DB (trap-guaranteed).
* **`scripts/systemd/droneops-restore-drill.{service,timer}`** — installed on
  BOS-HQ at `/etc/systemd/system/`, `OnCalendar=*-01,04,07,10-16 16:23 UTC`
  (quarterly on the 16th, anchored to the 2026-07-16 install-time verified
  restore), `Persistent=true` so a powered-off host catches up.
* **Self-watching:** full success writes node-exporter textfile metric
  `droneops_restore_drill_last_success_timestamp_seconds`; an InfraWatch
  Grafana rule pages `infrawatch-alerts` if the drill has not succeeded in
  >100 days or the metric is absent. Failure fires an ntfy `high`
  (dedup `droneops-restore-drill`, 6 h cooldown); success posts one
  `default`-priority note (4×/year, ADR-0037 digest class).
* **Proved live 2026-07-22:** restored `droneops/db/2026/07/22/…sql.gz` from
  R2, verified flights=760/760, battery_logs=759, tos_acceptances=10; scratch
  DB dropped; metric stamped.


## 2026-07-19 — ops(standby): silence chronic healthcheck FATAL spam on droneops-db-standby [skip-deploy]

Compose-only (no app/version change). The standby's healthcheck ran
`pg_isready -U replicator`; dbname defaults to the username and no
`replicator` database exists, so PostgreSQL logged
`FATAL: database "replicator" does not exist` every 10 s (~8.5k lines/day)
while the check still passed. Healthcheck now probes `-U droneops -d droneops`
(the app role+db, present on the standby via replication). Applied live on
CHAD-HQ (10.99.0.2) by recreating `droneops-db-standby`.

## 2026-07-16 — ops(backups): off-host R2 push + fix broken tos_signed path + freshness metric [skip-deploy]

Ops-script only (no app/version change). The 2026-07-16 BOS backup audit found
the nightly `scripts/snapshot.sh` dump was **local-only** (no off-host copy)
and its signed-TOS step was silently no-op'ing every night — it tarred
`${REPO_ROOT}/data/tos_signed`, a path that never existed. The real signed
legal PDFs live in the `droneops_app_data` Docker volume at `uploads/tos_signed`
(alongside `uploads/flight_logs`, ~557M total).

* **Off-host DB push.** After the local gzipped `pg_dump` (unchanged, 14-day
  local retention), the dump is streamed to Cloudflare R2 at
  `s3://<obs bucket>/droneops/db/YYYY/MM/DD/droneops-<TS>.sql.gz`, reusing the
  obs R2 credential source (`/opt/observability/.env`) and the shared
  `obs-glitchtip-backups` bucket with a dedicated `droneops/` prefix.
* **Fixed + expanded uploads coverage.** Replaced the broken `data/tos_signed`
  tar with an incremental `aws s3 sync` of the volume's `uploads/` tree
  (signed-TOS PDFs + flight logs) to `s3://<obs bucket>/droneops/uploads/`,
  mounting the volume read-only.
* **Freshness metric + alerting.** On FULL success writes
  `droneops_backup_last_success_timestamp_seconds` to the node-exporter
  textfile collector; InfraWatch `obs-rule-droneops-backup-stale` pages on
  >28h/never-written. Any dump/upload failure pushes ntfy `high` to the
  existing `infrawatch-alerts` topic. Removed the silent `|| true` swallow.

## 2026-07-06 — fix(payments): delivery verification pass — two real bugs + e2e endpoint tests — v2.80.1 (ADR-0040 addendum)

End-to-end verification of the v2.80.0 automation caught two bugs before any
prod payment exercised them (full detail in the ADR-0040 addendum):

* **`Mission.invoice` lazy="noload" identity-map trap.** Every trigger path
  loads the mission before the delivery service runs, so the service's
  `selectinload(Mission.invoice)` re-query returned the identity-mapped
  mission WITHOUT repopulating the relationship — the gate read
  `invoice=None` and skipped `not-paid-in-full` on PAID missions. The Stripe
  webhook and mission-update triggers were silently dead. Fix: the service
  queries the Invoice table directly; `_delivery_skip_reason(mission,
  invoice)` takes it explicitly.
* **SMTP-unconfigured no-op was stamped as sent.** `_send_html_email`
  returns False when SMTP isn't configured; the stamp was written anyway,
  permanently losing the delivery. Fix: stamp only on a True send; the False
  path returns `skipped:smtp-unconfigured` (WARN) and stays armed.
* New `test_download_link_delivery_e2e.py`: 9 endpoint-level tests driving
  the REAL `update_invoice` / `update_mission` / `get_client_mission`
  functions against sqlite (house pattern; includes a JSONB→JSON sqlite
  shim for the reports table). These are the tests that caught bug #1.
* **Report-editor override could be silently lost (independent review
  finding).** Generate Report / Generate PDF re-baselined the unsaved
  `paymentOverride` switch without persisting it — the dirty-guard went
  quiet and the server kept override=false, so the operator believed the
  link was released while PDF + email withheld it. Fix: the pre-PDF PUT now
  persists `include_download_link` + `download_link_payment_override` (the
  PDF renders against what the operator sees), and the generate paths
  preserve the previous baseline so an unsaved flip stays dirty.
* Bypass sweep confirmed: portal + report PDF/email are the only
  client-reachable `download_link_url` surfaces, all gated; docs updated
  (README feature sections, PROGRESS, ADR-0040 addendum).
* Suites: 607 backend / 53 frontend pass; tsc clean.

## 2026-07-06 — feat(payments): automated download-link delivery on payment-in-full — v2.80.0 (ADR-0040)

Completes ADR-0039: payment-in-full is now a TRIGGER, not just a gate. Per
Bill (2026-07-06): no manual report regeneration — when the client pays they
get the link in a separate automated follow-up email and it populates in the
client portal.

* **Delivery service** `app/services/download_link_delivery.py` — also now
  owns the ADR-0039 gate policy (reports router + portal import it; one
  source of truth). Sends branded `download_link_email.html`, stamps
  `missions.download_link_email_sent_at` (migration
  `0009_mission_dl_email_sent_at`) AFTER a successful send so failures retry
  on the next trigger. Fail-soft: never breaks the payment flow.
* **Three triggers:** Stripe balance-paid webhook; manual mark-paid
  (`PUT /invoice` false→true transition); download URL set/changed on the
  mission (covers footage-ready-after-payment; a URL change RESETS the dedup
  stamp so replacement links re-deliver).
* **Client portal:** `GET /api/client/missions/{id}` returns
  `download_url`/`download_expires_at` only when the gate passes; the
  DELIVERABLES card shows the download button when unlocked ("unlocks when
  the invoice is paid in full" while unpaid), and the post-payment poll
  re-pulls the mission so the link appears without a reload. Gotcha honored:
  `Mission.invoice` is lazy="noload" — endpoint eager-loads it explicitly.
* Skip conditions logged with reason: no-url / already-sent / not-billable /
  not-paid-in-full / link-expired (WARN) / no-customer-email (WARN).
* Tests: 16 new (`test_download_link_delivery.py`); portal fixture +
  migration fence updated; 598 backend / 53 frontend pass.

## 2026-07-05 — feat(reports): unpaid-invoice download-link gate + operator override — v2.79.0 (ADR-0039)

Policy (Bill, 2026-07-05): **clients do not get the mission-footage download
link until the invoice is paid in full.** Trigger: the 2026-07-02 River M.
report went out with the footage link while BARNARDHQ-2026-0005 ($400.50) was
unpaid — nothing in the code checked payment.

* **Server-side gate at a single choke point.** Both exposure paths (report
  PDF render + report email) now build the link only via
  `_build_download_link()` → `_download_link_payment_blocked()`
  (`backend/app/routers/reports.py`). Withholds while a billable mission's
  invoice is unpaid; **fail-closed** when billable-but-never-invoiced; $0
  invoices and non-billable missions pass. Deposit alone does NOT release —
  only `paid_in_full`.
* **Per-report operator override** `reports.download_link_payment_override`
  (migration `0008_report_dl_payment_override`, additive, default false).
  Settable only via `PUT /report` (never the generate path, so regeneration
  can't reset it); every flip is audit-logged with the acting user.
* **Editor surface** (`MissionReportEdit.tsx`): yellow "link withheld —
  invoice not paid in full" alert + orange override switch when the link is
  requested and payment is outstanding; the Sent toast says explicitly when
  the link was withheld (`download_link_withheld` on the send response).
  `GET/PUT /report` return computed `download_link_payment_blocked`.
* **Withholding never blocks the report itself** — the client still gets the
  report; only the footage link is held.
* **Residual:** a PDF rendered pre-gate carries the baked-in link; send
  warn-logs this and River's stale `pdf_path` was invalidated in prod. See
  ADR-0039 for the full policy + alternatives.
* Tests: 14 new gate tests (`test_report_download_link_payment_gate.py`);
  migration-fence + ADR-0038 fixtures updated; 582 backend / 53 frontend pass.

## 2026-07-03 — feat(reports): client-report narrative quality levers — v2.77.0 (ADR-0035)

Guard-safe quality pass on the shared report system prompt
(`SYSTEM_PROMPT_TEMPLATE` in `backend/app/services/ollama.py`, inherited by the
Claude path via `claude_llm.py`). Implements the top three levers of
**docs/plans/2026-07-03-report-quality.md** (`FU-AI-QUALITY-PASS`):

* **Kill hedging (§3.1).** Requires definitive, active-voice authority; forbids
  "appeared to" / "seemed" / "was observed to" / "it is likely" softeners unless
  the data is genuinely uncertain.
* **Anti-bloat budget (§3.2).** Each section is 2–5 sentences of substance — no
  padding, no restating the heading, no generic boilerplate; brevity on a routine
  flight is professional, not a defect.
* **Number-grounding (§3.3).** Grounds every claim in the provided figures
  (flight count / total time / distance / aircraft with units; area acreage); no
  vague quantities when an exact number exists.
* **Guard integrity (ADR-0029).** The number-grounding lever carries an explicit
  altitude carve-out — number-grounding does NOT extend to altitude, which stays
  neutral capture data; ranking/singling-out/tallying flights by altitude remains
  forbidden. The runtime detector `report_audience.py` is unchanged; new tests in
  `test_report_audience_guard.py::TestNarrativeQualityLevers` lock the levers and
  prove a report containing a 146.3 m AGL (480 ft) flight stays guard-clean. Full
  ADR-0029 / audience-leak suites pass unchanged (52 passed).
* **Caps unchanged** (ADR-0030). `.deployer-disabled` repo — hand-deploy on
  BOS-HQ; verify the public OpenAPI version (2.77.0), not `deployer-state.json`.

## 2026-07-03 — Advisory-lock the Alembic migration boot path (ADR-0036, Phase 1)

Migration-consolidation hardening, Phase 1 of
**docs/plans/2026-07-03-migration-consolidation.md**. Decision + rationale in
**docs/adr/0036-migration-single-path-hardening.md**.

* **Advisory lock on the migration run.** `run_migrations_sync()`
  (`backend/app/db_migrations.py`) now wraps its entire detect + stamp +
  upgrade critical section in a **session-level Postgres advisory lock**
  (`_MIGRATION_LOCK_ID = 8675310`) taken on a dedicated AUTOCOMMIT connection
  and released in a `finally`. Previously the migration path relied only on
  transaction atomicity + the `--workers 1` / single-replica assumptions — two
  backends booting concurrently (multi-worker, multi-replica, or a blue-green
  pair briefly pointing two backends at the same writable primary) could both
  enter `command.upgrade` and deadlock on a revision's DELETEs (0003) or
  double-apply DDL. The lock is **blocking** (`pg_advisory_lock`, not `try_`):
  a losing racer WAITS for the winner, then re-detects `current == head` and
  no-ops — it never skips the lock and proceeds against an un-migrated schema.
  Lock id is DISTINCT from `seed.py`'s `_SEED_LOCK_ID` (8675309) so migrating
  and seeding don't needlessly serialize against each other. Mirrors the
  posture the seed path already had. The ADR-0021 `pg_is_in_recovery()`
  primary-only guard is untouched.
* **Revision-id length invariant fence.** A hermetic test asserts every
  Alembic revision id is ≤ 32 chars (the `alembic_version.version_num`
  `VARCHAR(32)` that caused the v2.75.1 crash-loop when revision `0004` was
  41 chars, ran its DDL, then rolled back the stamp on every boot). CI now
  fails before such a revision can ship.
* **Tests.** `backend/tests/test_db_migrations.py` gains lock-envelope
  coverage: acquire-before-upgrade / release-after ordering, brownfield
  stamp+upgrade under lock, the no-op fast path still acquires+releases, the
  lock is released even when `command.upgrade` raises, and the lock id is
  distinct from the seed lock. Suite: 25 passed, 2 skipped (opt-in real-PG
  integration via `DOC_TEST_PG_URL`).
* **Scope.** Phase 1 only. `_add_missing_columns` / `_create_hot_indexes` and
  the legacy helpers in `main.py` are intentionally NOT removed here — the plan
  defers helper-severance (Phase 3) and the model-vs-head CI sync gate
  (Phase 2) to later, lower-urgency passes.
* **Deploy.** This repo is `.deployer-disabled` — the NOC deployer pulls git
  but does not rebuild. Ship via a hand-deploy on BOS-HQ
  (`docker compose build backend worker beat && up -d --no-deps …`); verify
  container build time, not `deployer-state.json`.

## 2026-07-03 — feat(missions): airspace / LAANC awareness at mission creation (ADR-0037)

Airspace/weather data was dashboard-only and not tied to mission creation.
Operators now get an **operator-facing pre-flight airspace check** at
scheduling time. Full design in **docs/adr/0037-airspace-laanc-awareness-at-mission-creation.md**.

* **New service `backend/app/services/airspace.py`.** `fetch_airspace_class()`
  point-in-polygon queries the FAA public Class Airspace ArcGIS FeatureServer
  (free, no key) — no intersecting polygon ⇒ uncontrolled Class G.
  `derive_laanc_requirement()` is tri-state: `True` for controlled B/C/D/
  E-surface, `False` for G, **`None` when undetermined** (never fabricate a
  safe-looking default from missing data). `assemble_preflight()` reuses the
  existing weather-router TFR/METAR/Open-Meteo fetchers and emits neutral
  advisories. `extract_latlon()` derives a coordinate from a mission's
  free-form `area_coordinates` (flat/aliases, `center`, GeoJSON Point/Polygon).
* **New endpoints (`backend/app/routers/missions.py`).**
  `GET /api/missions/airspace-preflight?lat=&lon=&airport=` (primary) and
  `GET /api/missions/{mission_id}/preflight`. Returns `{airspace_class,
  laanc_likely_required, controlling_facility, tfrs, weather, advisories,
  degraded, disclaimer}`. Static preflight route is declared before
  `/{mission_id}` so the path isn't captured as a mission id.
* **Computed on demand, never persisted.** TFRs/weather are time-varying; a
  create-time snapshot would be stale by flight day. No schema change, no
  migration → failover-safe. The `create_mission` write path is unchanged.
* **Graceful degradation.** All feeds gathered with `return_exceptions=True`;
  any feed failing (or raising) yields partial data + `degraded: true` +
  advisory — **never a 500**. Undetermined airspace ⇒ `laanc_likely_required:
  null`.
* **Operator-facing ONLY — never in the client report (ADR-0029 boundary).**
  Preflight is never persisted on the mission, never passed to any report
  builder, and renders no compliance verdict. Guarded by
  `tests/test_report_never_references_airspace.py` (fails if any report module
  or the mission schema references airspace/laanc/preflight/tfr) and by a unit
  test asserting no advisory contains "violation/illegal/non-compliant".
* **Tests.** +44 (`tests/services/test_airspace_service.py`,
  `tests/test_missions_airspace_preflight.py`,
  `tests/test_report_never_references_airspace.py`). Suite 507 → 551 passing,
  0 regressions.

## 2026-07-03 — fix(reports): resolve the aircraft from the live flight, not the stale junction copy (ADR-0038) — v2.78.0

Phase 1 of the flight-attach unification (plan:
`docs/plans/2026-07-03-flight-attach-unification.md`) — the **root fix** for the
ADR-0033 junction-staleness class.

* **The bug.** The `MissionFlight` junction copied `Flight.aircraft_id` at attach
  time. When a fleet serial was registered **later**, the live flight updated but
  the junction copy stayed stale — the Avata 2 mechanism. ADR-0033 made the
  report *tolerant* of a NULL copy (fell back to parsed `drone_model`), but never
  read the live fleet record, so a late-linked flight showed the bare string
  `"Avata2"` instead of the canonical `"DJI Avata 2"` card (name/image/specs).
* **Read convergence** (`backend/app/routers/reports.py`). `_load_live_flight_metrics`
  now LEFT-JOINs the fleet `Aircraft` (scalar columns only — the heavy Flight
  JSON is still never loaded, ADR-0025/0019). `_aircraft_label` and the new
  `_build_aircraft_cards` (extracted from the PDF path) resolve **native** flights
  from the live `Flight.aircraft`; **legacy-ODL** (`flight_id IS NULL`) rows keep
  the junction/cache read (Phase 2 materializes them). One resolver drives both
  the narrative label and the PDF "Aircraft used" card.
* **Write change** (`backend/app/routers/missions.py`). The single-add and bulk
  native attach paths no longer copy `aircraft_id` onto the junction (set NULL —
  derived on read; client-sent values still ignored, ADR-0007). The column is
  **retained** (drop is Phase 4).
* **Behaviour.** Preserving for reports **except** the fix: a native flight linked
  after attach now shows the correct fleet aircraft with no detach/re-attach.
* **Defers (per plan):** legacy-ODL materialization (Phase 2); metrics/track
  live-only flip + zero-cache-read counter (Phase 3); column drops + `flight_id`
  NOT NULL (Phase 4). ADR-0007 matcher and ADR-0029 audience guard untouched.
* **Tests.** New `backend/tests/test_report_live_aircraft_adr0038.py`: real-DB
  late-link root-fix proof (junction stays NULL, live resolves), legacy-ODL
  no-regression, unlinked-native `drone_model` fallback, stale-copy-ignored.
  Fail-before/pass-after confirmed (`"Avata2"` → `"DJI Avata 2"`). Full backend
  suite green (511 passed, 3 skipped). Existing attach-derives-aircraft tests
  updated to the new "junction not copied" contract.
* **Deploy.** `.deployer-disabled` — manual BOS-HQ rebuild
  (`docker compose build backend worker beat flight-parser && up -d --no-deps …`);
  verify the public `openapi.json` version (`2.78.0`), not `deployer-state.json`.

## 2026-07-03 — Avata 2 report incident: data remediation + prod deploy (ADR-0033)

Follows the code fix below. Full incident write-up + audit trail in
**docs/adr/0033-avata2-missing-from-report-incident.md**.

* **Data remediation (prod).** Root cause was a blank `serial_number` on the
  fleet `DJI Avata 2` record, so ADR-0007's strict serial-first matcher left
  every recent Avata flight unlinked. Registered serial `1581F6W8A242N0A3` and
  backfilled: aircraft 1 row, `flights` 12 rows (unlinked 12 → 0),
  `mission_flights` 4 rows. The "Springfield Drifters Promo" mission now resolves
  `DJI Avata 2 ×2 + DJI Mini 5 Pro ×1`; regenerating the report renders the Avata
  correctly. **Standing rule:** register a drone's serial when adding it to the
  fleet, or its flights stay unlinked under ADR-0007.
* **Deploy.** This repo is `.deployer-disabled` — the NOC deployer pulls git but
  does not rebuild. The reports fix + the ADR-0032 parser fix were hand-deployed
  on BOS-HQ (`docker compose build backend worker beat flight-parser && up -d
  --no-deps …`). Verify container build time, not `deployer-state.json`, to
  confirm a DOC deploy is actually running.

## 2026-07-03 — fix(reports): attached flight with unrecognized aircraft no longer missing from report

An attached flight whose fleet aircraft was unrecognized (`flights.aircraft_id`
NULL) was dropped from — or genericized to "Unknown" in — the client report.

* **Field defect.** The 2026-07-02 "Springfield Drifters Promo" mission had a DJI
  Avata 2 flight attached (native `flight_id`), but the generated report omitted
  it. Root cause: the Avata flight carried a `drone_serial` that the fleet
  "DJI Avata 2" aircraft record lacked (its `serial_number` is blank), so the
  strict serial-match path (ADR-0007) refused a model fallback and left
  `aircraft_id` NULL. `backend/app/routers/reports.py` then read ONLY
  `MissionFlight.aircraft` and substituted the literal "Unknown" — discarding the
  flight's own parsed `drone_model` ("Avata2"). The flight was attached the whole
  time; the report layer was not robust to an unlinked aircraft.
* **Fix (report layer, defense-in-depth).** New `_aircraft_label()` resolves the
  aircraft display name with a fallback chain: linked fleet `model_name` → live
  `Flight.drone_name`/`drone_model` → cache `drone_name`/`drone_model`/`aircraft`
  → "Unknown" only as a true last resort. `_build_flight_summaries` (the LLM
  aggregation) and the PDF "Aircraft used" section both use it, so an attached
  flight is never silently dropped from either surface. `_load_live_flight_metrics`
  now also selects `drone_model`/`drone_name` (scalar columns; heavy JSON still
  never loaded, ADR-0019). Regression test:
  `backend/tests/test_report_unrecognized_aircraft_label.py`.
* **No compliance logic touched** — the ADR-0029 altitude/Part-107 exceedance
  prohibition stays intact.
* **Operator residual (data, not code).** To restore canonical fleet attribution
  (and the aircraft image/specs card), add serial `1581F6W8A242N0A3` to the
  "DJI Avata 2" fleet aircraft record, then POST `/api/flights/backfill-aircraft`.
  Until then the report labels the flight "Avata2" from the parsed model.

## 2026-07-02 — fix(flight-parser): correct DJI voltage, Litchi/Airdata speed units, Airdata altitude selection

Three confirmed flight-log parser correctness bugs that put wrong numbers into
client-facing report data. All fixed with new per-format tests (the Litchi and
Airdata parsers previously had zero test coverage). Candidate for a new ADR
(parser unit-correctness; number TBD by the operator).

* **`flight-parser/src/dji.rs` — DJI battery voltage was 1000× too small.** The
  frame loop did `battery.voltage as f64 / 1000.0`, but `dji-log-parser` 0.5.7
  already returns `FrameBattery.voltage` in **volts** (its `SmartBattery` and
  `CenterBattery` record parsers map the raw `u16` with `/1000.0` — confirmed in
  the crate's `src/record/smart_battery.rs` and `src/record/center_battery.rs`).
  The extra divide turned a 15.2 V pack into 0.0152 V and disagreed with the
  Airdata parser (which stores volts raw). Extracted a tested
  `frame_battery_voltage()` normaliser that passes volts through unchanged.

* **`flight-parser/src/litchi.rs` — Litchi speed was stored without unit
  conversion.** Litchi CSVs export `speed(mph)` (some km/h); the value was
  summed into `max_speed` and every track speed with no conversion, inflating
  speed by ~2.237× (mph) / ~3.6× (km/h). Now detects the unit from the matched
  header and normalises to m/s, mirroring the Airdata parser. Also fixed the
  time-column selection: the old `contains("time")` could bind the numeric
  epoch-ms `timestamp` column instead of `datetime(utc)`, collapsing duration to
  the point-count fallback — now prefers an explicit `datetime` column and never
  binds `timestamp`.

* **`flight-parser/src/airdata.rs` — metric Airdata speed + altitude
  selection.** Added a km/h → m/s branch (metric exports were treated as m/s,
  ~3.6× inflated). Lowercased the dead `altitude_above_seaLevel(feet)` candidate
  (its capital `L` never matched a lowercased header) and demoted sea-level (MSL)
  below the AGL / relative-altitude candidates, adding an explicit
  `height_above_takeoff(m)` entry — so a metric export exposing both
  `height_above_takeoff(m)` and `altitude_above_sealevel(m)` now reports the AGL
  value for `max_altitude`, not the (much larger) MSL value.

No altitude/Part-107 exceedance flagging was added — these are unit-correctness
fixes only (ADR-0029: reports are client deliverables, not compliance audits).

Verification: `cargo test` in a `rust:1-slim` container — 20/20 pass (14
pre-existing + 6 new); clean `cargo build`, zero warnings.

## 2026-07-01 — fix(reports): remove disproven "unverified peak" ODL altitude caveat — v2.76.3

ODL-imported flights at the ~500 m DJI device ceiling were tagged in client
reports as `" — unverified (device-reported maximum, not a measured peak)"`. That
caveat was a defensive residue from ADR-0028 H1, never validated. It is **false**
and is removed. See **ADR-0031**.

**Ground-truth verification** (author bill-bg, 2026-07-01): device-ceiling
flights' max-altitude readings are self-consistent on a per-aircraft basis and
agree with post-flight telemetry reviews. The warning was a false overprotection.
Removed the caveat from the report narrative template; the underlying metric
(max altitude in feet/meters from the parsed flight log) stands.
