# ADR-0026 — Duplicate flight attachments: kill the report-inflation at the source + accurate, unit-correct metrics

* **Status:** Accepted
* **Date:** 2026-06-29
* **Extends / corrects:** ADR-0025 (large-mission flight handling + bulk attach). ADR-0025 added an *idempotent bulk* attach path but left the *single* attach path (`add_flight`) with zero dedup and added no DB-level guard — this ADR closes that gap and corrects the report metrics it feeds.
* **Version:** v2.74.0

## Context

The AI mission report for **"Savannah Bananas Games"** (a live-broadcast stadium production, mission `e5f3aedf-c3a2-46d2-9438-33a3cb8f3f8f`) showed badly inflated numbers. Verified against the authoritative DB (`droneops-standby-db` on BOS-HQ — the real primary; `droneops-db-1` is a neutralized `alpine:3` stub):

| Metric | Reported (wrong) | True (dedup by flight) | Inflation |
|---|---|---|---|
| Total Flights | 50 | 27 | +85% |
| Total Flight Time | 1,659.9 min (27.7 hr) | 695.3 min (11.6 hr) | +139% |
| Total Distance | 160.33 mi | 62.29 mi | +157% |
| Area Covered | 67.67 acres | ≈67 acres | ≈correct |

### Root cause (RC-1 — the one thing)

The `mission_flights` table held **50 rows for 27 unique flights** — 23 duplicates from a **retry storm**: attach requests timing out / 500ing during the 2026-06 OOM window (ADR-0025) while the operator/frontend re-clicked. Copy counts: one flight ×13, another ×7, ×4, ×2, ×2 (= 28 rows for 5 flights → 23 extra). All 23 duplicates were byte-identical, created within a 52-second window.

Nothing caught the repeat:
* `missions.add_flight` (single attach) had **zero dedup** — every call inserted a new row. (`add_flights_bulk`, added in ADR-0025, *is* idempotent, but savannah's rows carried full tracks → they came via the old single-add path, predating bulk.)
* There was **no unique constraint** on `mission_flights (mission_id, flight_id)`.
* The report summed over **all** rows (`reports.py` `_build_flight_summaries` for count; the duration/distance loop), with no dedup.

Because the duplicated flights were the long ones, totals inflated **non-uniformly** — the "completely fucked" signature. The report math was otherwise faithful; the LLM did not do bad arithmetic. Area was ≈correct because coverage unions geometry, so duplicate identical tracks contribute zero extra area.

### Secondary cause (RC-2 — measurement on decimated geometry)

Area was computed from the **2000-vertex strided decimation** used for the map render (`load_bounded_flight_tracks` → `extract_gps_tracks` → `calculate_area_acres`). Strided sampling distorts path shape; a *measurement* should run on full-resolution geometry. Negligible for savannah, but wrong in principle.

### Report-writing / credibility issues

* **Altitude units landmine.** The report printed altitude by restating raw `max_altitude` cache values and appending **"ft"**. Verified ground truth: DJI logs altitude in **metres** (flight-parser `src/dji.rs:50` — `header_max_height ... // meters AGL`; `airdata.rs` explicitly converts feet→metres on ingest), and `Flight.max_altitude` is metres. So the savannah max of `190.8` is **190.8 m AGL = 626 ft**, not "190.8 ft". The old narrative said *"altitudes ranged from approximately 96 ft to 190.8 ft AGL ... within applicable Part 107 operating parameters"* — it **understated altitude ~3.28×** and, with the correct unit, the true range (~315–626 ft AGL) **exceeds the Part 107 400 ft ceiling**, so the compliance claim was unfounded.
* **Ghost / aborted flights.** Two ~7 s, ~0.2 m, 0-altitude rows were counted as "flights" and dragged the altitude minimum to 0.

### Verification before acting

Every claim was re-verified on the authoritative DB before any mutation: the 50-vs-27 counts and the per-flight copy breakdown, the duplicate rows' byte-identical caches + 52-second creation window, the altitude unit (parser source + `gps_track` alt range == `max_altitude`), the two ghost rows, and a fleet-wide scan proving **only** savannah is affected (blast radius = 1 mission).

## Decision

1. **Repair the data (one-time, reversible).** Back up all 50 savannah rows (gzipped JSONL), then delete the 23 duplicates keeping the earliest `added_at` per `flight_id` (`id` tiebreaker). Re-verify 27/27. No other mission touched.
2. **Structural guard (RC-1).** Migration `0003` adds two **partial UNIQUE indexes** — `(mission_id, flight_id) WHERE flight_id IS NOT NULL` and `(mission_id, opendronelog_flight_id) WHERE opendronelog_flight_id IS NOT NULL`. It **dedups first** (idempotent, generic across all missions) then constrains, so it is safe on a brownfield DB. Applied to live immediately for instant protection; the deploy re-applies it as a no-op (`CREATE UNIQUE INDEX IF NOT EXISTS`).
3. **Idempotent single-add.** `add_flight` returns the existing row if the flight is already attached, with an `IntegrityError` backstop for the concurrent-attach race that slips the SELECT guard.
4. **Defensive report dedup.** `reports.py` aggregates (`_build_flight_summaries`, the duration/distance loop, the PDF `flight_count`) iterate over flights uniqued by identity key — the engine never trusts the row set to be duplicate-free.
5. **Area on full-resolution geometry (RC-2).** New `mission_tracks.calculate_mission_area_acres` loads each flight's full-res track on demand, one at a time, projects + shape-preservingly simplifies (Douglas-Peucker @ tolerance) + buffers it, discards the raw track, then unions — full-res measurement with the ADR-0025 OOM fix preserved (peak memory O(one raw track + N simplified polygons)). The map keeps the cheap decimated render path.
6. **Accurate, unit-correct report writing.** Altitude is formatted in code as `"<m> m AGL (<ft> ft)"` (source unit primary, explicit conversion) so the LLM never guesses or appends a unit; ghost/aborted launches (duration < 30 s AND distance < 10 m) are flagged and excluded from altitude ranges; the shared system prompt now instructs the model to restate provided totals verbatim with their labels, never convert/relabel units, exclude aborted launches from ranges, and never invent figures.

## Consequences

* Reports are accurate and duplicate-proof at the storage layer, the attach layer, and the aggregation layer (defence in depth). Re-clicking an attach is a safe no-op.
* The corrected savannah figures (27 flights / 695.3 min / 62.29 mi) restate the deduped DB exactly. **Honesty flag for the operator:** with altitude now unit-correct, the data shows a max of 190.8 m AGL (626 ft) — above the Part 107 400 ft ceiling. This is either a genuine operational/waiver matter or an artifact of non-representative data (the duplicates were generated in a 52-second burst); the report no longer asserts Part 107 compliance, and this is surfaced for operator review rather than silently printed or hidden.
* The full-res area path costs more GEOS work per report than the decimated path, but remains bounded (one raw track at a time) and runs in the executor off the event loop.

## Failover & Resilience Guard

* Migration runs only on the **writable primary** (the ADR-0021 `pg_is_in_recovery()` guard wraps the programmatic runner); the new indexes replicate to the CHAD-HQ / BOS standby via WAL. Plain (non-`CONCURRENTLY`) `CREATE UNIQUE INDEX` on a thousands-of-rows table is sub-second — same rationale as ADR-0021/0022. Does **not** touch port bindings, connection strings, `pg_hba`, the blue-green swap, or the failover engine.
* The data repair was a transactional `DELETE` with an in-transaction assertion (commit only if exactly 27/27 remain), executed against the authoritative primary; it replicates to the standby as ordinary WAL.
