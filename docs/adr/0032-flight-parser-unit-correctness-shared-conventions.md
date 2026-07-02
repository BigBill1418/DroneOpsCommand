# ADR-0032: Flight-parser unit correctness (voltage/speed/altitude)

- Status: Accepted
- Date: 2026-07-02
- Related: ADR-0027 (choose_duration), ADR-0028 (GPS teleport gate)

## Context

A top-down audit of the Rust `flight-parser` found three confirmed unit-conversion
defects that silently corrupted client-facing report values. None were caught by
tests because `litchi.rs` and `airdata.rs` had **zero** unit coverage.

1. **DJI battery voltage was divided by 1000 twice.** `dji-log-parser` 0.5.7 already
   returns volts (its `record/smart_battery.rs` / `center_battery.rs` map the raw
   `u16` with `/1000.0`), yet `dji.rs` divided again — a 15.2 V pack was emitted as
   0.0152 V. Airdata stored volts raw, so the two parsers disagreed by 1000×.
2. **Litchi speed stored raw as m/s** although Litchi logs are `speed(mph)` — every
   speed inflated ~2.237×. Litchi also had no unit detection at all.
3. **Airdata had no km/h branch** (metric exports read as m/s, ~3.6×), and its
   `altitude_above_seaLevel(feet)` candidate carried a capital `L` matched against a
   lowercased header, so it never matched and MSL could win over AGL.

Root cause of the *class*: each format re-implements unit/column resolution
independently; there is no shared conversion layer, so a fix in one parser does not
protect the others.

## Decision

- Normalise DJI voltage through a single tested helper that returns volts unchanged
  (documenting that the crate already converts); remove the second `/1000.0`.
- Detect the unit token in the matched Litchi/Airdata speed header and convert to m/s
  (mph ×0.44704, km/h ×0.277778). Prefer an explicit `datetime` column over any
  `time` substring so the numeric epoch-ms `timestamp` can never bind and collapse
  duration to the point-count fallback.
- Prefer AGL/height-above-takeoff over MSL for `max_altitude`; convert feet→meters.
- Add golden unit tests to every parser module asserting corrected numeric values.

The report **audience guard remains unchanged**: client reports never flag
altitude/Part-107 exceedances. This ADR is unit-correctness only.

## Consequences

- DJI voltage, and Litchi/Airdata speed & altitude, now match reality and each other.
- Known follow-up (not addressed here): CSV dispatch in `main.rs` tries the Litchi
  parser first, and Airdata files satisfy Litchi's column check, so most Airdata CSVs
  are parsed as Litchi. The Litchi speed fix now also corrects those files, but Litchi
  does not convert feet→meters, so an Airdata-in-feet file routed through Litchi still
  under-reports altitude. Fixing this requires header-signature format sniffing at
  dispatch — tracked as a separate change.
- The absence of a shared `units`/`columns` module is the standing risk; consolidating
  it would prevent this defect class recurring.
