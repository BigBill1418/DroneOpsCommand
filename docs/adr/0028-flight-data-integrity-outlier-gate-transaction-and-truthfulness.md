# ADR-0028 — Flight-data integrity: GPS outlier gate, batch-import transaction safety, race-safe dedup, live-scalar reporting, and Part-107 altitude truthfulness

- Status: Accepted
- Date: 2026-06-29
- Supersedes/relates: ADR-0019/0020/0025 (mission OOM), ADR-0026 (report metric accuracy + ghost flights), ADR-0027 (DJI duration + auto names)

## Context

A full audit of the flight-data / parser / mission-touchpoint surface (the
"Terry audit") found a cluster of correctness and reliability defects ranging
from a catastrophic distance corruption to ingest race conditions and altitude
truthfulness gaps. Each was verified against the live authoritative database
(`droneops-standby-db` on BOS-HQ) before being fixed.

Baseline (verified 2026-06-29): 737 flights — 584 `opendronelog_import`
(`raw_metadata = NULL`, never through the Rust parser) + 153 `dji_txt`. The
ADR-0027 duration re-stamp is 100 % complete; cache-vs-live drift is 0; all unit
conversions are correct and labeled. Those were NOT revisited.

## Decisions

### C1 — GPS outlier / teleport gate in distance summation

All three parsers (`dji.rs`, `airdata.rs`, `litchi.rs`) summed raw
consecutive-point haversine with no physical-plausibility check (the only filter
rejected the null-island `(0,0)` point). Ground truth: ODL flight `f57c9373`
reports **12,583,855 m in 554 s** (implied 22,722 m/s — orbital) versus a 29.1
m/s recorded max. Its own stored track, summed with an outlier gate, is **3,604
m** (6.5 m/s average — physically consistent).

A shared per-segment gate (`flight-parser/src/gate.rs::segment_ok`, mirrored in
`backend/app/services/flight_metrics.py`) drops a haversine segment when its
implied speed exceeds **60 m/s** (≈134 mph — far above any consumer/prosumer
multirotor max of ~30 m/s, so a real fast pass is never dropped), or, when no
per-segment Δt is available, when the raw segment exceeds **500 m**. The gate is
applied in all three parsers. The OpenDroneLog ingest path
(`import_from_opendronelog` + its streaming twin) additionally clamps a
physically-impossible passthrough distance (implied average > 3× the recorded
max speed, or > 60 m/s when no max is recorded) by recomputing from the stored
track, falling back to a `max_speed × duration` clamp; the original value +
method are recorded under `raw_metadata.distance_sanitized`.

**Data repair (migration 0006):** every existing implausible `opendronelog_import`
row is repaired the same way. Ground truth found **exactly ONE** affected row
(not the "handful" the audit estimated — the other rows the audit's heuristic
flagged were tiny dji_txt hover-jitter accumulations of 27–272 m with near-zero
max speed, not teleports, and are left untouched). The migration is idempotent
(keyed on the audit note) and audit-trailed.

### C2 — multi-file upload batch no longer poisons its own transaction

`get_db` only commits when `session.is_active`, which a failed flush clears. A
single mid-batch `IntegrityError` left the session in a failed state; every later
file hit `PendingRollbackError`, the route returned the flushed-but-uncommitted
flights as "imported" (HTTP 200), and `get_db` then skipped the end-commit —
silently discarding the whole batch. Each per-file unit in the three sync upload
loops (`/upload`, `/device-upload`, `/reprocess`, plus `/reprocess/all`) now runs
in its own `SAVEPOINT` (`db.begin_nested()`), so one failure rolls back only that
flight while every sibling still commits.

### H3 + H4 + M3 — race-safe auto-naming and dedup

`_generate_flight_name`'s only backstop was an unhandled `IntegrityError`. A new
partial UNIQUE index `uq_flights_source_file_hash` (migration 0005;
`WHERE source_file_hash IS NOT NULL`) makes dedup TOCTOU-safe — verified safe to
build (737/737 distinct, zero duplicates). The builder `_build_flight_from_parsed`
is now self-contained: it pre-checks the hash, flushes inside a savepoint, and on
`IntegrityError` either (a) regenerates the name and retries on an autoname
collision (bounded), or (b) returns a clean skip on a hash duplicate — it never
propagates an `IntegrityError` to poison the batch. M3 (per-flight hashes for
multi-flight files) is **deferred with rationale**: the `dji-log-parser` crate and
both CSV parsers emit exactly one flight per file today, so `source_file_hash` is
already per-flight; multi-flight support would key dedup on `sha256(file)+index`
and remains compatible with this index.

### H2 — report/PDF/LLM read live `Flight` scalars, not the cache snapshot

`flight_data_cache` is an attach-time snapshot. Drift is 0 today, but the C1
sanitize (and any future correction) would silently desync every report. The
report totals loop, `_build_flight_summaries`, and the PDF flight count now read
authoritative live `Flight` scalar **columns** (`gps_track`/`telemetry`/
`raw_metadata` never loaded — ADR-0019), falling back to the cache only for
legacy-ODL rows (`flight_id IS NULL`). The frontend `MissionDetail` continues to
display the scalar cache (still present after M1, and drift-0 correct); a live
fetch there is deferred as low value.

### H1 — Part-107 altitude truthfulness

`max_altitude` is launch-relative AGL in metres. Ground truth: **346/584 ODL +
74/153 dji** flights exceed 400 ft AGL (121.92 m); ~13 ODL rows cluster at exactly
500 m (the DJI configured ceiling, not a measured peak). The report engine now
flags per-flight `max_altitude > 121.92 m` and surfaces it both in the summaries
and the LLM system prompt, which is instructed to **state the altitude and the
fact of exceedance truthfully** while **never fabricating a waiver/authorization
claim either way** (the operator adds any LAANC/waiver context). ODL altitudes
pinned near 500 m are flagged "ceiling-limited (peak unverified)".

### H5 — map coverage measured at full resolution

`/map/coverage` and `/map?include_coverage=true` measured acreage on the
2000-vertex strided decimation (the ADR-0026 anti-pattern), so they could
disagree with the full-res PDF report. Both now route through
`calculate_mission_area_acres` (the same full-res, OOM-bounded path the report
uses); decimation is kept only for GeoJSON/PNG rendering.

### Medium fixes

- **M1** — migration 0007 strips heavy keys (`track`/`gps_data`/`coordinates`/
  `telemetry`) from native (`flight_id IS NOT NULL`) `flight_data_cache` rows (up
  to ~0.95 MB live), which can always re-resolve from `Flight.gps_track`.
  Legacy-ODL caches (their only track copy) are preserved. Key-name normalization
  is deliberately NOT done — the readers already accept both casings.
- **M2** — the ingest cadence guard derives Hz from `raw_metadata.frame_count`
  (the honest sample count; `point_count` excludes GPS-filtered points) and the
  ceiling is tightened 60 → 25 Hz.
- **M4** — bulk attach inserts each row in its own savepoint, so a concurrent
  unique-constraint race skips one row instead of 500-ing the whole bulk.
- **M5** — `reprocess/all` streams the stored file to the parser from an open
  handle instead of `read_bytes()` (OOM regression), re-runs the anomaly guard,
  and wraps each row in a savepoint.
- **M6** — a minimal-viability gate rejects a parse with no start_time, points or
  duration instead of persisting a junk `start_time=NULL` row.
- **M7** — the auto-name `LIKE` prefix escapes its literal underscores
  (`escape='\\'`) so the rank/conflict-bump no longer over-matches.
- **M8** — ghost/aborted launches are excluded uniformly from duration, distance
  AND flight count; the aborted count is surfaced separately.
- **M9** — a corrupt DJI header `total_time` far larger than the wall-clock frame
  span is bounded to that span before being trusted.

### Low fixes / documented

- **L2** — CSV `estimate_duration` 1 Hz fallback assumption documented; timestamp
  span is always preferred.
- **L4** — `_parse_datetime` tries ISO/format parsing before the numeric-epoch
  branch (and the epoch branch is magnitude-guarded), so a bare `"2024"` no longer
  becomes a 1970 timestamp.
- **L5** — tz-aware datetimes are normalized to naive UTC before storage in the
  naive `start_time` column (manual entry + any datetime input).
- **L6** — the scalar display cache now carries `drone_name` + `battery_serial`.
- **Documented, not changed:** L1 (max_speed is horizontal-only — inherent),
  L3 (decrypt-failure header-only flight is the intended fallback; now also
  protected by the M6 viability gate), L7 (duplicate manual names — cosmetic),
  L8 (battery cycle double-count — unreachable: the reprocess-update path does not
  re-track batteries and dedup prevents re-import), L9 (Airdata/Litchi generic
  drone_model + no serial — inherent to those export formats).

## Consequences

- The catastrophic 12.5 M m distance is gone; mission reports that summed it are
  no longer poisoned. New imports are protected at parse time and at ODL ingest.
- Batch uploads are atomic per-file; a single bad file can no longer silently
  discard an entire successful batch.
- Dedup and auto-naming are race-safe at the DB level.
- Reports/PDF/LLM are anchored to authoritative live data, not a snapshot.
- Altitude is reported truthfully against the 400 ft AGL limit without fabricating
  compliance claims.

## Failover & Resilience Guard

Pure application logic + row-data/index migrations. No change to port bindings,
connection strings, `pg_hba`, replication, or the blue-green/failover flow. All
three new migrations are idempotent and replicate to standbys via WAL. The new
partial unique index is verified conflict-free on the live data. The flight-parser
is a separate deploy image and is rebuilt by the change to `flight-parser/src/*`.

## Out-of-repo open item

The OpenDroneLog `maxAltitude` / `totalDistance` semantics (is ~500 m the
configured ceiling or an achieved peak?) cannot be confirmed from the repo — no
ODL API credentials are present in the repo or backend env, and the Gmail
connector is forbidden by fleet rule. **Flagged for operator verification**
against a raw ODL `/api/flight_data` payload. The C1 repair does not depend on
this: it recomputes distance from the stored track geometry, which is
translation-invariant for the small home-relative tracks ODL exports.
