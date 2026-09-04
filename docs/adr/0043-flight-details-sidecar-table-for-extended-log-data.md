# ADR-0043 — Extended DJI log data lands in a `flight_details` sidecar plus a `flight_series` table, not on `flights`

- **Status:** Accepted (decision recorded; implementation PLANNED, not started)
- **Date:** 2026-09-04
- **Amended:** 2026-09-04, same day — operator decisions D1–D7 (below) arrived
  after the first acceptance. The core decision (a sidecar, not columns on
  `flights`) is unchanged and reinforced. Two things changed materially:
  **time series moved out of the sidecar into their own table**, because
  full-resolution storage made a ~300 KB column on the sidecar a re-run of the
  problem the sidecar exists to prevent; and **the backfill now deliberately
  writes to `flights`**, which the original decision forbade, so a second
  guarantee mechanism is recorded here.
- **Version:** n/a — no code shipped with this ADR
- **Relates to:** [ADR-0019](0019-flight-library-list-defers-heavy-json-columns.md)
  (list defers heavy JSON), [ADR-0020](0020-report-geo-buffer-oom.md),
  [ADR-0025](0025-large-mission-flight-handling-oom-and-bulk-attach.md) (the
  third member of the same OOM family), [ADR-0022](0022-alembic-adoption-and-health-gate-trim.md)
  (Alembic), [ADR-0042](0042-fresh-install-integrity-and-demo-hygiene.md)
  (post-baseline migrations must be idempotent), [ADR-0027](0027-dji-duration-and-flight-name-correction.md)
  + [ADR-0028](0028-flight-data-integrity-outlier-gate-transaction-and-truthfulness.md)
  (the headline metrics this must not disturb), [ADR-0032](0032-flight-parser-unit-correctness-shared-conventions.md)
  (unit conventions + the missing-shared-layer standing risk),
  [ADR-0029](0029-mission-reports-are-client-deliverables-not-compliance-audits.md)
  / [ADR-0031](0031-odl-max-altitude-is-verified-remove-unverified-peak-caveat.md)
  (no altitude/Part-107 commentary), [ADR-0007](0007-strict-fleet-attribution-matcher.md)
  (attribution is by serial), [ADR-0017](0017-flight-date-operator-timezone.md)
  (flight-time provenance), [ADR-0018](0018-deploy-path-is-noc-fleet-deployer.md)
  (deploy path), [ADR-0041](0041-comprehensive-encrypted-backup-to-r2.md)
  (what a stored value ends up inside).
- **Implementation plan:** [`docs/plans/2026-09-04-flight-details-data-ingestion.md`](../plans/2026-09-04-flight-details-data-ingestion.md)
- **Research input:** [`docs/plans/2026-09-04-dji-log-untapped-data-census.md`](../plans/2026-09-04-dji-log-untapped-data-census.md)

## Context

The 2026-09-04 census established, against seven real production logs, that
`dji-log-parser` hands the parser far more than `flight-parser/src/dji.rs` keeps:
per-point timestamps, RC link quality, distance-from-home, the flight-mode/RTH
timeline, photo/video events, gimbal pointing, MSL altitude, battery
current/mAh/cell balance, the human-readable warning stream, and — one call
deeper into the raw record stream — pilot GPS, pack cycle count, component
firmware, and failsafe configuration. All 210 production `dji_txt` flights are
v14 logs with `frames_decoded=true` and their originals are retained at
`/data/uploads/flight_logs/<sha256>.txt`, so every item is backfillable.

The operator asked for it to be captured into the database and made reachable
from the Flights menu, with utilisation ("reports, battery, maintenance")
deferred. That framing — **breadth of capture now, minimal presentation, no
consumers yet** — is what forces the storage decision, because we commit to
persisting a lot of new data before knowing which parts will be queried.

The constraint that dominates: `flights` is the subject of three prior OOM
incident ADRs. It already carries three heavy JSON columns (`gps_track`,
`telemetry`, `raw_metadata`, `backend/app/models/flight.py:47-49`), and ADR-0019
exists because SQLAlchemy loading them by default on a 500-row list query
OOM-killed the uvicorn worker into a crash-loop and made uploaded flights vanish
from the mission picker. That ADR's suite includes a coverage test that forces a
decision when a new large JSON column is added to `Flight`. This change is that
trigger, and it arrives while a related hazard is still live:
`reprocess_all_from_stored` does a bare `select(Flight).where(...)` with no
`defer()` (`backend/app/routers/flight_library.py:1650-1659`).

### Operator decisions that shaped the amendment

| # | Decision |
|---|---|
| D1 | Store the **full raw pilot position track**, plus derived pilot-to-aircraft distance. Reports must not include it unless deliberately added. |
| D2 | **Full resolution at rest** — no downsampling when stored; downsample only at the API/read layer. |
| D3 | Flight Details link on **every** flight regardless of source; empty sections say "not available for this source". |
| D4 | **Logs become the source of truth for batteries** — pack values drive `cycle_count` / `health_pct`; hand-entered values kept in history but no longer shown as current. |
| D5 | The backfill also **repairs existing rows**: re-stamp `gps_track`/`telemetry` timestamps on the 210, and replace the literal `drone_model = "Unknown(NNN)"` with the header `aircraft_name`. Duration/distance/max-altitude/max-speed stay untouched. |
| D6 | **Evaluate a `dji-log-parser` bump first**, with a before/after diff over all retained logs; adopt only if nothing moves unexplained. |
| D7 | **Re-import OpenDroneLog-era flights** where original DJI files are recovered, replacing matched rows and carrying over mission attachments, notes, tags, pilot and aircraft. |

## Options considered

### A. Widen `flights.telemetry` + add a `flights.flight_details` JSON column

- **For:** no new table, no join; the existing `/telemetry` read path already
  serves series.
- **Against:** adds heavy JSON to the one table three OOM ADRs are about,
  widening the live `reprocess/all` footgun and every future `select(Flight)`.
  Protection would rest on remembering to `defer()` — a discipline that has
  already failed once in production.
- **Against:** `Flight.telemetry` is SQLAlchemy generic `JSON` → Postgres `json`,
  not `jsonb`; nothing stored there could ever be GIN-indexed without an
  `ALTER TABLE … USING` rewrite of a large TOASTed column.
- **Fatal under D2.** At full resolution the series alone are ~1.3 MB of raw
  JSON per flight, so every full-entity load in the codebase becomes a
  multi-megabyte detoast.

### B. A `flight_details` sidecar table, 1:1 with `flights`, holding everything

- **For:** extended data becomes opt-in by construction; typed scalars are
  queryable; `JSONB` keeps GIN indexing available for `events`; the backfill can
  address rows by column-select and never load the `Flight` entity.
- **Against under D2:** a ~300 KB compressed `series` column on the sidecar
  re-creates the ADR-0019 trap one level down — any `select(FlightDetails)`
  entity load detoasts the whole thing to read a single scalar.

### C. Fully normalized child tables, down to per-sample rows

- **For:** real SQL over every value.
- **Against:** the M4TD census log has 13,870 frames; 14 series across 210
  flights is ~41 M rows, built for a query nobody has asked for, with write cost
  on a streaming-replicated primary.

### D. Sidecar for scalars **+ a separate series table** (chosen)

- Everything B offers for the scalar/summary surface, plus isolation of the one
  genuinely large payload.
- **One row per (flight, source, series name)**, so a chart request touches one
  ~110 KB array rather than the flight's whole ~1.3 MB of series.

## Decision

**Split the storage.**

**`flight_details`** — 1:1 with `flights`, primary-keyed on `flight_id`
(`FK flights.id ON DELETE CASCADE`). Typed, nullable, unit-suffixed columns per
ADR-0032 conventions for every scalar a future query might filter or aggregate
on; `JSONB` for the small structured groups (`phases`, `events`, `config`,
`firmware`, `health`, `sd_card`, `serials`). `schema_version`, `parser_version`,
`crate_version` and `generated_at` on every row, so "re-backfill everything
produced below version X" is a trivial query.

**`flight_series`** — `PRIMARY KEY (flight_id, source, name)`, with `unit`,
`sample_count`, `precision_dp`, and `values`. **Full resolution, one value per
source sample** (D2).

Binding consequences, each part of the decision rather than an implementation
detail:

1. **No new column is added to `flights`.** The ADR-0019 heavy-column set is
   unchanged.
2. **Both relationships are `lazy="noload"`**, matching `battery_logs`
   (`models/flight.py:58`). `selectin` on either reproduces ADR-0019. A test
   asserts the compiled list query references neither table, with a control test
   proving the assertion is meaningful.
3. **No secondary index ships until a query needs one.** An index is write cost
   on a replicated primary, paid on every insert, for a read that does not exist
   yet. `JSONB` on the group columns is what keeps a later GIN cheap.
4. **`flight_series.values` is `json`, not `jsonb` — deliberately the opposite
   of the group columns.** `jsonb` re-encodes every number as a variable-length
   `numeric` and adds a per-element `JEntry` header; on a 13,870-element float
   array that is tens of KB of pure overhead, and binary numerics compress worse
   than ASCII digit runs. The only thing `jsonb` buys — containment operators and
   GIN — is worthless for an opaque array we always fetch whole. The distinction
   is intentional and must not be "tidied up" later. A `DOUBLE PRECISION[]`
   alternative is decided by measurement, not argument: a P0 acceptance gate
   compares `pg_column_size()` and read latency on one real flight before P1
   locks it in. Default `json`.
5. **`source` exists on `flight_series` because the series do not share one time
   base.** Frames, AppGPS and OFDM record at different cadences (13,870 / 657 /
   5,538 in the M4TD census log). Each source group carries its own `t_offset_s`
   row and every series within the group is index-aligned to it.
6. **Rounding, not decimation, is what makes full resolution affordable.** Every
   series is emitted at physically meaningful precision (0.1 m, 0.01 m/s, 0.1°,
   1 mV, 7 dp for coordinates) recorded in `precision_dp`. Every sample is kept;
   only spurious f64 mantissa digits are dropped — a ~4× text reduction with zero
   information loss. Scalars are computed at full resolution **before** any
   read-layer decimation; maxima, minima, edge counts and the `∫V·I dt` / `∫I dt`
   integrals never see a reduced array.
7. **Three separate write sets, three separate guarantees.** A bug in one cannot
   reach the others:
   - **A — details + series:** the `Flight` ORM entity is never loaded. The
     backfill selects `Flight.id` and `Flight.source_file_hash` as *columns*, so
     there is no object to dirty and no attribute assignment is possible. The
     existing `/reprocess/all` route is explicitly **not** reused: its write set
     is precisely the columns that must not move
     (`flight_library.py:1699-1709`), and its selector
     (`point_count == 0 OR gps_track IS NULL`) matches none of the 210 anyway.
   - **B — the D5 repair pass**, which *does* write to `flights`. Guarantee:
     timestamps are **merged into the existing stored track**, never replaced by
     the new parse's track, so coordinates cannot change because they never come
     from the new parse; a length precondition **skips** any row whose re-parse
     yields a different point count rather than attempting alignment; and a
     post-write assertion inside the savepoint recomputes the coordinate hash and
     re-reads the seven scalars, rolling that row back on any drift.
     `drone_model` repair is predicated on the anchored regex
     `^Unknown\(\d+\)$`, which a real model name can never match. Prior values are
     preserved in `flight_details` (`gps_timestamps_restamped_at`,
     `drone_model_previous`) so `flights.raw_metadata` stays untouched. Dry-run
     is the default.
   - **C — the D7 ODL replacement**, the one place where headline metrics change
     on purpose (see Consequences).
8. **Fleet-level verification is a checksum, not a promise.** Identical
   `md5(string_agg(...))` over the headline scalars **and** over every
   `gps_track` coordinate, taken before and after each write set, or the pass is
   reverted. Scalars alone are insufficient once D5 writes inside `gps_track`.
9. **The migrations are idempotent** (ADR-0042): `0001_baseline_schema` builds
   fresh databases with `create_all` from the live models, so `0010` and `0011`
   must no-op when their objects already exist, exactly as `0009` guards its
   column.
10. **The parser contract stays backward compatible.** `ParsedFlight` gains one
    optional `details` field with `skip_serializing_if`; Litchi and Airdata emit
    byte-identical JSON, and the Rust compiler forces both call sites to be
    audited because they build struct literals.
11. **ADR-0029 / ADR-0031 remain in full force.** The Flight Details view
    presents recorded height limits as data. It does not compare them to
    anything, does not mention 400 ft or Part 107, and emits no compliance
    commentary — on that page or anywhere downstream.
12. **A report-audience guard is part of this decision, not a follow-up** (D1).
    Pilot position is now stored in full, so: the report engine reads nothing
    from either new table; a named, currently-**empty** allowlist constant in the
    report layer makes any future addition a deliberate one-line diff; a test
    asserts report generation issues zero queries against `flight_details` or
    `flight_series`; and the CSV/GPX/KML exports
    (`flight_library.py:2625,2645,2668`), being client-shareable, gain no pilot
    columns and are covered by the same test.

## Rationale

The deciding argument is not ergonomics, it is blast radius. Option A's cost is
not the column — it is that every existing and future `select(Flight)` becomes
slightly more dangerous, protected only by remembering to `defer()`. That
protection has already failed once here, in production, with a crash-looping API
worker and flights invisible in the mission picker. A sidecar removes the failure
mode instead of adding another instance of it: code that does not join the table
cannot be hurt by it.

D2 then applies the same reasoning one level down, and is why option B alone is
no longer sufficient. Storing full-resolution series is right — the operator
wants the fidelity, and rounding makes it affordable — but a ~300 KB column on
the sidecar means an ordinary `select(FlightDetails)` entity load, which is what
ORM code writes by default, detoasts the whole payload to read one scalar. That
is ADR-0019's exact shape. Splitting removes the possibility rather than
documenting it. The per-series row is then close to free and buys roughly a 12×
reduction in bytes touched on the chart path, which matters on a backend with a
1536 MiB cgroup and this incident history.

Option C is the right shape for a system that knows its queries. This one does
not yet — the operator's own framing is "then later we can figure out where else
to pull that data in." Normalizing to per-sample rows now is schema built on a
guess, paid for in migrations before anything appears on screen. The chosen split
defers that decision cheaply: `events` is the one group with a plausible SQL
future, it is `JSONB`, and it can be GIN-indexed in place or promoted to a child
table later with the stored JSONB as its source.

On the backfill: the hardest requirement is that re-reading 210 logs through a
*new* parser build must not move a number ADR-0027 and ADR-0028 established.
Write set A gets a structural guarantee — the entity is never in the session.
D5 removes that option for write set B, so B gets the strongest mechanism
available instead: never source the protected values from the new parse at all
(merge timestamps into the existing track), refuse to proceed when the shapes
disagree, and assert the invariant after writing, inside the savepoint that can
still roll it back. Structural where possible; verified where not.

## Consequences

- Extended log data is durable, queryable, versioned, and stored at full
  fidelity, without making any existing flight query heavier. The
  ADR-0019/0020/0025 OOM family gains no new member.
- **Storage grows materially and knowingly.** Recomputed from the census: ~1.3 MB
  of raw JSON per M4TD-sized flight (13,870 frames × 14 frame series + pilot +
  OFDM blocks), compressing to roughly **260–430 KB**; ~100–150 KB for a typical
  5,000-frame flight. Across 210 flights, **~25–60 MB one-time**, plus
  ~100–300 KB per new flight. For calibration, ADR-0019 measured
  `gps_track + telemetry + raw_metadata` at 33–44 MB compressed across the top
  500 flights — so the series are roughly 0.6–1.8× the existing heavy-JSON
  footprint. Postgres handles this comfortably; the risk was always in read
  paths, which is what the split addresses. The estimate is verified by
  measurement after the first backfill batch, before the remaining 185 run.
- **The timestamp asymmetry the first draft accepted is now closed** by D5. Every
  DJI flight, old and new, ends with per-point timestamps in `gps_track` and
  `telemetry.timestamps`. The Flight Details view still reads its time base from
  `flight_series` rather than `flights.telemetry`, because the two are
  independently maintained and cross-indexing them would be a latent bug.
- Three currently-always-null API fields become populated: `signal_strength`
  (hard-coded `None` at `dji.rs:265`), `distance_from_home` (`dji.rs:266`) and
  `timestamps` (`dji.rs:120`). `/telemetry` already serves all three and the
  frontend already types them permissively, so this is additive.
- **The `SmartBatteryStatic` correction is a shim we own, and the census's
  characterization of it was wrong.** Working the census's own four values, the
  transform is uniformly `raw >> 8` — *not* a byte swap. For the 32-bit
  `designed_capacity` the two differ, and the M30 sample's low byte is `0x02`,
  nonzero, which a clean endian swap of a well-formed field would not leave. That
  reads as a field-offset/alignment bug in the crate's struct decode. The shim is
  therefore plausibility-gated (`pack_values_plausible`) and flagged
  (`pack_values_shimmed`), and D6's evaluation checks whether it is needed at all
  before it is built.
- **Battery numbers change meaning** (D4). `batteries.cycle_count` stops being a
  per-import counter (`flight_library.py:2568`) and becomes the pack's own
  lifetime cycles when a plausible value exists, with `metrics_source` recording
  which. The legacy counter is preserved in `cycle_count_observed` and keeps
  incrementing so the two stay comparable. Pack-sourced writes take
  `GREATEST(cycle_count, pack_cycle_count)` — without that guard, a backfill
  processing flights in arbitrary order would leave the count at whatever the
  last-processed, possibly oldest, flight reported. `battery_logs.cycles_at_time`
  keeps its existing meaning and the pack value goes in a **new** column;
  silently redefining a column mid-history would corrupt the series it holds.
- **D7 is the one place the "nothing moves" guarantee deliberately does not
  apply.** A replaced ODL row's headline metrics change — ODL passthrough values,
  some already sanitized by ADR-0028 C1 / migration `0006`, give way to a real
  DJI parse. It is scoped to rows with a *unique* file match; the fleet checksum
  covers `source = 'dji_txt'` and is unaffected; prior values are preserved in
  `flight_details.replaced_from`; and the dry-run reports per-match metric deltas
  for operator review before any write. Replacement is **update-in-place**, which
  makes the mission-attachment carry-over free — the primary key never changes,
  so every foreign key survives untouched — at the cost of one obligation:
  `mission_flights.flight_data_cache` scalars
  (`missions.py:_scalar_cache_from_flight:85`, ADR-0025 A2) must be refreshed for
  affected rows, because the cached duration/distance will have changed.
- **The D7 matching rule refuses rather than guesses.** Serial, start time
  (±120 s) and duration (±max(30 s, 5 %)) must all hold; two or more candidates
  aborts the match; a row with no serial is never matched. The uniqueness rule is
  not optional: ADR-0027 measured **44–184 s** battery-swap gaps between
  consecutive flights on one airframe, so a ±120 s window can reach an adjacent
  flight. A file whose hash already exists is reported as a duplicate rather than
  replaced — merging a doubled physical flight is a destructive operator call.
- **Pilot coordinates are stored and therefore reach R2 backups** (ADR-0041) and
  are recoverable from them. The §4.4 guard is the mitigation, and
  `pilot_track_stored` makes affected rows enumerable if a purge is ever wanted.
  This is a deliberate, operator-directed reversal of the first draft's
  derived-only default.
- **Stick positions (`RC` / `RCDisplayField`) and Tier 2 `Unknown` record types
  remain explicit non-goals.** ~30 % of records in newer logs stay undecodable
  with the current crate and no reverse-engineering is authorized.
- **A crate version bump is gated on evidence, not appetite** (D6). It could
  retire the shim and fix the `Unknown(178)/(137)/(139)` `ProductType` gap, but
  it can also change frame decoding and therefore headline metrics on all 210
  flights. It requires a full before/after diff over every retained log, a
  classification of each difference as expected-improvement or unexplained, a
  report in `docs/reports/`, its own ADR, and a `force=true` re-backfill on
  adoption.
- The inline `downsample` helper in `get_telemetry`
  (`flight_library.py:1999-2003`) is extracted to a shared service used by both
  read paths. This is ADR-0032's standing finding applied prophylactically — that
  ADR concludes the absence of a shared layer is what lets a defect class recur,
  and a second copy-pasted downsampler would be the same mistake.
- **Failover & Resilience Guard:** two additive tables and three additive
  nullable columns via plain DDL — replication-safe over WAL, no port binding, no
  `pg_hba`, no connection-string change. Survives container recreation (the
  migrations are idempotent and the fresh-DB path builds from live models) and
  standby promotion. The backfill, repair and re-import passes are all
  authenticated, operator-initiated, batched and idempotent, so a mid-pass
  failover loses at most the in-flight batch and the selector picks it up on
  retry. No customer-facing service is affected during a failover.

## Status of implementation

Nothing is built. The nine-phase build order, sizing, per-phase tests,
version-bump targets, deploy notes, the D7 matching rule, nine risks and three
remaining assumptions are in
`docs/plans/2026-09-04-flight-details-data-ingestion.md`. ROADMAP item **FP-1**
points at that plan. P7 is blocked on the log-inventory hunt, recorded there as a
PENDING section for the operator to fill in.
