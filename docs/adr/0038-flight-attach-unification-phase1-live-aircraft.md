# ADR-0038: Flight-attach unification, Phase 1 — resolve the report aircraft from the live flight, stop copying it onto the junction

- Status: Accepted
- Date: 2026-07-03
- Related: ADR-0007 (serial-first fleet matching — untouched), ADR-0025
  (heavy-column defer / OOM family), ADR-0028 H2 (live-scalar reporting),
  ADR-0029 (report audience guard — untouched), ADR-0033 (Avata junction-
  staleness incident)
- Plan: `docs/plans/2026-07-03-flight-attach-unification.md` (this ADR implements
  **Phase 1** only; Phases 2–4 remain proposed)
- ADR-number coordination: `docs/plans/2026-07-03-migration-consolidation.md`
  tentatively reserved 0035. The flight-attach unification is the artifact
  landing code now, so it takes **0035**; the migration-consolidation ADR should
  take the next free number (**0036**).

## Context

The `MissionFlight` junction (`backend/app/models/mission.py`) carries three
parallel representations of an attached flight: the native `flight_id`, the
legacy `opendronelog_flight_id`, and — the subject of this ADR — a **copied
`aircraft_id`** snapshotted from `Flight.aircraft_id` at attach time, plus a
`flight_data_cache` scalar snapshot.

The copied `aircraft_id` **goes stale**. This is the mechanism behind ADR-0033:
a flight was attached while its DJI Avata 2's fleet serial was still
unregistered, so `flights.aircraft_id` (and therefore the junction copy) was
NULL. When the serial was registered **later**, `flights.aircraft_id` updated to
point at the fleet record — but the junction's copy kept its stale NULL. Nothing
re-copies it short of a detach/re-attach.

ADR-0033's code fix (a58893a) made the report *tolerant* of a NULL junction copy
by falling back to the flight's parsed `drone_model`, so the aircraft stopped
vanishing. But it still did not read the **live fleet aircraft**: after the late
serial registration the report showed the bare parsed string `"Avata2"` instead
of the canonical fleet record `"DJI Avata 2"` (with its manufacturer, image, and
specs on the PDF "Aircraft used" card). The stale copy was masked, not removed.

Terry's convergence finding (plan §1): report reads already prefer live `Flight`
scalars (`_resolve_flight_metrics`, `_aircraft_label`), so the copied
`aircraft_id` and the cache are **already vestigial for native flights** — read
only as a fallback. The remaining work is to make that de facto reality de jure
for the aircraft read, at the root.

## Decision

**For native flights (`flight_id` set), resolve the aircraft from the live
`Flight.aircraft`, never from the junction's copied `aircraft_id`; and stop
writing that copy at attach.** Concretely:

1. **Read convergence (`backend/app/routers/reports.py`).**
   - `_load_live_flight_metrics` now LEFT-JOINs the fleet `Aircraft` via
     `Flight.aircraft_id` and returns its identity/card columns
     (`aircraft_id`, `aircraft_model_name`, `aircraft_manufacturer`,
     `aircraft_image_filename`, `aircraft_specs`) alongside the existing scalar
     metrics. Only scalar columns are selected — the heavy Flight JSON
     (`gps_track` / `telemetry` / `raw_metadata`) is still never loaded
     (ADR-0025/ADR-0019 preserved).
   - `_aircraft_label` branches on class. **Native**: live fleet
     `Aircraft.model_name` → live `drone_name` → live `drone_model` → `"Unknown"`
     (the junction copy is never consulted). **Legacy-ODL** (`flight_id IS
     NULL`): unchanged — junction `mf.aircraft` → cache → `"Unknown"`.
   - The PDF "Aircraft used" card is extracted into `_build_aircraft_cards` and
     resolves aircraft the **same** way — native from the live join (full fleet
     card with image/specs when linked; label-only when unlinked), legacy-ODL
     from the junction/cache. One resolution path drives both the narrative label
     and the PDF card.

2. **Write change (`backend/app/routers/missions.py`).** The single-add
   (`add_flight`) and bulk (`add_flights_bulk`) native paths no longer copy
   `Flight.aircraft_id` onto the junction — the junction `aircraft_id` is set to
   `None` for every native attach. This also continues to ignore any client-sent
   `aircraft_id` (single source of truth is the flight log, ADR-0007). The
   **column is retained** (its drop is Phase 4); existing rows keep their value
   but are no longer read for native flights.

### Class-branching rule

The one class with **no live backing row** is legacy-ODL (`flight_id IS NULL AND
opendronelog_flight_id IS NOT NULL`). That class keeps the junction/cache read
because there is nothing live to resolve from — Phase 2 materializes those into
real `Flight` rows, after which the fallback can be retired (Phase 3/4). Every
reader that changed here branches on `flight_id is not None` and does the live
resolution only for the native class.

## Scope — what Phase 1 does vs defers

**Does now:** converges the aircraft read onto the live flight for native rows;
stops copying `aircraft_id` at attach (single-add + bulk); extracts the PDF card
resolver. Behavior-preserving for reports **except** the intended fix — a native
flight linked *after* attach now shows the correct fleet aircraft (name + card)
with no detach/re-attach.

**Explicitly defers (per the plan):** the **metrics** read still falls back to
the cache for legacy-ODL (`_resolve_flight_metrics` unchanged — Phase 3);
legacy-ODL materialization into `Flight` rows (Phase 2); flipping metrics/track
reads to live-only and the zero-cache-read counter (Phase 3); dropping
`opendronelog_flight_id` / `aircraft_id` / `flight_data_cache` and making
`flight_id` NOT NULL (Phase 4). The ADR-0007 matcher and the ADR-0029 altitude/
Part-107 audience guard are untouched. Airdata is out of scope.

## Consequences

- **The staleness class is closed at the root for native flights** — the report
  aircraft can no longer diverge from the live flight, because it is read live.
  A serial registered after attach is reflected on the next generation.
- **The junction `aircraft_id` becomes derived-on-read** for native rows and is
  a Phase-4 drop candidate. Old read paths still find the column (not dropped),
  so this phase is independently reversible.
- **No new heavy-JSON load** — the live aircraft comes from a scalar LEFT JOIN,
  not from loading `MissionFlight.flight`; the OOM posture is unchanged.
- **Write-contract change is intentional and pinned by tests.** The former
  "attach copies `Flight.aircraft_id` onto the junction" contract
  (`test_mission_flight_attach_derives_aircraft.py`, the bulk assertion in
  `test_mission_large_flight_hardening.py`) is replaced by "native attach leaves
  the junction `aircraft_id` NULL; the aircraft is resolved live." These are
  updated to the new contract — a deliberate change, not a regression.

## Verification

- New: `backend/tests/test_report_live_aircraft_adr0038.py` — (a) a real-DB test
  proving a native flight linked **after** attach resolves the live fleet
  aircraft (name + card) with the junction copy still NULL (the ADR-0033
  scenario); (b) legacy-ODL still renders from junction/cache; (c) an unlinked
  native flight still falls back to `drone_model`; (d) a stale junction copy is
  ignored when the live flight says otherwise.
- Fail-before/pass-after confirmed: under the pre-ADR-0038 read logic the
  post-late-link narrative resolves to `"Avata2"` (parsed string); with the fix
  it resolves to `"DJI Avata 2"` (live fleet record).
- Full backend suite green (511 passed, 3 pre-existing skips).

## Deploy note

This repo is `.deployer-disabled` — the NOC deployer pulls git but does **not**
rebuild. Ship via a **manual rebuild on BOS-HQ**
(`docker compose build backend worker beat flight-parser && up -d --no-deps …`)
and verify the container build time / public `openapi.json` version (`2.76.4`),
**not** `deployer-state.json` (per ADR-0033's deploy note).
