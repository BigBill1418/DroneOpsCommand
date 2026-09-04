# Flight Details — pull the untapped DJI log data into the DB and give it a page

**Status:** PLANNED, nothing built. Implementation plan for ROADMAP **FP-1**.
**Date:** 2026-09-04
**Decision record:** [ADR-0043](../adr/0043-flight-details-sidecar-table-for-extended-log-data.md)
**Input:** [`2026-09-04-dji-log-untapped-data-census.md`](2026-09-04-dji-log-untapped-data-census.md)
— the primary-source census of what the logs actually carry. This plan does not
re-derive it; it consumes it.

## Operator ask (Bill, 2026-09-04, verbatim)

> "we should think about a plan to make all this extra data more accessible and
> to make sure to pull it into the DB - even if that is just a 'Flight Details'
> link somewhere on a flight in the 'flights' menu - then later we can figure
> out where else to pull that data in to utilize it."

Read literally, and that's how it's scoped:

1. **Capture breadth into the database, durably.** Every Tier 0 / Tier 1 item
   the census found, not a curated subset.
2. **One "Flight Details" view** reachable from the Flights menu. Minimal
   presentation — grouped facts, no chart polish.
3. **Leave the door open.** Reports, battery health, maintenance triggers are
   explicitly *later*. This phase must not close any of those doors, and must
   not pre-build any of them.

Corollary that shapes every decision below: **breadth of capture beats depth of
presentation**, and **nothing already trusted may move.**

---

## 1. Data model

### 1.1 Options considered

**(a) Widen `flights.telemetry` with new series + add a `flights.flight_details`
JSON column for scalars/summary/events.**

- **Pro:** no new table, no join, no relationship to configure. Reuses the
  existing `/telemetry` read path for the series.
- **Con — decisive:** `flights` is already the subject of three OOM ADRs
  (ADR-0019 list defer, ADR-0020 report geo buffer, ADR-0025 mission detail).
  It carries three heavy JSON columns today (`gps_track`, `telemetry`,
  `raw_metadata`, `backend/app/models/flight.py:47-49`). Adding a fourth means
  every `select(Flight)` in the codebase becomes a little more dangerous, and
  the ADR-0019 coverage test (`backend/tests/test_flight_library_list_defers_heavy_columns.py`)
  exists precisely to force a decision when that happens. The concrete hazard is
  not hypothetical: `reprocess_all_from_stored` does a bare
  `select(Flight).where(...)` over every matching DJI flight
  (`flight_library.py:1650-1659`) — full entity load, all JSON columns, no
  defer. A fourth heavy column widens an existing footgun.
- **Con:** `Flight.telemetry` is SQLAlchemy generic `JSON`, which emits
  Postgres `json`, not `jsonb`. Events stored there can never be GIN-indexed
  without an `ALTER TABLE … USING` rewrite of a multi-hundred-MB TOASTed column.

**(b) A new `flight_details` table, 1:1 with `flights` — typed columns for
scalars, `JSONB` for series/events/groups.**

- **Pro:** loading extended data becomes *opt-in*. The flights list, the mission
  detail path, the report path, the map path, and `reprocess/all` never see it —
  by construction, not by remembering to `defer()`. ADR-0019's class of defect
  cannot recur here.
- **Pro:** typed scalars are directly queryable later — "battery packs by cycle
  count across flights", "flights with an RTH", "max MSL by month" — with plain
  b-tree indexes, no JSONB path extraction.
- **Pro:** `JSONB` from day one, so a GIN index on `events` is available when
  event search becomes real, with no rewrite.
- **Pro — backfill:** the backfill can select `(Flight.id, Flight.source_file_hash)`
  as *columns*, never load the `Flight` entity, and therefore structurally
  cannot dirty a headline metric (see §3.2).
- **Con:** one join for the details page (irrelevant — it is a single-row read),
  one migration, one relationship that must be declared `lazy="noload"` or it
  silently recreates the ADR-0019 failure mode.

**(c) Normalized child tables — `flight_events`, `flight_battery_samples`,
`flight_phases`, …**

- **Pro:** real SQL over events; no JSON path syntax.
- **Con — decisive at sample granularity:** the M4TD census log alone has 13,870
  frames. Sample-level normalization across 210 flights is ~2.9 M rows for one
  series, and there is no read path today that needs per-sample SQL. That is
  schema built for a query nobody has asked for.
- **Con:** ~6 tables, ~6 migrations, ~6 models to land before a single value
  reaches the screen. Directly against "breadth of capture first."
- **Partially deferred, not rejected:** `events` is the one group with a
  plausible future SQL need ("show me every flight with a compass error").
  Option (b) keeps that door open — `events` is `JSONB`, so it can be GIN-indexed
  in place, or promoted to a child table later with the JSONB as the source.

### 1.2 Recommendation — option (b)

**A `flight_details` sidecar table, 1:1 with `flights`, primary-keyed on
`flight_id`.** Typed nullable columns for every scalar a future query might
filter or aggregate on; `JSONB` groups for the structured/variable-length data
(phases, events, config, firmware, health, sd_card, series).

Rationale in one line: it is the only option where "add a lot of new data" does
not also mean "make every existing flight query heavier," and the repo has
already paid for that lesson three times.

### 1.3 Concrete schema

Units follow ADR-0032 conventions throughout: **metres, m/s, volts, amps, °C,
degrees, mAh, Wh, seconds, Hz.** Every column name carries its unit suffix. All
columns except `flight_id`, `schema_version`, `generated_at` are **nullable** —
a Litchi/Airdata flight has no row at all, and a DJI log whose frames failed to
decode produces a row that is mostly NULL.

```
flight_details
  flight_id                     UUID       PK, FK flights.id ON DELETE CASCADE
  schema_version                SMALLINT   NOT NULL DEFAULT 1
  parser_version                VARCHAR(32)          -- flight-parser Cargo version
  generated_at                  TIMESTAMP  NOT NULL  -- naive UTC, repo convention

  -- decode provenance
  frame_count                   INTEGER
  record_count                  INTEGER
  frame_hz_est                  DOUBLE PRECISION     -- Hz
  first_frame_at                TIMESTAMP            -- naive UTC
  last_frame_at                 TIMESTAMP

  -- altitude (MSL/VPS — AGL stays on flights.max_altitude, untouched)
  max_altitude_msl_m            DOUBLE PRECISION
  min_altitude_msl_m            DOUBLE PRECISION
  home_altitude_msl_m           DOUBLE PRECISION
  max_vps_height_m              DOUBLE PRECISION
  take_off_altitude_raw         DOUBLE PRECISION     -- UNITS UNCONFIRMED, see OQ-1
  take_off_altitude_units       VARCHAR(16)          -- 'unconfirmed' until OQ-1 closes

  -- range / rates
  max_distance_from_home_m      DOUBLE PRECISION
  max_climb_rate_ms             DOUBLE PRECISION     -- positive = climbing
  max_descent_rate_ms           DOUBLE PRECISION     -- positive magnitude, descending
  header_max_vertical_speed_ms  DOUBLE PRECISION

  -- phases
  takeoff_count                 SMALLINT
  landing_count                 SMALLINT
  rth_count                     SMALLINT
  sport_mode_seconds            DOUBLE PRECISION
  waypoint_mode_seconds         DOUBLE PRECISION
  manual_mode_seconds           DOUBLE PRECISION

  -- camera
  photo_count                   INTEGER              -- camera.is_photo RISING edges
  header_capture_num            INTEGER              -- header, for cross-check
  video_seconds                 DOUBLE PRECISION
  header_video_time_s           DOUBLE PRECISION

  -- RC link (raw crate scale; 0-90 observed, 104 seen once — NOT rescaled)
  rc_downlink_min               SMALLINT
  rc_downlink_avg               DOUBLE PRECISION
  rc_downlink_max               SMALLINT
  rc_uplink_min                 SMALLINT
  rc_uplink_avg                 DOUBLE PRECISION
  rc_uplink_max                 SMALLINT
  rc_zero_downlink_frames       INTEGER
  rc_disconnect_events          SMALLINT
  ofdm_signal_avg_pct           DOUBLE PRECISION     -- video link, separate from RC

  -- battery (per-flight, from frames)
  battery_current_max_a         DOUBLE PRECISION
  battery_energy_wh             DOUBLE PRECISION     -- ∫ V·I dt at full frame res
  battery_discharge_mah         DOUBLE PRECISION     -- ∫ I dt
  battery_cell_count            SMALLINT
  battery_cell_deviation_max_v  DOUBLE PRECISION
  battery_temp_min_c            DOUBLE PRECISION
  battery_temp_max_c            DOUBLE PRECISION
  battery_full_capacity_mah     DOUBLE PRECISION
  battery_current_capacity_mah  DOUBLE PRECISION

  -- pack (Tier 1, SmartBatteryStatic, shim-corrected — see §2.4)
  pack_cycle_count              INTEGER
  pack_designed_capacity_mah    INTEGER
  pack_full_charge_voltage_v    DOUBLE PRECISION
  pack_values_shimmed           BOOLEAN              -- true = >>8 correction applied
  pack_values_plausible         BOOLEAN              -- false = shimmed value out of range, do not display

  -- config snapshot in force for this flight
  height_limit_m                DOUBLE PRECISION
  go_home_height_m              DOUBLE PRECISION
  max_allowed_height_m          DOUBLE PRECISION
  is_beginner_mode              BOOLEAN

  -- identity
  aircraft_sn_full              VARCHAR(32)          -- 20-char, vs header's 16
  app_platform                  VARCHAR(32)          -- Android / DJIFly / Linux(Goggles)

  -- pilot / VLOS (derived only — see OQ-2)
  pilot_sample_count            INTEGER
  pilot_max_distance_m          DOUBLE PRECISION
  pilot_avg_distance_m          DOUBLE PRECISION

  -- rollups so the UI can badge without opening a JSONB
  event_count                   INTEGER
  warning_event_count           INTEGER
  anomaly_flag_count            SMALLINT

  -- JSONB groups
  phases    JSONB   -- [{state, seconds, frames}]  from osd.flyc_state
  events    JSONB   -- [{t_offset_s, last_t_offset_s, kind, severity, message, count, garbled}]
  config    JSONB   -- failsafe_action, go_home_mode, obstacle_avoidance, mvo, + long tail
  firmware  JSONB   -- [{component, version}]
  health    JSONB   -- {is_vibrating: N frames, is_compass_error: N, ...}
  sd_card   JSONB   -- {min_remain_capacity_mb, min_remain_photo_num, min_remain_video_s}
  serials   JSONB   -- {rc_sn, camera_sn, battery_sn, component_serials[]}
  series    JSONB   -- {t_offset_s: [], <name>: []} — decimated, see §2.5
  extra     JSONB   -- forward-compat catch-all; nullable, normally NULL
```

**Indexes at P0: the primary key only.** No secondary index ships until a query
exists that needs it — an index is write cost on a streaming-replicated primary,
paid on every insert, for a read nobody has written yet. The `JSONB` choice is
what keeps the option open (a GIN on `events` is a later one-liner; a GIN on a
`json` column is not possible at all).

**Relationship declaration is load-bearing:**

```python
# backend/app/models/flight.py
details = relationship("FlightDetails", back_populates="flight",
                       uselist=False, lazy="noload", cascade="all, delete-orphan")
```

`lazy="noload"` matches the existing `battery_logs` relationship
(`flight.py:58`). `selectin` here would drag a JSONB blob into every
`/flight-library` list row and reproduce ADR-0019 exactly. A test asserts the
compiled list query does not reference `flight_details` (§6, P0).

### 1.4 Migration

`backend/alembic/versions/0010_flight_details.py`, `down_revision =
"0009_mission_dl_email_sent_at"`.

**Must be idempotent** — ADR-0042 / the 2026-08-22 fresh-install incident.
Migration `0001_baseline_schema` builds fresh databases with `create_all` from
the **live models**, so on a fresh DB the table already exists by the time 0010
runs. Guard exactly as `0009` does (`flight_library`-adjacent pattern,
`0009_mission_dl_email_sent_at.py:37-40`):

```python
conn = op.get_bind()
if "flight_details" in sa.inspect(conn).get_table_names():
    return  # fresh DB: 0001's live-models create_all already built it
```

Revision id `0010_flight_details` is 19 chars (≤ 32 — the constraint 0007/0009
call out). Additive, no backfill inside the migration, no data rewrite —
replication-safe plain DDL via WAL. Survives container recreation and standby
promotion; no port, pg_hba, or connection-string change (Failover & Resilience
Guard: clean on all five questions).

---

## 2. Parser contract (`flight-parser` → backend JSON)

### 2.1 Shape

One new optional field on `ParsedFlight` (`flight-parser/src/main.rs:19-40`):

```rust
#[serde(skip_serializing_if = "Option::is_none")]
pub details: Option<FlightDetails>,
```

Litchi (`litchi.rs:169`) and Airdata (`airdata.rs:208`) build `ParsedFlight`
struct literals, so adding the field is a **compile error** in both until
`details: None` is added — the compiler enforces the audit instead of a reviewer
having to. Their JSON output is byte-identical afterwards (`skip_serializing_if`).
Backend treats an absent/`null` `details` as "no `flight_details` row," so both
formats are untouched end to end.

`FlightDetails` mirrors §1.3: typed scalars flattened, plus
`phases/events/config/firmware/health/sd_card/serials/series` as
`serde_json::Value`.

### 2.2 Tier 0 — from `log.frames()` (already decoded, currently dropped)

The parser already iterates every frame (`dji.rs:139-207`). These are additional
accumulators inside that same loop — no second decode, no extra memory beyond
the accumulators and the (decimated) series buffers.

| Group | Frame source | New output |
|---|---|---|
| Time base | `frame.custom.date_time` | `series.t_offset_s`, `first/last_frame_at`, `frame_hz_est`. **Also** fills the currently-empty `TelemetryData.timestamps` (`dji.rs:120` — `let timestamps = Vec::new();` never gets pushed to) and `TrackPoint.timestamp` for **new imports only** (see §3.3 for why backfilled flights don't get this). |
| Link quality | `rc.downlink_signal`, `rc.uplink_signal` | `rc_downlink_*`, `rc_uplink_*`, `rc_zero_downlink_frames`, `series.rc_downlink`, `series.rc_uplink`. Also fills `TelemetryData.signal_strength` (hard-coded `None` at `dji.rs:265`, which is why `/telemetry` has always served `signal_strength: null`). |
| Distance from home | `home.latitude/longitude` vs `osd` | `max_distance_from_home_m`, `series.distance_from_home_m`. Also fills `TelemetryData.distance_from_home` (`dji.rs:266`). |
| Phase timeline | `osd.flyc_state`, `osd.flight_action`, `osd.flyc_command` | `phases` histogram, `takeoff_count`, `landing_count`, `rth_count`, `*_mode_seconds`; state transitions emitted as `events` of kind `mode`. |
| Camera | `camera.is_photo` rising edges, `camera.is_video`, `sd_card_state` | `photo_count`, `video_seconds`, `sd_card`. |
| Gimbal | `gimbal.pitch/roll/yaw`, `is_stuck`, `*_at_limit` | `series.gimbal_pitch_deg`, `series.gimbal_yaw_deg`; `is_stuck` frames into `health`. |
| Attitude / vertical | `osd.pitch/roll/yaw`, `osd.z_speed` | `max_climb_rate_ms`, `max_descent_rate_ms`, `series.z_speed_ms`, `series.aircraft_yaw_deg`. |
| MSL / VPS | `osd.altitude`, `osd.vps_height`, `home.altitude` | `max/min_altitude_msl_m`, `home_altitude_msl_m`, `max_vps_height_m`, `series.altitude_msl_m`, `series.vps_height_m`. |
| Battery detail | `battery.current`, `current/full_capacity`, `cell_voltages[]`, `cell_voltage_deviation`, `min/max_temperature` | `battery_current_max_a`, `battery_energy_wh`, `battery_discharge_mah`, `battery_cell_*`, `battery_temp_*`, `series.battery_current_a`. **`battery_discharge_mah` is the value that fills the always-null `BatteryData.discharge_mah`** (`dji.rs:279`) and therefore `battery_logs.discharge_mah` (`models/battery.py:52`) — but only at P5, see §6. |
| Safety flags | `is_vibrating`, `is_compass_error`, `is_motor_blocked`, `voltage_warning`, … | `health` frame-counts, `anomaly_flag_count`. |
| Config | `home.height_limit`, `go_home_height`, `max_allowed_height`, `is_beginner_mode`, `go_home_mode` | `height_limit_m`, `go_home_height_m`, `max_allowed_height_m`, `is_beginner_mode`, `config`. |
| Events | `app.tip`, `app.warn` | `events` (see §2.6). |
| Header extras | `max_vertical_speed`, `capture_num`, `video_time`, `take_off_altitude`, `app_platform` | corresponding columns; `take_off_altitude` stored **raw** with `take_off_altitude_units='unconfirmed'`. |

### 2.3 Tier 1 — from the raw record stream (**a second pass, and the plan's
largest unknown**)

`dji.rs:104` calls `log.frames(keychains)` and nothing else. `AppGPS`,
`SmartBatteryGroup::SmartBatteryStatic`, `Firmware`, `Camera`, `MCParams`,
`OFDM`, `ComponentSerial` never become a `Frame` and are unreachable today.

**Unverified and it must be verified first:** the exact `dji-log-parser` 0.5.7
record-access API — whether it is `log.records(keychains)`, its return type,
whether it consumes the keychains (forcing a re-fetch from DJI's API for the
frames pass), and its peak memory. The census says "one `records()` call away";
that is a reasonable read of the crate but was not exercised. **P2 opens with a
spike that answers exactly this before any other P2 work** (§6, P2-a). If the
answer is bad — a second DJI keychain round-trip per flight, or a memory
profile that blows the parser's 256 MiB cap — P2 stops and gets re-planned; P0/P1
already deliver the bulk of the value without it.

Per-record outputs:

| Record | → |
|---|---|
| `AppGPS` | `pilot_sample_count`, `pilot_max_distance_m`, `pilot_avg_distance_m`, `series.pilot_distance_m`. **Derived only — raw pilot lat/lon is NOT stored.** See OQ-2. |
| `SmartBatteryStatic` | `pack_cycle_count`, `pack_designed_capacity_mah`, `pack_full_charge_voltage_v` via the §2.4 shim. |
| `Firmware` | `firmware[]`. |
| `Camera` | `sd_card` (remaining capacity/photos/video timer), `record_time` cross-check against the frame-derived `video_seconds`. |
| `MCParams` | `config.failsafe_action`, `config.obstacle_avoidance`, `config.mvo`. |
| `OFDM` | `ofdm_signal_avg_pct`, `series.ofdm_signal_pct`. |
| `ComponentSerial` | `aircraft_sn_full`, `serials.component_serials[]`. |
| `RC` / `RCDisplayField` | **Deferred.** Stick-position→pilot-input-intensity is a modelling exercise with no consumer yet; capturing 4,147 stick samples per flight to display nothing is storage without a reader. Noted in the ADR consequences as an explicit non-goal for this phase. |

Tier 2 (`Unknown` record types, ~30% of records in newer logs) is **out of
scope**, unchanged from the census's conclusion.

### 2.4 The `SmartBatteryStatic` shim — corrected characterization

The census calls these values "big-endian-wrong." Working the four sample values
it recorded, the transform is uniformly **`raw >> 8`**, not a byte swap:

| Field | Raw | Hex | `>> 8` | Census expected |
|---|---|---|---|---|
| `loop_times` (M4TD) | 5888 | `0x1700` | 23 | 23 |
| `loop_times` (M30) | 2304 | `0x0900` | 9 | 9 |
| `designed_capacity` (M4TD) | 1899520 | `0x1CFC00` | 7420 | 7420 mAh (pack spec) |
| `designed_capacity` (M30/TB30) | 1505282 | `0x16F002` | 5880 | 5880 mAh (pack spec) |

For a `u16` like `loop_times`, `>> 8` and a byte swap are indistinguishable. For
the 32-bit `designed_capacity` they are not, and the M30 low byte is `0x02` —
**nonzero**. A clean endian swap of a well-formed field would leave zero there.
A nonzero low byte plus a uniform 8-bit shift reads as a **field-offset /
alignment bug in the crate's struct decode**, not an endianness bug. That
distinction matters: an alignment bug is more likely to be already fixed
upstream, and more likely to differ per record layout.

**Decision:** implement the shim as `raw >> 8` (not `swap_bytes()`), behind a
plausibility gate — cycles in `0..=3000`, designed capacity in `1000..=30000`
mAh, full-charge voltage in `10.0..=60.0` V. Out-of-range → store the shimmed
value anyway but set `pack_values_plausible = false`, and the UI does not display
it. `pack_values_shimmed` records that the correction was applied, so the day the
crate fixes it upstream we can tell shimmed rows from native ones. **P2 begins by
checking the upstream changelog** (§7 R-4) — if 0.6.x decodes these correctly,
the shim becomes a version-gated no-op instead of a permanent workaround.

### 2.5 Series and downsampling policy

Two rules, and the ordering between them is the whole point:

1. **Every scalar is computed at FULL frame resolution**, before any decimation.
   Maxima, minima, edge counts, and the `∫V·I dt` / `∫I dt` integrals never see a
   decimated array. Decimating first and then taking a max is how a peak gets
   quietly lost.
2. **Series are stored decimated** to `SERIES_MAX_POINTS = 4000`, using the same
   index-stride algorithm the backend already uses
   (`flight_library.py:1999-2003`). 4,000 is 2× the `/telemetry` endpoint's
   default `max_points` and well under its 10,000 cap, so no read path is ever
   resolution-starved by the stored form.

`series.t_offset_s` (seconds since the first frame) is emitted **at the same
decimated indices as every other series in the blob**, so all detail series
share one time base and are plottable against each other without index games. It
is also the reason the Flight Details page reads its series from
`flight_details.series` and never from `flights.telemetry` — the two blobs have
different resolutions and must not be cross-indexed. Stated plainly in the ADR
consequences.

Storage estimate (assumption, not measured): 13 series × 4,000 f64 rendered as
JSON text ≈ 400 KB raw per flight, TOAST-compressed to roughly **60–150 KB**.
Over 210 flights that is **~15–30 MB one-time**, plus ~100 KB per new flight.
Immaterial against the existing `gps_track`/`telemetry` footprint (ADR-0019
measured 33–44 MB *compressed* across the top 500 flights). OQ-3 asks Bill to
confirm the 4,000 cap; raising it to full resolution is a constant-factor change
(~3.5× on the M4TD) and nothing else.

### 2.6 Event extraction and the garbled-string rule

The census found ~20% of `app.tip` / `app.warn` strings arrive with a **garbled
prefix, intact suffix**, from the crate's decrypt/append path. The rule:

1. **Trim** leading bytes that are not printable ASCII or valid UTF-8, and any
   leading run before the first capital letter that begins a word-boundary run of
   ≥ 3 ASCII letters. Set `garbled: true` when anything was trimmed.
2. **Dedupe on the cleaned string**, not the raw one. This is what turns the
   M4TD's 18 separate "Remote controller disconnected. Adjust antennas" strings
   into **one** event record with `count: 18`, `t_offset_s` = first occurrence,
   `last_t_offset_s` = last. Without this, `events` is a spam log; with it, it is
   the mission-report events section.
3. **Never reconstruct** a message from a partial. If cleaning leaves fewer than
   8 characters, emit `kind: "unparsed"` with the cleaned remnant and
   `garbled: true` — do not guess at the original. (ADR-0028's truthfulness
   posture: an unknown stays unknown.)
4. Severity from the source: `app.tip` → `info`, `app.warn` → `warning`,
   `AppSeriousWarn` (Tier 1) → `serious`. Mode transitions → `info`, kind `mode`.

Raw strings are **not stored** — cleaned message + `garbled` flag only. Keeping
both roughly doubles the events blob to preserve bytes nobody will read.

### 2.7 Backward compatibility summary

- Litchi / Airdata: `details: None`, output byte-identical, compiler-enforced.
- Existing DJI output fields: **unchanged in value**. The only in-place changes
  to existing fields are three currently-always-empty ones getting populated —
  `TelemetryData.timestamps` (`dji.rs:120`), `TelemetryData.signal_strength`
  (`dji.rs:265`), `TelemetryData.distance_from_home` (`dji.rs:266`). The
  `/telemetry` endpoint already serves all three (`flight_library.py:2007-2015`)
  and the frontend already types `telemetry` as `Record<string, number[]>`
  (`FlightReplay.tsx:64`), so nothing downstream breaks — those keys go from
  `null` to arrays.
- `duration_secs`, `total_distance`, `max_altitude`, `max_speed`, `home_lat/lon`,
  `point_count`, `gps_track` geometry: **untouched by every phase of this plan.**

---

## 3. Backfill / reprocess for the 210 retained logs

### 3.1 What exists today, and why it is the wrong tool

`FlightDataTab.tsx` surfaces the reprocess mechanism (`FlightDataTab.tsx:522-560`):
a "Need Reprocess" stat from `GET /flight-library/reprocess/status`
(`flight_library.py:1589-1627`), a "RE-PROCESS N FLIGHTS" button hitting
`POST /flight-library/reprocess/all` with a 600 s client timeout
(`FlightDataTab.tsx:215`), and a "MANUAL RE-UPLOAD" fallback to
`POST /flight-library/reprocess`. (The "Restart (Home)" / "Skip +5%" controls
named in some notes are **not** reprocess controls — they are playback buttons in
`FlightReplay.tsx:534,562`. Different surface.)

**`/reprocess/all` must not be reused here**, for two independent reasons:

1. **Its selector doesn't match.** It targets flights with
   `point_count == 0 OR point_count IS NULL OR gps_track IS NULL`
   (`flight_library.py:1652-1658`). All 210 dji_txt flights have decoded frames
   and tracks, so it would select **zero** of them.
2. **Its write set is exactly what must not move.** It assigns
   `duration_secs`, `total_distance`, `max_altitude`, `max_speed`,
   `home_lat/lon`, `point_count`, `gps_track`, `telemetry`, `raw_metadata`
   (`flight_library.py:1699-1709`). Those are the values ADR-0027 (header
   airtime) and ADR-0028 (outlier gate, sanity bound) settled and that
   migration `0004_dji_duration_name_restamp` already restamped. Re-deriving
   them from a *different parser build* is a silent-regression generator.

### 3.2 The new backfill, and how "nothing moves" is guaranteed

`POST /api/flight-library/details/backfill?limit=25&force=false`
(auth: `Depends(get_current_user)`, same as every neighbouring route).

```
1. select(Flight.id, Flight.source_file_hash)             # COLUMNS, not the entity
     .outerjoin(FlightDetails, FlightDetails.flight_id == Flight.id)
     .where(Flight.source == "dji_txt",
            Flight.source_file_hash.is_not(None),
            FlightDetails.flight_id.is_(None))            # force=true drops this clause
     .limit(limit)
2. for each: _get_stored_file_path(hash)  -> skip if absent
3. stream the stored original to the parser from an open file handle
     (the ADR-0028 M5 pattern at flight_library.py:1676-1683 — never read_bytes())
4. take parsed["details"];  skip (not error) when absent
5. async with db.begin_nested():                          # ADR-0028 C2 savepoint
       upsert FlightDetails(flight_id=..., **details)
6. return {processed, written, skipped_no_file, skipped_no_details, remaining, errors}
```

**The guarantee is structural, not disciplinary.** The `Flight` ORM entity is
never loaded into the session, so there is no `Flight` object to dirty and no
attribute assignment is possible. It is not "we remembered not to write
`duration_secs`" — there is nothing there to write to. Step 1 is also the
ADR-0019/0025 lesson applied literally: a column-select over 210 rows loads two
scalars each, not 210 × (gps_track + telemetry + raw_metadata).

**Idempotency:** the `FlightDetails.flight_id IS NULL` clause means a second run
processes zero rows. `force=true` re-parses and upserts (needed after a parser
version bump); the `parser_version` / `schema_version` columns make a
"re-backfill everything produced by parser < X" query trivial later.

**OOM:** peak memory per iteration is one streamed 20 MB log on the backend side
(never fully resident — file handle streamed to httpx) and one details payload.
The backend is on a 1536 MiB cgroup (ADR-0025 context); this is bounded and
sequential. The real memory question is **parser-side** and is the P2 gate
(§7 R-2). `limit=25` default keeps a single request at roughly 1–2 minutes at an
observed ~2–4 s/parse, so the UI loops on `remaining > 0` rather than holding one
600 s request open like `/reprocess/all` does.

**Concurrency:** sequential, one flight at a time, mirroring `/reprocess/all`.
No parallel fan-out — the parser is a single 256 MiB container and the DJI
keychain API is a shared external dependency.

**Prod verification of the no-move guarantee** (run on BOS-HQ against
`droneops-standby-db`, before and after, output quoted into `PROGRESS.md`):

```sql
SELECT md5(string_agg(
         id::text||'|'||duration_secs||'|'||total_distance||'|'||
         max_altitude||'|'||max_speed||'|'||point_count, ',' ORDER BY id))
FROM flights WHERE source = 'dji_txt';
```

Identical hash before and after, or the backfill is reverted and re-planned.

### 3.3 The one asymmetry, stated plainly

Because the backfill writes **only** `flight_details`, the 210 existing flights
do **not** get per-point timestamps written into `flights.gps_track[].timestamp`
or `flights.telemetry.timestamps` — those live on `flights` and are therefore
off-limits. New imports (post-P1) **do** get them.

Result: for a window, new flights have a time-stamped `gps_track` and old ones
don't. This is deliberate — closing it means rewriting `gps_track` on 210 rows
that ADR-0027/0028 already settled, which is a distinct, higher-risk change that
deserves its own gate.

Mitigation so it is invisible where it matters: **the Flight Details page reads
its time base and every series from `flight_details.series` only** (which every
backfilled flight has). The asymmetry is confined to the legacy
`/telemetry` + `/replay` surfaces.

Closing it is **FP-1 Phase 6, deferred** (§6): a `gps_track`/`telemetry`
re-stamp gated on a before/after diff proving `duration_secs`,
`total_distance`, `max_altitude`, `max_speed`, `point_count` and every track
coordinate are bit-identical, with only `timestamp` keys added. Not in this
phase, and it needs Bill's explicit go-ahead.

---

## 4. API

### 4.1 `GET /api/flight-library/{flight_id}/details`

- **Auth:** `_user: User = Depends(get_current_user)` — identical to
  `get_flight` / `get_telemetry` / `get_track` (`flight_library.py:1972,1987,2024`).
- **Params:** `max_points: int = Query(2000, le=10000)` — same defaults and cap
  as `/telemetry` (`flight_library.py:1985`); `include_series: bool = True`.
- **404** when the flight does not exist. **200 with `{"details": null,
  "eligible": true|false}`** when the flight exists but has no details row —
  `eligible` is `source == "dji_txt"`, and drives the page's empty state. Not a
  404: "this flight has no extended data yet" is a normal state with a remedy,
  not an error.
- **Response:** scalars flattened, plus `phases`, `events`, `config`,
  `firmware`, `health`, `sd_card`, `serials`, and `series` (omitted entirely when
  `include_series=false`).
- **Downsampling:** the `downsample` closure currently defined inline inside
  `get_telemetry` (`flight_library.py:1999-2003`) is **extracted** to
  `backend/app/services/telemetry_downsample.py` and used by both endpoints.
  This is the ADR-0032 lesson applied prophylactically — that ADR's standing
  finding is that "the absence of a shared units/columns module is the standing
  risk," and a second copy-pasted downsampler is the same defect class.

### 4.2 `POST /api/flight-library/details/backfill` — §3.2.

### 4.3 `GET /api/flight-library/details/status`

Counts for the Settings surface: `{total_dji, with_details, without_details,
without_details_with_stored_file, parser_versions: {…}}`. All `COUNT(*)` /
`GROUP BY` — no JSON loaded. Mirrors the existing `/reprocess/status` shape so
`FlightDataTab` gets a second card that reads the same way.

### 4.4 How the Flights list gets a link without loading heavy columns

**Recommendation: it doesn't need one.** The "Flight Details" entry is shown
whenever `source === 'dji_txt'` — a field `FlightResponse` already carries
(`schemas/flight.py:65`). Zero new columns, zero new joins, zero risk to the
list query that ADR-0019 exists to protect. Flights without a details row land on
the page's empty state, which links straight to Settings → Flight Data to run the
backfill — **making the backfill discoverable is a feature, not a consolation.**

Considered and deferred: a `has_details: bool` on `FlightResponse`, via an
`exists()` correlated subquery or a `column_property`. It is genuinely cheap (an
index-only probe on a unique PK), but a `column_property` attaches to *every*
`select(Flight)` in the codebase — including the report, mission, and map paths —
to save one empty-state render. Not worth it until the empty state is actually
common enough to annoy.

---

## 5. Frontend

### 5.1 Where it hangs off the Flights menu

The Flights surface already exists: nav item `Flights → /flights`
(`AppShell.tsx:41`), page `Flights.tsx`, a right-hand detail `Drawer` opened by
clicking a row (`Flights.tsx:771`), a per-row kebab `Menu`
(`Flights.tsx:808-829`), and a precedent for a per-flight sub-page —
`/flights/:id/replay` (`App.tsx:125`, lazy-loaded at `App.tsx:50`, launched from
the drawer's "FLIGHT REPLAY" button at `Flights.tsx:888-899`).

**Follow that precedent exactly. No new top-level nav item** — AppShell already
carries 12, and this is a per-flight view, not a section.

1. **New route** `/flights/:id/details` → `FlightDetails.tsx`, lazy, added
   alongside the replay route in `App.tsx`.
2. **Primary entry:** a `FLIGHT DETAILS` button in the drawer, immediately above
   the existing `FLIGHT REPLAY` button, same Mantine styling (cyan, `light`,
   `fullWidth`, Bebas Neue + 2px letter-spacing). Rendered when
   `detailFlight.source === 'dji_txt'`.
3. **Secondary entry:** a `Flight details` item at the top of the per-row kebab
   menu, above `Edit` (`Flights.tsx:813`), so it is reachable without opening the
   drawer.

### 5.2 What the page shows (P4 scope)

Mantine `Card`s in the established dark/cyan style (`#0e1117` on `#1a1f2e`
borders, Bebas Neue headings, Share Tech Mono for data — `Flights.tsx:905-1000`
is the pattern to copy). Sections, in this order:

| Section | Content |
|---|---|
| **Summary** | Frames/records decoded, frame rate, parser version, MSL max/min + home MSL, max VPS, max distance from home, max climb/descent. AGL max stays on the existing drawer — not duplicated here. |
| **Flight phases** | One horizontal stacked bar (plain CSS flex, no chart library) + a table of state → seconds → % . Takeoff/landing/RTH counts as badges. |
| **Camera** | Photo count (with the header `capture_num` cross-check shown when they disagree — the M4TD census case: 5 edges vs header 4), video seconds, SD card remaining at landing. |
| **Battery** | Start/end/min V (existing), plus max current, energy Wh, discharge mAh, cell deviation, temp min/max, pack cycle count + designed capacity (**hidden when `pack_values_plausible = false`**). |
| **Link quality** | Downlink/uplink min/avg/max, frames at zero downlink, disconnect count, OFDM video-link average. Colour by band, no chart. |
| **Events & warnings** | Table: `t_offset_s` (as mm:ss), severity badge, message, count. A `garbled` row is marked with a subtle indicator so a mangled string never reads as a clean fact. |
| **Config** | Height limit in force, go-home height, max allowed height, beginner mode, failsafe action, obstacle avoidance / MVO. **No commentary, no comparison to any limit** — ADR-0029 is in force: this is a data view, and the page must never editorialize about altitude or Part 107. |
| **Firmware & serials** | Component firmware table, full 20-char aircraft SN, RC/camera/battery SNs. |
| **Pilot position / VLOS** | Sample count, max and average aircraft-to-pilot distance. **Coordinates are never shown** (they are never stored — OQ-2). Section hidden entirely for Goggles-generated logs (Avata 2 / FPV have no `AppGPS`). |
| **Empty state** | When `details == null && eligible`: "No extended data for this flight yet" + a link to Settings → Flight Data. When `!eligible`: "Extended data is available for DJI logs only." |

### 5.3 Explicitly deferred (do not build in P4)

Chart polish and any charting library; map overlays (link-quality-coloured
track, gimbal footprint); mission-report integration; auto-updating the
`batteries` table from `pack_cycle_count`; maintenance-record triggers from
`health` flags; event→ntfy alerting; stick-position/pilot-input analysis;
cross-flight comparison views. Each is a "later we can figure out where else to
pull that data in" item, and each is a separate decision.

---

## 6. Build order, sizing, tests, versions, deploy

Sizing is calendar-days of focused work, not elapsed time.

| Phase | Scope | Size | App ver | Parser ver |
|---|---|---|---|---|
| **P0** | Schema + read path | S (~0.5 d) | 2.82.0 | — |
| **P1** | Tier 0 parser pass | M (~1.5 d) | 2.83.0 | 1.2.0 |
| **P2** | Tier 1 records pass | M–L (~2 d) | 2.84.0 | 1.3.0 |
| **P3** | Backfill | S–M (~0.5 d) | 2.85.0 | — |
| **P4** | Flight Details UI | M (~1.5 d) | 2.86.0 | — |
| **P5** | Battery wiring (gated) | S (~0.5 d) | 2.87.0 | — |
| **P6** | `gps_track` re-stamp | — | **DEFERRED**, needs operator go-ahead | |

**Total ≈ 6–7 days**, of which P2 carries essentially all the schedule risk.

**P0 — schema + empty read path.** Migration `0010_flight_details` (idempotent
per ADR-0042), `models/flight_details.py`, `Flight.details` relationship with
`lazy="noload"`, `schemas/flight.py` additions, `GET /{id}/details` +
`/details/status`, the extracted `telemetry_downsample` service.
*Tests:* `backend/tests/test_flight_details_model.py` — migration no-ops on a
fresh DB; the compiled `/flight-library` list query references neither
`flight_details` nor any new column (mirroring
`test_flight_library_list_defers_heavy_columns.py`, with the same
control-test-proves-the-assertion structure); endpoint 404 / empty-shape /
`eligible` behaviour. Inert on deploy — an empty table changes nothing.

**P1 — Tier 0.** All of §2.2 inside the existing frame loop. `details: None`
added to `litchi.rs` / `airdata.rs`. Backend persists `parsed["details"]` in
`_build_flight_from_parsed` (`flight_library.py:422-534`) inside the existing
best-effort savepoint pattern used for battery tracking
(`flight_library.py:527-533`) — a details failure must never fail an import.
*Tests (Rust, in `dji.rs`'s existing `#[cfg(test)] mod tests`):* phase histogram
over a synthetic frame vector; photo counter counts **rising edges only**; event
dedupe collapses N identical strings to one record with `count = N`; the garbled
prefix rule trims and flags; series decimation stride is exactly the backend's;
unit assertions per ADR-0032 (m, m/s, V, A, °C, mAh, Wh).
*Tests (pytest):* a parsed payload with `details` writes a row; one without
writes none; a malformed `details` is swallowed and the flight still imports.

**P2 — Tier 1.**
*P2-a, the gate (do this first, alone):* a throwaway spike answering — what is
0.5.7's record accessor signature, does it consume the keychains, what is peak
RSS on the largest prod log, and does 0.6.x (if it exists) fix the
`SmartBatteryStatic` decode and the `Unknown(178/137/139)` `ProductType`?
Measured, not assumed. If peak RSS exceeds the parser's `mem_limit: 256m`
(`docker-compose.yml:350`), the fix is a `mem_limit` bump to 512m in the same
change, justified by the measurement — not a guess.
*P2-b:* the record pass, the `>> 8` shim with its plausibility gate.
*Tests:* the shim against all four census values (5888→23, 2304→9,
1899520→7420, 1505282→5880) plus explicit rejection of an implausible result;
`AppGPS`-absent logs (Goggles) produce NULL pilot fields, not zeros.

**P3 — backfill.** §3.2 endpoint, plus a `FlightDataTab` card that loops
`limit=25` batches until `remaining == 0`.
*Tests:* a `before_execute` SQLAlchemy event listener asserts the backfill path
emits **zero** `UPDATE` statements against `flights`; a second run reports
`processed: 0`; a missing stored file is a skip, not an error.
*Prod:* the §3.2 checksum before and after, quoted in `PROGRESS.md`.

**P4 — UI.** Route, page, two entry points, empty state.
*Test:* one Vitest smoke at `frontend/src/pages/__tests__/FlightDetails.test.tsx`
(the established location — `MissionDetail.hub.test.tsx` et al.) — renders each
section from a fixture, and renders the empty state on `details: null`.

**P5 — battery wiring, gated on operator sign-off.** Fills
`battery_logs.discharge_mah` (`models/battery.py:52`, always NULL today) and
optionally `batteries.cycle_count` / `health_pct` from `pack_cycle_count`. Gated
because `batteries.cycle_count` is currently a hand-incremented counter
(`flight_library.py:2568` — `battery.cycle_count += 1` per flight) and replacing
it with the pack's own value **changes an existing operator-visible number**.
That is a decision, not a refactor.

**CI:** `.github/workflows/` contains only `auto-merge-claude.yml`,
`secret-scan.yml`, `self-hosted-smoke-test.yml` — **there is no pytest or cargo
job.** So each phase runs `cd flight-parser && cargo test` and
`cd backend && pytest` locally and **quotes the output** in the commit body or
`PROGRESS.md`. Per the fleet "verify the observable end state" rule, an exit code
is not evidence.

**Version bumps** — CLAUDE.md requires all four on every code change:
`README.md:5`, `frontend/package.json:4`, `backend/app/main.py:602`,
`frontend/src/components/Layout/AppShell.tsx`. Note: AppShell carries the string
at **two** places (lines 121 and 394 — desktop sidebar and mobile drawer); CLAUDE.md
says "line" singular. Both must move. Additionally `flight-parser/Cargo.toml`
(currently `1.1.0`) bumps on P1/P2 — it is **not** in CLAUDE.md's four-file list
and should be added as a fifth marker, because `main.rs:127` already reports
`env!("CARGO_PKG_VERSION")` on `GET /health`, which is exactly how we confirm a
parser deploy actually landed.

**Deploy.** *Correcting a common misreading:* prod is **not** deployed by hand.
Per **ADR-0018** and CLAUDE.md, DroneOpsCommand prod on BOS-HQ is deployed by the
**NOC Master Control fleet deployer** (`swarmpilot_deployer`) on push to `main`;
the `.deployer-disabled` marker in the repo root disables the retired *per-repo
autopull*, not the fleet deployer. `update.sh` does not exist (deleted in
`e4610b5`) — **CLAUDE.md's "Tech Stack → Deploy: `update.sh`" line is stale and
should be corrected in a docs pass.** `flight-parser` is a `build:`-only compose
service (`docker-compose.yml:344-347`); NOC ADR-0079 taught the deployer's digest
gate to resolve those, so a Rust change does rebuild the image. Verify each
parser deploy landed with `GET /health` → `version` (`main.rs:120-133`), not with
"the push succeeded." The **demo stack** (`~/droneops-demo` on BOS-HQ) is *not*
deployer-managed and must be updated by hand per CLAUDE.md.

Docs-only commits carry `[skip-deploy]` in the subject.

---

## 7. Risks

| # | Risk | Assessment | Mitigation |
|---|---|---|---|
| **R-1** | The `dji-log-parser` 0.5.7 record-access API is unverified — signature, keychain consumption, return shape. | **High likelihood of friction, high impact on P2 only.** The census inferred it from crate source; it was never called. | P2-a spike is a hard gate. P0/P1/P3/P4 deliver without it. If records need a second DJI keychain fetch per flight, that is a per-flight external API call across 210 backfills and P2 stops for a re-plan. |
| **R-2** | Parser memory. `flight-parser` runs on `mem_limit: 256m` (`docker-compose.yml:350`, sized for an idle ~1 MiB service). Frames + records resident simultaneously on a 20 MB log with 13,870 frames could exceed it. | Medium likelihood, medium impact — an OOM-killed parser fails the import loudly (`httpx.ConnectError` → "flight-parser service unavailable"), it does not corrupt data. | Measure in P2-a. Bump to 512m in the same change if measured need, with the number in the commit body. Do not pre-emptively bump on a guess. |
| **R-3** | Tier 2 `Unknown` record types are ~30% of records in newer logs (types 5, 17, 26, 45, 48, 54, 55, 57, 63, 253, 254). | Certain, low impact. | Accepted and out of scope, unchanged from the census. Type 48 (80 bytes, once per OSD frame on M4TD/M30) is the one worth watching upstream. |
| **R-4** | The `>> 8` shim (§2.4) rests on four data points from two airframes. | Medium likelihood of an airframe where it is wrong. | Plausibility gate + `pack_values_plausible` flag; the UI hides implausible values rather than showing a wrong cycle count. Check upstream first — if 0.6.x fixed it, version-gate the shim instead of owning it. |
| **R-5** | The `Flight.details` relationship, if ever changed from `lazy="noload"` to `selectin`, silently reproduces ADR-0019 across the entire list path. | Low likelihood, **high impact** (that bug was a production OOM crash-loop). | The P0 test asserts the compiled list query never references `flight_details`, with a control test proving the assertion is meaningful. Same structure as the existing ADR-0019 guard. |
| **R-6** | A crate upgrade (see OQ-4) would re-derive headline metrics for all 210 flights. | Low likelihood in this phase (explicitly out of scope), **high impact** if done casually. | Any `dji-log-parser` version bump requires its own ADR and a before/after diff of all 210 flights' headline metrics. It is not a dependency chore. |
| **R-7** | Storage growth is an estimate (§2.5), not a measurement. | Low impact — the estimate has ~3× headroom before it matters. | Measure `pg_total_relation_size('flight_details')` after the first 25-flight backfill batch and record it in `PROGRESS.md` before continuing. |

---

## 8. Open questions for Bill

**OQ-1 — `take_off_altitude` units.** The census found it appears to be **10×
metres** on the M4TD log. One airframe, one sample. *Assumption taken in this
plan:* store the value **raw** with `take_off_altitude_units = 'unconfirmed'`
and **do not display it** until a second airframe confirms the scale. Guessing
×0.1 and being wrong puts a fabricated altitude on a screen, which is exactly
what ADR-0028's truthfulness posture forbids.

**OQ-2 — Pilot GPS: store the track, or only derived distance?** *Assumption
taken:* store **derived scalars and a decimated distance series only** —
`pilot_sample_count`, `pilot_max_distance_m`, `pilot_avg_distance_m`,
`series.pilot_distance_m`. **Raw pilot lat/lon is not stored.** Reasoning: the
operational value asked for (VLOS distance) is fully captured by the derived
form; the raw form is personal location data about the PIC, it would land in a
database that is backed up to R2 (ADR-0041) and read by client-facing report
code, and once stored it is hard to un-store. If Bill wants the raw pilot track
(e.g. for a "where was I standing" map layer), say so and it becomes a column —
but it should be a deliberate yes, not a default.

**OQ-3 — Series resolution.** 4,000 points per series (§2.5), ~60–150 KB
compressed per flight. Full resolution is ~3.5× that on a 15 Hz airframe. Is
4,000 acceptable, or is full-resolution storage wanted for future time-weighted
analysis? *Assumption taken:* 4,000. All scalars are computed at full resolution
regardless, so nothing measurable is lost — only replot fidelity beyond 4,000
points, which no read path can currently request (the endpoint caps at 10,000
and defaults to 2,000).

**OQ-4 — Bump `dji-log-parser` past 0.5.7?** Not evaluated, deliberately. A
newer crate could fix both the `Unknown(178)/(137)/(139)` `ProductType` gap and
the `SmartBatteryStatic` decode, retiring the §2.4 shim. But it can also change
frame decoding, and therefore `duration_secs` / `total_distance` /
`max_altitude` / `max_speed` on all 210 flights — the numbers ADR-0027 and
ADR-0028 fought for. *Assumption taken:* **out of scope for FP-1.** P2-a reads
the upstream changelog for information only. Adopting a new version is its own
ADR with a full before/after metric diff.

**OQ-5 — Fix `drone_model = "Unknown(178)"` in this phase?** 150 of 210 DJI
flights carry the literal `Unknown(178)` / `Unknown(137)` / `Unknown(139)` in
`flights.drone_model`, while the real name ("Matrice 4TD", "DJI Mavic 4 Pro")
sits in `raw_metadata.aircraft_name`. Attribution is by serial (ADR-0007) so
nothing is mis-attributed — the column is just ugly. *Assumption taken:* **not
in FP-1.** It writes to `flights.drone_model` on existing rows, which is exactly
the class of change this plan is built to avoid mixing in. It is a small,
clean, separate data-repair migration (fall back to `aircraft_name` when the
product type is `Unknown(*)`) and should be one.

**OQ-6 — Should any captured event page an alert?** *Assumption taken:* **no.**
Every event here is post-hoc, discovered minutes-to-days after the flight, and
fails question 1 of the ADR-0037 five-question gate (actionable within 5
minutes). Capture and display only. If a "compass error on last flight" nudge is
wanted later, it belongs in the NOC digest, not as a page.

**OQ-7 — Is Flight Details reachable for non-DJI flights?** *Assumption taken:*
**no** — the link appears only when `source === 'dji_txt'`. Litchi/Airdata/ODL
imports have no extended data to show, and a page that is always empty for
three-quarters of the library teaches operators to ignore it.

---

## 9. What this plan deliberately does not do

- Does not touch `duration_secs`, `total_distance`, `max_altitude`, `max_speed`,
  `home_lat/lon`, `point_count`, or `gps_track` geometry on any existing row.
- Does not change `flights.raw_metadata`.
- Does not add a column to `flights`.
- Does not put a chart, a map overlay, or a report section anywhere.
- Does not reinterpret, editorialize, or compare any altitude against any limit
  (ADR-0029 / ADR-0031 remain in full force on every surface this plan touches).
- Does not upgrade `dji-log-parser`.
- Does not auto-maintain the `batteries` table (P5 is separately gated).
