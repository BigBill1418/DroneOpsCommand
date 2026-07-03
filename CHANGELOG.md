> **Maintained automatically by NOC doc-autogen.** This file is refreshed twice daily (04:00 + 16:00 UTC) by `~/noc-master/scripts/doc-autogen.py`, which summarizes recent commits via Claude Haiku 4.5 and commits with a `[skip-deploy]` trailer so no container rebuilds are triggered. See [NOC-Master ADR-0013](https://github.com/BigBill1418/NOC-Master-Control-SWARM/blob/main/docs/decisions/ADR-0013-docs-only-deploy-skip.md). Manual edits are preserved — the generator diffs against existing content before writing.

# Changelog

Notable changes to DroneOpsCommand. Dates are absolute (YYYY-MM-DD, UTC).

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

**Ground-truth verification** (authoritative `droneops-standby-db`, per-point
`gps_track`): of **570** ODL flights with tracks, **570 (100%)** have stored
`max_altitude` matching the actual track peak within 1 m; **0** where stored
exceeds the track; max absolute difference **0.4 m**. The 13 device-max-band
flights each carry hundreds of real GPS points at 499–500 m AGL — the drone
genuinely flew to its configured 500 m limit. ODL `max_altitude` is an accurate
achieved-peak AGL value and is now presented plainly.

* **`backend/app/routers/reports.py`.** Removed the `unverified_peak` flag
  computation + dict key + summary annotation string, and the now-unused
  `_ODL_DEVICE_MAX_LOW_M` / `_ODL_DEVICE_MAX_HIGH_M` constants.
* **`backend/app/services/ollama.py`.** Removed the matching "unverified
  device-reported maximum" clause from `SYSTEM_PROMPT_TEMPLATE` (imported by
  `claude_llm.py`, so both LLM providers are covered).
* **Tests.** Flipped the two ODL-peak assertions to verify the caveat is GONE;
  dropped the obsolete guard clean-text case. Full backend suite: 504 passed, 3
  skipped.
* **ADR-0029 NOT regressed.** The altitude-limit / 400 ft / Part-107 exceedance
  prohibition (prompt clause + `report_audience.py` runtime guard + tests) is
  fully intact. This removed only the data-quality caveat.

## 2026-06-30 — fix(reports): remove disproven "unverified peak" ODL altitude caveat — v2.76.3

ODL-imported flights at the ~500 m DJI device ceiling were tagged in client
reports as `" — unverified (device-reported maximum, not a measured peak)"`. That
caveat was a defensive residue from ADR-0028 H1, never validated. It is **false**
and is removed. See **ADR-0031**.

**Ground-truth verification** (authoritative `droneops-standby-db`, per-point
`gps_track`): of **570** ODL flights with tracks, **570 (100%)** have stored
`max_altitude` matching the actual track peak within 1 m; **0** where stored
exceeds the track; max absolute difference **0.4 m**. The 13 device-max-band
flights each carry hundreds of real GPS points at 499–500 m AGL — the drone
genuinely flew to its configured 500 m limit. ODL `max_altitude` is an accurate
achieved-peak AGL value and is now presented plainly.

* **`backend/app/routers/reports.py`.** Removed the `unverified_peak` flag
  computation + dict key + summary annotation string, and the now-unused
  `_ODL_DEVICE_MAX_LOW_M` / `_ODL_DEVICE_MAX_HIGH_M` constants.
* **`backend/app/services/ollama.py`.** Removed the matching "unverified
  device-reported maximum" clause from `SYSTEM_PROMPT_TEMPLATE` (imported by
  `claude_llm.py`, so both LLM providers are covered).
* **Tests.** Flipped the two ODL-peak assertions to verify the caveat is GONE;
  dropped the obsolete guard clean-text case. Full backend suite: 504 passed, 3
  skipped.
* **ADR-0029 NOT regressed.** The altitude-limit / 400 ft / Part-107 exceedance
  prohibition (prompt clause + `report_audience.py` runtime guard + tests) is
  fully intact. This removed only the data-quality caveat.

## 2026-06-29 — fix(reports): raise LLM output-token caps so full after-action reports complete (no more mid-sentence truncation) — v2.76.2

Reports were truncating mid-sentence in the client portal preview — every
report died at ~2,895 characters. Root cause: a 1024-token **output cap** on the
report-generation LLM calls. See **ADR-0030**.

The live BOS-HQ instance runs the Claude path; the worker log proved the cap was
the cause: `Claude report generated: 2895 chars, 3426 input tokens, 1024 output
tokens` — cut off exactly at the cap. The savannah report
(`e5f3aedf-…`) stored `final_content`==`llm_generated_content`==exactly 2,895
chars, ending mid-word ("…wide-establ").

* **Claude path (`backend/app/services/claude_llm.py`).** `max_tokens`
  1024 → **4096**.
* **Ollama path (`backend/app/services/ollama.py`).** `num_predict` (output)
  1024 → **4096**; `num_ctx` (window) 2048 → **8192**. The 2048 window was
  smaller than the input alone (~3.4k tokens), compounding the truncation. The
  deployed self-hosted model `llama3.1:8b-instruct-q4_K_M` has a 131,072 native
  context length, so 8192 is well within range (~1 GB KV cache; host has >20 GB
  free — verified safe).
* **No other truncation exists** (audited): `final_content` /
  `llm_generated_content` are unbounded `Text` columns; there is no `[:N]` slice,
  `.truncate()`, length limit, or frontend content line-clamp in the report path.
* **ADR-0029 preserved.** The altitude-limit / Part-107-exceedance prohibition
  (prompt + runtime guard) is intact; the regenerated full report re-scanned
  clean (zero exceed / 400 ft / Part 107 / altitude-limit matches).
* **Regression test** (`backend/tests/test_report_output_token_caps_adr0030.py`)
  asserts both output caps ≥ 4096 and Ollama `num_ctx` ≥ 8192 (> num_predict) so
  this cannot silently regress.

## 2026-06-29 — fix(reports): mission reports are client deliverables, not compliance audits — remove altitude-limit / Part-107 exceedance commentary — v2.76.1

Reverses the **H1** portion of ADR-0028. See **ADR-0029**. A client-facing
report must NEVER announce, flag, list, compute, or comment on whether any
flight exceeded an altitude limit, the 400 ft AGL ceiling, or any Part-107 /
regulatory limit. The v2.76.0 report engine emitted, in the Savannah Bananas
client report, "A number of flights operated above 400 ft AGL — specifically
Flights … — and as such exceeded the standard Part 107 400 ft AGL altitude
limit." That class of language is removed and prohibited.

* **Report builder (`backend/app/routers/reports.py`).** Removed the
  `PART_107_CEILING_M` (121.92 m) constant, the per-flight `over_400ft` flag,
  the `over_400ft` summary field, the `over_400ft_count` tally + its log line,
  and the `" — exceeds the 400 ft AGL Part 107 limit"` annotation. The
  `ceiling-limited` annotation was replaced with a neutral data-confidence note.
  Altitude is now presented as neutral capture data only (value/range, correct
  units — the v2.74.0 display fix is retained). The sole remaining caveat is a
  data-quality flag (`unverified_peak`) for ODL rows pinned at the device-
  reported ~500 m maximum, rendered "unverified (device-reported maximum, not a
  measured peak)" — no limit/ceiling/threshold/Part-107 reference.
* **LLM system prompt (`backend/app/services/ollama.py`, shared by the Claude
  path).** The "state the fact of exceedance" clause is replaced with an explicit
  prohibition: the model must not mention, compare against, or flag any altitude
  limit / 400 ft ceiling / Part-107 altitude rule; must not state or imply any
  exceedance; must not list flights by altitude. The single positive framing
  ("conducted in accordance with FAA Part 107 procedures") is preserved.
* **Runtime guard (`backend/app/services/report_audience.py`).** The existing
  post-generation audience-leak detector (wired into the editorial gate via
  `has_audience_leak`) now also flags altitude-limit / exceedance / Part-107-
  ceiling language as a deterministic second line of defense. Tuned to not flag
  neutral altitude data or the positive Part-107 framing. No schema change.
* **Tests.** ADR-0028 H1 exceedance tests replaced with absence-assertions;
  added detector tests for the verbatim Savannah sentence + limit phrasings, and
  passes for neutral altitude / positive-framing text. Full backend suite green
  (502 passed, 3 skipped).

## 2026-06-29 — fix(flight-data): GPS outlier gate, batch-import transaction safety, race-safe dedup, live-scalar reporting, Part-107 altitude truthfulness — v2.76.0

A full flight-data/parser/touchpoint integrity pass. See **ADR-0028**. Flight
parser bumped to **v1.1.0** (separate image). Three idempotent migrations
(0005–0007) sequence cleanly after 0004; all proven end-to-end against a real
Postgres (fresh + brownfield empty-diff, head `0007_strip_legacy_cache_track`,
idempotent re-run).

* **C1 — GPS teleport gate.** All three Rust parsers (`dji.rs`/`airdata.rs`/
  `litchi.rs`) now drop a haversine segment whose implied speed > 60 m/s (or, with
  no Δt, whose length > 500 m) via the shared `gate::segment_ok`, mirrored in
  `app/services/flight_metrics.py`. ODL ingest clamps physically-impossible
  passthrough distances. **Data repair:** the one catastrophic ODL row
  (`f57c9373`: 12,583,855 m → recomputed **3,604 m** from its own track, 1
  teleport dropped) is fixed by migration 0006, audit-trailed under
  `raw_metadata.distance_sanitized`.
* **C2 — batch upload transaction safety.** Each file in `/upload`,
  `/device-upload`, `/reprocess`, `/reprocess/all` runs in its own SAVEPOINT, so
  one `IntegrityError` no longer silently discards an entire "imported" batch.
* **H3/H4 — race-safe naming + dedup.** Partial unique index
  `uq_flights_source_file_hash` (migration 0005; zero existing duplicates) +
  catch-and-retry auto-naming; the builder never propagates `IntegrityError`.
* **H2 — reports read live `Flight` scalars** (heavy JSON deferred), cache only
  as legacy-ODL fallback.
* **H1 — Part-107 truthfulness.** Flights > 400 ft AGL (121.92 m) are flagged
  factually to the report + LLM prompt (no fabricated waiver claims); ODL ~500 m
  altitudes flagged ceiling-limited.
* **H5 — map coverage** measured at full resolution (matches the PDF report).
* **M1** strip heavy keys from native flight caches (migration 0007; ODL-legacy
  tracks preserved). **M2** cadence guard from `frame_count`, ceiling 60→25 Hz.
  **M4** per-row bulk-attach savepoints. **M5** stream stored file on reprocess
  (no OOM). **M6** minimal-viability gate. **M7** escape auto-name LIKE wildcards.
  **M8** ghosts excluded uniformly from totals + count. **M9** corrupt header
  bounded to wall-clock span. **L2/L4/L5/L6** correctness fixes.
* **Deferred (documented in ADR-0028):** M3 (multi-flight-per-file — parser emits
  one flight/file today), L1/L3/L7/L8/L9. **Operator action:** ODL
  `maxAltitude`/`totalDistance` semantics flagged for verification.

## 2026-06-29 — fix(migrations): shorten 0004 revision id to fit alembic_version(32) — v2.75.1

Hotfix making v2.75.0 deployable. The migration revision id
`0004_dji_duration_and_flight_name_restamp` (41 chars) exceeded
`alembic_version.version_num varchar(32)`. The migration body ran fine but the
final alembic stamp raised `value too long for type character varying(32)`,
rolling the whole transaction back on every startup → backend crash-loop. Renamed
to `0004_dji_duration_name_restamp` (30 chars). No behavioural change to the data
fix. Lesson recorded in the migration docstring: keep revision ids ≤ 32 chars.

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
  old hermetic harness hid (`test_task_reads_sha`).
