# ADR-0027 — DJI flight duration parsed from authoritative header airtime; auto flight names made unique + start-ordered

- Status: Accepted
- Date: 2026-06-29
- Supersedes/relates: ADR-0017 (operator-local flight dates), ADR-0026 (report metric accuracy)

## Context

The "Savannah Bananas Games" mission (27 flights: 19× DJI Mavic 4 Pro serial
`1581F986C258E002`, 8× DJI Matrice 4TD serial `1581F8HGX255P00A`) surfaced an
"impossible single-airframe overlap": one Mavic appeared to be airborne on two
overlapping intervals. Investigation (DB-evidence-backed) inverted the original
"start_time is wrong" premise. **`start_time` is correct** — it matches the DJI
log filename local-time token plus the 7 h PDT→UTC offset and is parsed straight
from the log header (`flight-parser/src/dji.rs`). Two *other* bugs were the cause.

### Root cause #1 — duration estimated from a hard-coded sample rate

`flight-parser/src/dji.rs` discarded DJI's authoritative airtime
(`details.total_time`, persisted as `flights.raw_metadata->>'header_duration'`)
and instead estimated:

```rust
let final_duration = if has_frames { frames.len() as f64 / 10.0 } else { header_duration };
```

The `/10.0` assumes every airframe logs at 10 Hz. It does not:

| Model           | True cadence | Effect on stored `duration_secs` |
|-----------------|--------------|----------------------------------|
| Mavic 4 Pro     | 15 Hz        | inflated **×1.5** |
| DJI FPV         | ~5 Hz        | **halved** (×0.5) |
| others          | ~10 Hz ± drift / frame drops | within a few % |

DB verification (live primary `droneops-standby-db`, source `dji_txt`,
`header_duration` present): Mavic 4 Pro avg ratio **1.4927** across 36 flights;
DJI FPV avg **0.514** across 2. Using `header_duration` for every flight makes
the Savannah flights line up perfectly sequential with 44–184 s battery-swap
gaps — the overlap vanishes. The header is authoritative; the frame count is an
estimate.

### Root cause #2 — auto flight-name sequence collisions + garbage order

`_generate_flight_name` (`backend/app/routers/flight_library.py`) built the
trailing `_NNNN` from a **`created_at` count inside a `start_time`-day window,
fleet-wide, not per-aircraft, not start-ordered**:

```python
day_start = flight_date.replace(hour=0, ...)          # start_time's day
count = SELECT count(*) WHERE created_at >= day_start AND < day_end   # ingest time!
```

Three compounding defects: (a) the window is from `start_time`'s day but the
count filters on `created_at`, so when start-day ≠ ingest-day the count is 0 and
everything collides on `_0001`; (b) it is a fleet-wide "created today" tally, not
per-aircraft or start-ordered, producing descending junk sequences
(…0012,0010,0007,0005,0002); (c) the count window used a UTC day boundary against
a Pacific date token. The Pacific `date_str` itself (ADR-0017) was already
correct. Result: 16 duplicate DJI-prefixed names fleet-wide (Savannah: 7×
`DJI-Matrice-4TD_20260627_0001`, 3× `DJI-Mavic-4-Pro_20260627_0001`, plus
`DJI-Mini-5-Pro_20260506_0001`×2, `DJI-Mavic-4-Pro_20260521_0003`×2,
`DJI-Matrice-4TD_20260610_0002`×2). Operator-typed names (`Batt Maint`×4,
`Maintenance Check Flight`×3) are unrelated and left alone.

### Blast radius is wider than first diagnosed

The duration bug is not limited to "35 Mavic + 1 FPV". Because `header_duration`
is authoritative, **59** `dji_txt` flights (of 153 with a header) diverge > 1 %
and were re-stamped: 36 Mavic 4 Pro, 13 Mavic 3 Pro, 4 Matrice 4TD, 2 Matrice
30T, 2 DJI FPV, 1+1 Avata 2. The Mavic 4 Pro (×1.5) and FPV (×0.5) cases are the
dramatic ones; the others drift 1–10 % where cadence ≠ exactly 10 Hz or frames
dropped. The remaining 94 within-1 % rows are left untouched by design.

Also discovered (not in the original diagnosis): the mission report sums
`mission_flights.flight_data_cache->>'duration_secs'` — a snapshot taken at
attach time — **not** live `flights.duration_secs`. Re-stamping flights alone
would not fix any report; the cache must be re-stamped too (26 rows).

## Decision

1. **Parser (`dji.rs`)** — prefer `header_duration` whenever present and
   positive. Only when it is absent/zero fall back to a frames-based estimate,
   and derive that from **actual frame timestamps** (`osd.fly_time` elapsed-time
   counter, else the `custom.date_time` wall-clock span) — never a hard-coded
   divisor, so it is model-agnostic. Extracted as a pure `choose_duration()` with
   unit tests.

2. **Data re-stamp (Alembic 0004)**, idempotent:
   - `flights.duration_secs ← header_duration` for `dji_txt` flights diverging
     > 1 % (59 rows).
   - `mission_flights.flight_data_cache.duration_secs ← header_duration` (26
     rows) so regenerated reports read correct airtime.
   - Recompute auto-pattern names (`<label>_YYYYMMDD_NNNN`) as the start-time
     rank within each `(label, operator-local day)` group (69 of 95 rows).
   - Add partial unique index `uq_flights_autoname` on auto-pattern names.

3. **Naming (`_generate_flight_name`)** — sequence = start-time rank within the
   `(label, operator-local day)` group, with a conflict-bump loop. Grouping is by
   the *name label* (the model token on the wire) because that is what must be
   unique; when one model maps to one airframe this equals per-airframe.

4. **Ingest guard (`_check_ingest_anomalies`)** — defense-in-depth, runs on every
   import (all paths funnel through `_build_flight_from_parsed`). Flags (never
   rejects — DJI logs are authoritative) two fingerprints of a mis-parsed
   duration: implausible `point_count/duration` cadence, and overlapping
   `[start, start+duration)` intervals for a single airframe. Overlap pages ntfy
   `high` on topic `droneops-flight-overlap` (ADR-0036/0037), deduped per
   airframe+local-day.

## Consequences

- Savannah total airtime corrects from **11.59 h → 8.54 h** (Mavic 9.15 h →
  6.10 h; Matrice 2.44 h unchanged). Distance (62.29 mi), start_time, and flight
  count (27) are unchanged. The altitude/Part 107 flag from the prior cycle still
  stands.
- Names become unique and start-ordered fleet-wide for machine-generated names;
  operator free-text names are untouched and may still repeat.
- Re-stamps are not reversed on downgrade (prior values were derived from a
  known-buggy estimator); only the index is dropped.

## Failover & Resilience Guard

No port bindings, connection strings, `pg_hba`, replication topology or
blue-green swap flow are touched. The migration is pure row data + one partial
index, applied only on the writable primary (`pg_is_in_recovery()` guard,
ADR-0021) and replicated to the CHAD-HQ/BOS standby via WAL. The flight-parser
image rebuilds via the deployer digest gate; it is a stateless microservice.
