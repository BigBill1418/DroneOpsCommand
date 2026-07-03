# Plan: MissionFlight Flight-Attach Unification (retire the triple-path junction)

- **Date:** 2026-07-03
- **Status:** Proposed (phased; each phase independently shippable + reversible)
- **Owner:** engineering (DroneOpsCommand)
- **Related:** ADR-0007 (serial-first fleet match), ADR-0025 (heavy-column
  defer / OOM family), ADR-0026 (duplicate-attach dedup), ADR-0028 (live-scalar
  reporting, H2), ADR-0033 (Avata junction-staleness incident, 2026-07-03).
- **Companion doc:** `2026-07-03-report-quality.md` (report narrative quality —
  separate concern, referenced in §7).

---

## 1. Problem statement

`MissionFlight` (the mission↔flight junction, `backend/app/models/mission.py:109`)
carries **three parallel representations** of the attached flight, and different
readers trust different ones:

| Field | Type | Meaning | Failure mode |
|---|---|---|---|
| `flight_id` | FK → `flights.id` (nullable) | **native** flight (the canonical future) | — |
| `opendronelog_flight_id` | `String(255)` (nullable) | **legacy** ODL flight — **no ORM model exists**; data lives only in the cache | orphan reference; no row to re-read |
| `aircraft_id` | FK → `aircraft.id` (nullable) | aircraft **copied from `Flight.aircraft_id` at attach time** | **goes stale** — the ADR-0033 Avata bug |
| `flight_data_cache` | `JSON` (nullable) | attach-time **scalar snapshot** of the flight | drifts vs live `Flight`; historically also held the GPS track → OOM (ADR-0025) |

This junction is the documented root of the 2026-06 OOM/inflation incident
family (ADR-0025/0026: the cache duplicated the GPS track in every list row and
duplicate attaches double-counted totals) **and** the 2026-07-03 Avata
staleness (ADR-0033: the copied `aircraft_id` went NULL/stale versus the live
flight).

**The good news — most of the convergence already happened defensively.** Reads
have already been migrated to prefer live `Flight` scalars:

- `_resolve_flight_metrics(mf, live)` (`reports.py:173-196`) reads **live
  `Flight` scalars** when `flight_id` is set and falls back to the cache **only**
  for legacy-ODL rows (ADR-0028 H2).
- `_aircraft_label(mf, live)` (`reports.py:199-231`, ADR-0033) resolves aircraft
  via a fallback chain: linked fleet `model_name` → live `Flight.drone_name`/
  `drone_model` → cache → `"Unknown"` — it no longer trusts only the junction.
- The GPS track was removed from the native-flight cache (ADR-0025, migration
  `0007_strip_legacy_cache_track`); `_scalar_cache_from_flight` writes scalars
  only.

So the cache and the copied `aircraft_id` are **already vestigial for native
flights** — they are read only as a fallback. What remains is to make that de
facto reality *de jure*: converge every read onto the live flight, materialize
the one class that has no live row (legacy-ODL), and then delete the redundant
columns. That is a de-risking exercise, not a rewrite.

---

## 2. Current-state read map (who reads what — verified)

### 2.1 Reads of `flight_data_cache`
| Site | File:line | Reads | Native still needs it? |
|---|---|---|---|
| metrics fallback | `reports.py:186-191` | duration/distance/alt/source/notes | **No** — native uses live scalars; cache only for `flight_id IS NULL` |
| aircraft label fallback | `reports.py:226-230` | drone_name/model/aircraft | No — fallback only |
| PDF unrecognized-aircraft card | `reports.py:534-538` | drone_name/model/aircraft | No — fallback only |
| mission serialize (strip heavy) | `missions.py:117` | strips track/telemetry before response | Defensive (legacy rows) |
| ODL attach fleet-match | `missions.py:601` | drone_serial/drone_model | **Yes — legacy-ODL only** |
| ODL attach track enrich | `missions.py:618-629` | writes `cache["track"]` from ODL svc | **Yes — legacy-ODL only** |
| map render | `map_renderer.py:54` | `cache["track"]` | **Yes — legacy-ODL only** |
| mission tracks | `mission_tracks.py:74,90,128,200` | `cache["track"]` | **Yes — legacy-ODL only** |

**Writes** (`_scalar_cache_from_flight`, `missions.py:75-101,594,760`) snapshot
scalars from the live flight at attach — pure denormalization, no unique data.

**Conclusion:** the *only* information that lives **exclusively** in
`flight_data_cache` (i.e. has no live `Flight` backing row) is the **legacy-ODL
class**: rows with `flight_id IS NULL AND opendronelog_flight_id IS NOT NULL AND
source = "opendronelog_import"`. Retiring the cache is gated entirely on
migrating those rows.

### 2.2 Branch points on `flight_id` vs `opendronelog_flight_id`
- `_flight_key(f)` (`reports.py:55-60`) — identity/dedup key: `("fid", flight_id)`
  → `("odl", opendronelog_flight_id)` → `("id", junction_id)`.
- `_resolve_flight_metrics` (`reports.py:173-196`) — native→live, else→cache.
- attach `add_flight` (`missions.py:584-636`) — native branch derives
  `aircraft_id` from `Flight`; ODL branch fleet-matches from cache + fetches
  track from the ODL service.

### 2.3 Aircraft derivation (the ADR-0033 staleness locus)
At attach, native path copies `data.aircraft_id = local_flight.aircraft_id`
(`missions.py:588`) into the junction. If the fleet serial is registered *later*
(the ADR-0033 remediation), `flights.aircraft_id` updates but the junction copy
does **not** — stale. Reports already defend against this via `_aircraft_label`;
the PDF aircraft-card path (`reports.py:501-548`) still consults `f.aircraft`
(the copied FK) first, then falls back. **The copied `aircraft_id` has no reason
to exist for native flights** — the live flight already knows its aircraft.

---

## 3. Target model

**One canonical attach path. The junction stores a *reference*, never a *copy*.**

```
MissionFlight (target)
  id                     PK
  mission_id             FK missions.id
  flight_id              FK flights.id  (NOT NULL in steady state)
  added_at               timestamp
  -- retired: opendronelog_flight_id, aircraft_id, flight_data_cache
```

- **Aircraft** is always read from `flight.aircraft` (live) via the
  `_aircraft_label` chain. No copy on the junction → cannot go stale.
- **Metrics** are always read from live `Flight` scalars. No cache → cannot drift.
- **Legacy-ODL flights become real `Flight` rows** (materialized, `source =
  "opendronelog_import"`), so every attached flight has a `flight_id` and the two
  legacy fields disappear.
- **GPS track** for legacy flights moves onto `Flight.gps_track` during
  materialization; map/track readers already read `Flight.gps_track` for native
  flights, so they converge on one source.

This is exactly the ROADMAP "remaining item #1" (lean list schema — the cache
duplicating the track is the O(track) list-payload cost) resolved at the root.

---

## 4. Phased migration (dual-write / backfill, never breaks live reports)

Each phase is a separate PR with its own version bump (per repo CLAUDE.md) and
is independently reversible. **Deploy reality:** this repo is
`.deployer-disabled` — the NOC deployer pulls git but does **not** rebuild.
Every phase requires a **manual rebuild on BOS-HQ** (`docker compose build
backend worker beat flight-parser && up -d --no-deps …`) and verification of
container build time, **not** deployer status (ADR-0033 deploy note). Migrations
run at backend startup on the writable primary only (ADR-0021 recovery guard).

### Phase 0 — Instrument & freeze (no schema change)
- Add a one-shot startup **census log**: count rows by class
  (`native = flight_id NOT NULL`, `odl = flight_id NULL AND
  opendronelog_flight_id NOT NULL`, `orphan = both NULL`). This tells us the
  real size of the legacy-ODL problem before touching it.
- Add a **write-side guard test** asserting `_scalar_cache_from_flight` output
  is a pure function of the live flight (no field originates only in the cache
  for native flights). Locks the invariant the rest of the plan depends on.
- **Rollback:** none needed (observability only).
- **Gate to Phase 1:** census shows the legacy-ODL count is bounded and known.

### Phase 1 — Converge the aircraft read; stop copying `aircraft_id`
- Change the PDF aircraft-card path (`reports.py:501-548`) to resolve aircraft
  through the **same `_aircraft_label` + live-flight** chain the narrative uses,
  instead of trusting `f.aircraft` (the copied FK) first. Single source for both
  outputs.
- Stop **writing** `data.aircraft_id` onto the junction at native attach
  (`missions.py:588`) — leave the column populated for now (read-compat) but
  make it derived-on-read. Add a test: registering a fleet serial *after* attach
  makes a regenerated report show the corrected aircraft **without** a
  detach/re-attach (the exact ADR-0033 scenario).
- **Rollback:** revert the PR; the copied column is still present and populated,
  so old read paths still work.
- **Gate to Phase 2:** report + PDF render aircraft identically from the live
  flight across the test matrix (native linked, native unlinked, legacy-ODL).

### Phase 2 — Materialize legacy-ODL flights into native `Flight` rows
- One-shot **backfill migration** (Alembic revision, per the migration-
  consolidation plan): for each `odl` junction row, create a `Flight` row from
  its `flight_data_cache` (scalars + `track` → `gps_track`), `source =
  "opendronelog_import"`, `source_file_hash` derived from the ODL id for
  idempotency; then set `mission_flights.flight_id` to the new flight and run the
  existing fleet-match to populate `flights.aircraft_id`.
- **Dual-write window:** during this phase the ODL attach branch
  (`missions.py:597-636`) also creates a `Flight` row immediately (so no *new*
  cache-only rows are created while the backfill drains the old ones).
- Idempotent + resumable (ADR-0026 dedup unique index on
  `(mission_id, flight_id)` protects against double-materialization; the ODL
  unique index still protects the source).
- **Rollback:** the new `Flight` rows are additive; `opendronelog_flight_id` and
  the cache are untouched, so reverting the reader changes restores the old path
  exactly. Do **not** drop anything in this phase.
- **Gate to Phase 3:** census shows `odl`-class count = 0 and `orphan` = 0; a
  full report-regeneration diff over a sample of historically-imported missions
  shows byte-identical (or explainably-better) metrics.

### Phase 3 — Flip reads to native-only; quarantine the cache
- Change `_resolve_flight_metrics` and `_aircraft_label` to read **live only**
  (drop the cache fallback branch), now that every row has a `flight_id`.
- Change map/track readers (`map_renderer.py`, `mission_tracks.py`) to read
  `Flight.gps_track` exclusively.
- Keep the columns in the DB but **stop reading them** (a "quarantine" release —
  if something was missed, the data is still there to diagnose).
- **Rollback:** revert to the fallback-tolerant readers.
- **Gate to Phase 4:** one full release cycle in production with zero reads of
  the cache (add a log-once counter that fires if any cache read path executes;
  it must stay at zero).

### Phase 4 — Drop the columns
- Alembic migration: make `flight_id` `NOT NULL`; **drop**
  `opendronelog_flight_id`, `aircraft_id`, `flight_data_cache`.
- Remove `_scalar_cache_from_flight`, `_strip_cache_heavy_keys`, the ODL attach
  branch, and the cache-fallback code.
- Remove the `opendronelog_client.get_flight_track` dependency if nothing else
  uses it.
- **Rollback:** this is the point of no return for the columns. It fires **only**
  after Phase 3's zero-read counter has held for a full cycle. Backup/restore
  drill (per the fleet backup posture) before running the DROP.

---

## 5. Test strategy

- **Existing regression net (must stay green throughout):**
  `test_mission_flight_attach_derives_aircraft.py`,
  `test_mission_flight_dedup_report.py` (ADR-0026),
  `test_mission_large_flight_hardening.py` (ADR-0025 heavy-key),
  `test_mission_list_defers_heavy_columns.py`,
  `test_report_unrecognized_aircraft_label.py` (ADR-0033),
  `test_reports_metrics_adr0028.py`, `test_report_geo_bounded.py`.
- **New tests per phase:** Phase 0 census + write-purity; Phase 1
  post-attach-serial-registration correctness (ADR-0033 scenario without
  re-attach); Phase 2 materialization idempotency + metrics-parity diff; Phase 3
  zero-cache-read assertion; Phase 4 schema-shape + no-dangling-reference.
- **Golden-report diff harness:** snapshot the generated narrative + PDF metric
  tables for a fixed set of missions (native-linked, native-unlinked-Avata,
  legacy-ODL, duplicate-attach) before Phase 1 and assert no regression at each
  gate. This is the safety net that "never breaks live reports."

---

## 6. Reads to converge (explicit checklist)

1. `reports.py:186-191` metrics fallback → **live only** (Phase 3).
2. `reports.py:226-230` + `501-548` aircraft label/card → **`_aircraft_label` +
   live** (Phase 1 for the card; already done for the narrative).
3. `missions.py:588` aircraft copy → **stop writing** (Phase 1), derive on read.
4. `missions.py:601,618-629` ODL fleet-match + track-enrich → **materialize to
   `Flight`** (Phase 2), then delete (Phase 4).
5. `map_renderer.py:54`, `mission_tracks.py:*` track reads → **`Flight.gps_track`
   only** (Phase 3).
6. `reports.py:55-60` `_flight_key` → simplify to `flight_id` once ODL is gone
   (Phase 4).

---

## 7. How to retire `flight_data_cache` safely — summary

The cache is safe to delete **only after** the legacy-ODL class has no members,
because that class is the sole holder of data with no live backing row. The
sequence is: (0) measure, (1) converge aircraft reads + stop copying, (2)
materialize legacy-ODL into `Flight` rows (additive, reversible), (3) flip reads
to live-only and prove zero cache reads for a full cycle, (4) drop the columns
after a backup. At no point does a report lose access to its data — every phase
either adds a better source or removes a source already proven unused.

Report **narrative** quality is a separate axis and is planned in
`2026-07-03-report-quality.md`.
