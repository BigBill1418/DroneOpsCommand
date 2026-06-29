# ADR-0025 — Large-mission flight handling: kill the GPS-track OOM at the source + bulk attach

* **Status:** Accepted
* **Date:** 2026-06-29
* **Supersedes / extends:** ADR-0019 (flight-library list defers heavy JSON columns), ADR-0020 (report-geo buffer OOM). This is the third member of the same OOM family.
* **Version:** v2.73.0

## Context

An operator built a mission ("savannah") with far more flights than any prior mission. Two symptoms appeared together:

1. **Adding/opening the mission errored out.** Re-opening the flights editor (or `MissionDetail`, or generating a report) returned a Cloudflare 502/520. The frontend's `loadMission` catch masked the real status behind a generic "Could not load mission flights" and navigated away.
2. **Adding flights was painfully slow** — one `POST /missions/{id}/flights` per click, each round-trip optimistic.

### Root cause (the one thing)

`GET /api/missions/{id}` serializes the **full GPS track for every attached flight**. Each attach copied `Flight.gps_track` (~19k points for a 30-min survey) into `MissionFlight.flight_data_cache` (`missions.py` `add_flight`), and the detail handler returned the full `MissionResponse` including every cache. So the response body was **O(N_flights × ~19k points)**. At savannah scale that materialized/serialized past the backend's 1536 MiB cgroup cap and the kernel OOM-killed uvicorn mid-response.

This is the same class as:
* **ADR-0019** — the flight-library *list* dragged the same heavy JSON; fixed by `defer()`.
* **ADR-0020** — *report* geometry buffered raw tracks; fixed by per-track Douglas-Peucker simplification.

v2.70.0's lean-list work (`MissionListItemResponse`, `defer(flight_data_cache)` on financials, `raiseload(MissionFlight.flight)`) deliberately left the **detail** and **report** read paths returning full tracks "byte-identical." Those two paths plus the **on-demand map** path (`maps.py`, which eager-loaded every linked `Flight` in full) were the remaining O(track) reads.

### Verification

Confirmed by reading the code before changing it: the only frontend consumers of `flight_data_cache` from the **detail payload** read **scalars** (`MissionDetail.tsx` reads `duration_secs`; `MissionFlightsEdit.tsx` reads display fields). `FlightReplay`/`Flights` read `gps_track` from the **per-flight** detail route, and the mission map reads tracks from `maps.py` — neither depends on the detail payload carrying a track. So stripping the track from the detail/report responses is safe.

## Decision

### A. Kill the OOM at the source

1. **Detail/write read path (A1).** `GET /api/missions/{id}` and every write re-query (POST/PUT/PATCH) serialize through `_serialize_mission()`, which strips `track`/`gps_data`/`coordinates`/`telemetry` from each flight's cache **before** serialization. It is **outbound-only** — the ORM rows are never mutated, so a read never triggers a write and legacy track data on disk is preserved. The strip is pure Python (`_strip_cache_heavy_keys`), so it behaves identically on the Postgres prod path and the SQLite/stub test paths. Detail payload becomes **O(rows)**.

2. **Root data-model fix (A2).** Attaching a **native** flight (`add_flight`, and bulk) stores **scalar display fields only** via `_scalar_cache_from_flight()` — never the GPS track. The track stays once on `Flight.gps_track` and is loaded on demand. The **legacy-ODL** attach path (rows with no `Flight` row, which fetch their track from OpenDroneLog) is **unchanged** — that cache track is the only copy those rows have, and A1 still strips it on read.

3. **Report + map path (A3).** New `services/mission_tracks.load_bounded_flight_tracks(db, mission)` resolves each flight's track **one at a time** — from the cache if present (legacy/ODL), else `Flight.gps_track` by `flight_id` — and **immediately decimates** it to `MAX_RENDER_VERTICES_PER_TRACK` (the cap already validated for rendering in ADR-0020), dropping the raw reference before the next flight. Peak memory is **O(one raw track + N × cap)** instead of O(N × raw). `reports.generate_report` and all three `maps.py` endpoints use it; `maps._load_mission` no longer eager-loads `MissionFlight.flight` (which had cascaded into loading every linked `Flight` in full).

### B. Fast multi-add

New `POST /api/missions/{id}/flights/bulk` (`MissionFlightBulkAttach`): inserts many `MissionFlight` rows in **one transaction** (single `db.flush()`), **idempotently** skipping flights already attached (by native `flight_id` or legacy `opendronelog_flight_id`) and de-duplicating within the batch. Native flights are bulk-loaded with the heavy JSON columns **deferred** (so a 500-flight attach stays O(rows)); aircraft is derived server-side (native: `Flight.aircraft_id`; ODL: fleet-match); caches are scalar-only. The editor (`MissionFlightsEdit.tsx`) gains **checkboxes + "Select all" + "Add selected (N)"**, and the per-row ADD routes through the same endpoint with a single item.

### C. Picker scale

The editor requests `/flight-library?limit=2000` (backend cap) so >500 library flights are reachable; previously it passed no limit and the backend's default of 500 silently hid the rest. (Pagination beyond 2000 is future work; flagged, not silently capped.)

### D. Frontend resilience

`loadMission` and the add path surface the **real HTTP status** in the Mantine notification and `console.error`, per the repo logging standard — the generic message had masked the 502/520.

### E. Guardrails

Tests pin the whole class: detail GET stays O(rows)/small payload on a fat legacy cache; native attach stores no track; bulk attach is one-flush / idempotent / scalar / ODL-preserved (with a source-level single-flush guard); the bounded loader decimates and loads on demand. Plus the existing ADR-0020 geo-bound tests still hold.

## Consequences

* Large missions open, serialize, report, and render within bounded memory regardless of flight count.
* Adding hundreds of flights is one request, not hundreds.
* `flight_data_cache` is now authoritatively **scalar** for go-forward native attachments; the GPS track has a single source of truth (`Flight.gps_track`). Legacy rows (native rows attached before this change, and ODL rows) keep their cache track on disk but it is stripped from every response and never re-served in bulk.
* **Failover/resilience:** no schema, port, replication, pg_hba, or blue-green change — read/write *behaviour* only. Survives container recreation (no init/volume dependency) and standby promotion. No customer-facing impact during failover.

## Alternatives considered

* **Postgres `jsonb` projection (`cache::jsonb - 'track'`) to strip in the DB.** Truly avoids loading the track into Python even for legacy rows, but is Postgres-only and breaks the SQLite/stub test paths. Rejected: with A2 in place, go-forward caches are already scalar, so the only transient load is for legacy missions — a strict improvement over the previous full serialize, and the Python strip is DB-agnostic and testable.
* **A data migration to drop tracks from existing caches.** Out of scope; the read-side strip + bounded loader make it unnecessary. Can be done later as hygiene.
* **Keep one-POST-per-click but parallelize on the client.** Doesn't fix the transactional/idempotency story and still hammers the API; the bulk endpoint is the correct shape.
