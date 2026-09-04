# ADR-0043 — Extended DJI log data lands in a `flight_details` sidecar table, not on `flights`

- **Status:** Accepted (decision recorded; implementation PLANNED, not started)
- **Date:** 2026-09-04
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
  (no altitude/Part-107 commentary).
- **Implementation plan:** [`docs/plans/2026-09-04-flight-details-data-ingestion.md`](../plans/2026-09-04-flight-details-data-ingestion.md)
- **Research input:** [`docs/plans/2026-09-04-dji-log-untapped-data-census.md`](../plans/2026-09-04-dji-log-untapped-data-census.md)

## Context

The 2026-09-04 census established, against seven real production logs, that
`dji-log-parser` hands the parser far more than
`flight-parser/src/dji.rs` keeps: per-point timestamps, RC link quality,
distance-from-home, the flight-mode/RTH timeline, photo/video events, gimbal
pointing, MSL altitude, battery current/mAh/cell balance, the human-readable
warning stream, and — one call deeper into the raw record stream — pilot GPS,
pack cycle count, component firmware, and failsafe configuration. All 210
production `dji_txt` flights are v14 logs with `frames_decoded=true` and their
originals are retained at `/data/uploads/flight_logs/<sha256>.txt`, so every
item is backfillable.

The operator asked for it to be captured into the database and made reachable
from the Flights menu, with utilisation ("reports, battery, maintenance")
explicitly deferred. That framing — **breadth of capture now, minimal
presentation, no consumers yet** — is what forces the storage decision, because
we are committing to persist a lot of new data before we know which parts will be
queried.

The constraint that dominates: `flights` is the subject of three prior OOM
incident ADRs. It already carries three heavy JSON columns (`gps_track`,
`telemetry`, `raw_metadata`, `backend/app/models/flight.py:47-49`), and ADR-0019
exists because SQLAlchemy loading them by default on a 500-row list query
OOM-killed the uvicorn worker into a crash-loop and made uploaded flights vanish
from the mission picker. That ADR's regression suite includes a coverage test
that deliberately forces a decision when a new large JSON column is added to
`Flight`. This change is exactly that trigger, and it arrives while an existing
hazard is still live: `reprocess_all_from_stored` does a bare
`select(Flight).where(...)` with no `defer()`
(`backend/app/routers/flight_library.py:1650-1659`), loading every JSON column of
every matching flight.

## Options considered

### A. Widen `flights.telemetry` + add a `flights.flight_details` JSON column

- **For:** no new table, no join, no relationship; the existing `/telemetry`
  read path already serves series.
- **Against:** adds a fourth heavy JSON column to the one table three OOM ADRs
  are about, widening the live `reprocess/all` footgun and every future
  `select(Flight)`. Protection would rest on remembering to `defer()` — a
  discipline that has already failed once in production.
- **Against:** `Flight.telemetry` is SQLAlchemy generic `JSON`, which emits
  Postgres `json`, not `jsonb`. Events stored there could never be GIN-indexed
  without an `ALTER TABLE … USING` rewrite of a large TOASTed column.

### B. A `flight_details` sidecar table, 1:1 with `flights` — typed columns for scalars, `JSONB` for groups

- **For:** extended data becomes opt-in **by construction**. The list, mission,
  report, and map paths never see it because they never join it — not because
  someone deferred it.
- **For:** typed scalars are directly filterable and aggregatable later (pack
  cycle count across flights, flights with an RTH, MSL by month) with plain
  b-tree indexes.
- **For:** `JSONB` from day one, so `events` can be GIN-indexed later with no
  column rewrite.
- **For:** the backfill can select `(Flight.id, Flight.source_file_hash)` as
  *columns* and never load the `Flight` entity — making "no headline metric
  moves" a structural property rather than a code-review promise.
- **Against:** one migration, one model, one relationship — and that
  relationship must be `lazy="noload"` or it silently recreates ADR-0019.

### C. Normalized child tables (`flight_events`, `flight_battery_samples`, `flight_phases`, …)

- **For:** real SQL over events and samples; no JSON path syntax.
- **Against:** at sample granularity it is a row explosion — the M4TD census log
  alone has 13,870 frames, so one series across 210 flights is ~2.9 M rows, built
  for a query nobody has asked for.
- **Against:** roughly six tables, migrations, and models before a single value
  reaches a screen — directly against the operator's "breadth of capture first,
  presentation minimal" framing.

## Decision

**Option B.** Extended per-flight log data is persisted in a new
`flight_details` table, 1:1 with `flights`, primary-keyed on `flight_id`
(`FK flights.id ON DELETE CASCADE`). Scalars a future query might filter or
aggregate on get typed, nullable, unit-suffixed columns per ADR-0032
conventions; structured and variable-length groups (`phases`, `events`,
`config`, `firmware`, `health`, `sd_card`, `serials`, `series`) are `JSONB`.
`schema_version`, `parser_version` and `generated_at` are carried on every row so
a "re-backfill everything produced by parser < X" sweep is a trivial query.

Binding consequences of that choice, each of which is part of the decision and
not an implementation detail:

1. **No new column is added to `flights`.** The ADR-0019 heavy-column set is
   unchanged.
2. **`Flight.details` is declared `lazy="noload"`**, matching the existing
   `battery_logs` relationship (`models/flight.py:58`). A test asserts the
   compiled `/flight-library` list query never references `flight_details`, with
   a control test proving the assertion is meaningful — the same structure as the
   existing ADR-0019 guard.
3. **No secondary index ships until a query needs one.** An index is write cost
   on a streaming-replicated primary, paid on every insert, for a read that does
   not exist yet. `JSONB` is what keeps the option cheap.
4. **The backfill never loads a `Flight` entity.** It selects `Flight.id` and
   `Flight.source_file_hash` as columns and writes only `flight_details`.
   Therefore `duration_secs`, `total_distance`, `max_altitude`, `max_speed`,
   `home_lat/lon`, `point_count`, `gps_track` and `raw_metadata` — the values
   ADR-0027 and ADR-0028 settled and migration `0004` restamped — **cannot** be
   disturbed: there is no object in the session to dirty. The existing
   `/reprocess/all` route is explicitly **not** reused, because its write set is
   precisely those columns (`flight_library.py:1699-1709`) and its selector
   (`point_count == 0 OR gps_track IS NULL`) would match none of the 210 anyway.
   Verified in production by an identical before/after `md5(string_agg(...))`
   checksum over those columns.
5. **Scalars are computed at full frame resolution; series are stored
   decimated** to 4,000 points on a shared `t_offset_s` time base. Maxima,
   minima, edge counts and integrals never see a decimated array. The Flight
   Details view reads series exclusively from `flight_details.series`, never from
   `flights.telemetry`, because the two blobs have different resolutions and must
   not be cross-indexed.
6. **The migration is idempotent** (ADR-0042): `0001_baseline_schema` builds
   fresh databases with `create_all` from the live models, so `0010` must no-op
   when the table already exists, exactly as `0009` guards its column.
7. **The parser contract stays backward compatible.** `ParsedFlight` gains one
   optional `details` field with `skip_serializing_if`; Litchi and Airdata emit
   byte-identical JSON, and the Rust compiler forces both call sites to be
   audited because they build struct literals.
8. **ADR-0029 / ADR-0031 remain in full force.** The Flight Details view presents
   the height limits and configuration recorded in the log as data. It does not
   compare them to anything, does not mention 400 ft or Part 107, and emits no
   compliance commentary — on this page or anywhere downstream.

## Rationale

The deciding argument is not ergonomics, it is blast radius. Option A's cost is
not the column — it is that every existing and future `select(Flight)` in the
codebase becomes slightly more dangerous, protected only by remembering to
`defer()`. That protection has already failed once here, in production, with a
crash-looping API worker and flights invisible in the mission picker. Option B
removes the failure mode instead of adding another instance of it: code that does
not join the table cannot be hurt by it.

Option C is the right shape for a system that knows its queries. This one does
not yet — the operator's own framing is "then later we can figure out where else
to pull that data in." Normalizing now is schema built on a guess, and the guess
would be paid for in six migrations before anything appears on screen. Option B
is the shape that defers that decision cheaply: `events` is the one group with a
plausible SQL future, it is `JSONB`, and it can be GIN-indexed in place or
promoted to a child table later with the stored JSONB as its source.

The backfill argument is what makes B decisively better rather than merely
tidier. The single hardest requirement in this work is that re-reading 210 logs
through a *new parser build* must not move a single number that ADR-0027 and
ADR-0028 established. Under option A the guarantee is behavioural — "the update
statement only assigns the new columns" — and one careless attribute assignment
in a later refactor breaks it silently. Under option B the guarantee is
structural: the `Flight` entity is never in the session.

## Consequences

- Extended log data is durable, queryable, and versioned, without making any
  existing flight query heavier. The ADR-0019/0020/0025 OOM family does not gain
  a fourth member.
- One join is needed for the Flight Details view. It is a single-row
  primary-key read; irrelevant.
- **A known, deliberate asymmetry:** because the backfill writes only
  `flight_details`, the 210 existing flights do **not** receive per-point
  timestamps in `flights.gps_track[].timestamp` or `flights.telemetry.timestamps`
  (those live on `flights` and are off-limits). New imports do. The Flight
  Details view is unaffected — it takes its time base from
  `flight_details.series.t_offset_s`, which every backfilled flight has — so the
  asymmetry is confined to the legacy `/telemetry` and `/replay` surfaces.
  Closing it requires rewriting `gps_track` on rows ADR-0027/0028 already
  settled, which is a separate, higher-risk change gated on a bit-identical
  before/after diff and explicit operator go-ahead (plan Phase 6, deferred).
- Three currently-always-null API fields become populated for new imports:
  `telemetry.signal_strength` (hard-coded `None` at `dji.rs:265`),
  `telemetry.distance_from_home` (`dji.rs:266`), and `telemetry.timestamps`
  (`dji.rs:120`). The `/telemetry` endpoint already serves all three and the
  frontend already types them permissively, so this is additive.
- The `SmartBatteryStatic` correction is a **shim we own**, not a fix. Working
  the census's four sample values, the observed transform is uniformly
  `raw >> 8` — *not* a byte swap, as the census characterized it: for the 32-bit
  `designed_capacity` the two differ, and the M30 sample's low byte is `0x02`,
  nonzero, which a clean endian swap of a well-formed field would not leave. That
  reads as a field-offset/alignment bug in the crate's struct decode. The shim is
  therefore plausibility-gated (`pack_values_plausible`) and flagged
  (`pack_values_shimmed`) so shimmed rows are distinguishable the day upstream
  fixes it.
- **Raw pilot GPS coordinates are not stored** — only derived aircraft-to-pilot
  distance scalars and a decimated distance series. The operational value asked
  for (VLOS distance) is fully captured; the raw form is personal location data
  about the PIC that would land in an R2-backed database (ADR-0041) read by
  client-facing report code. Recorded as an assumption open to reversal
  (plan OQ-2), but the default is deliberate.
- **Stick positions (`RC` / `RCDisplayField`) and Tier 2 `Unknown` record types
  are explicit non-goals** for this phase. ~30% of records in newer logs remain
  undecodable with 0.5.7 and no reverse-engineering is authorized.
- **A `dji-log-parser` version bump is out of scope and is not a dependency
  chore.** A newer crate could retire the shim and fix the
  `Unknown(178)/(137)/(139)` `ProductType` gap, but it can also change frame
  decoding and therefore the headline metrics on all 210 flights. It requires its
  own ADR with a full before/after metric diff.
- The inline `downsample` helper currently living inside `get_telemetry`
  (`flight_library.py:1999-2003`) is extracted to a shared service used by both
  endpoints. This is ADR-0032's standing finding applied prophylactically: that
  ADR's own conclusion is that the absence of a shared conversion layer is what
  lets a defect class recur, and a second copy-pasted downsampler would be the
  same mistake.
- **Failover & Resilience Guard:** additive nullable table via plain DDL —
  replication-safe over WAL, no port binding, no `pg_hba`, no connection-string
  change. Survives container recreation (the migration is idempotent and the
  fresh-DB path builds it from the live models). No blue-green or promotion
  impact; the backfill is an authenticated operator-initiated request that writes
  only to the new table, so a mid-backfill failover loses at most the in-flight
  batch and the idempotent selector picks it up on retry. No customer-facing
  service is affected during a failover.

## Status of implementation

Nothing is built. The phased build order, sizing, per-phase tests, version-bump
targets, deploy notes, risks and the seven open questions for the operator are in
`docs/plans/2026-09-04-flight-details-data-ingestion.md`. ROADMAP item **FP-1**
points at that plan.
