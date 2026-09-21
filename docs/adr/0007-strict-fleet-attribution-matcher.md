# ADR-0007: Strict fleet-attribution matcher (no fuzzy fallback)

- **Status:** Accepted
- **Date:** 2026-05-01
- **Supersedes:** the fuzzy three-pass matcher introduced in v2.49.0 (commit `852f701`, 2026-03-26)
- **Amended by:** [ADR-0044](0044-serial-prefix-matcher-odl-canonical-serials.md) (2026-09-05,
  v2.90.0) — adds one narrowly-scoped rule *inside* the serial branch: the 16-char DJI
  header serial and the 20-char OpenDroneLog form of the same airframe are reduced to a
  canonical form and compared for **full equality** (never a prefix test — the thing this
  ADR banned). Everything else here, including the model branch and the
  `aircraft_id IS NULL` scoping, is unchanged and still in force.

> **Status 2026-09-21:** in force and measurably working. Live check on the authoritative
> primary: `SELECT count(*) FROM flights WHERE aircraft_id IS NULL AND drone_serial IS NOT
> NULL AND drone_serial <> ''` → **0**, and the startup backfill logs
> `STARTUP: Aircraft backfill — 0/0 unlinked matched`. Two line citations below have
> drifted and are corrected here rather than in place: the startup backfill cited as
> `main.py:308-346` now lives inside **`_run_startup_schema_and_seed()`
> (`backend/app/main.py:~430-459`)**, and `_match_fleet_aircraft` is at
> `backend/app/routers/flight_library.py:659`. Cite the symbols.

## Context

`_match_fleet_aircraft()` in `backend/app/routers/flight_library.py` is
called by every flight-log upload path (device, web, reprocess) and by
the startup auto-backfill in `app/main.py`. It returns the
`Aircraft` row a parsed flight should be attributed to (sets
`Flight.aircraft_id`).

The original matcher tried four levels of fall-through:

1. Exact serial-number match (`func.upper(...) == drone_serial.upper()`).
2. **Pass 1:** exact normalized model-name match (e.g. parsed `"DJI Mavic 3"` → norm `"mavic3"`).
3. **Pass 2:** symmetric prefix match —
   `fleet_norm.startswith(parsed_norm) OR parsed_norm.startswith(fleet_norm)`.
4. **Pass 3:** symmetric substring match —
   `parsed_norm in fleet_norm OR fleet_norm in parsed_norm`.

This was designed to absorb DJI's inconsistent naming (`Matrice 30` vs
`M30T` vs `Matrice 30T`) without forcing the user to pre-curate the
fleet.

### Problem (reported 2026-05-01)

After adding a new aircraft to the fleet with a broader model name
(e.g. `"DJI Mavic 3 Pro"`), every subsequently-uploaded flight was
attributed to that aircraft, **regardless of which drone the log
actually came from**. Batteries appeared to follow the same drone in
the UI because the Batteries view derives aircraft from the
`BatteryLog → Flight.aircraft_id` chain; nothing was actually written
to `Battery.aircraft_id` (which the upload pipeline never populates).

Root cause: pass 2's symmetric prefix check. Once the fleet contains
`"DJI Mavic 3 Pro"` (norm `"mavic3pro"`), any incoming flight whose
parsed model normalizes to `"mavic3"` (or any other prefix) hits
`"mavic3pro".startswith("mavic3") == True` and is attributed to the new
aircraft — even when the flight's `drone_serial` is present in the log
but doesn't match. Pass 3's substring rule made the trap even wider.

The startup backfill (`main.py:308-346`) re-runs the same matcher on
every container restart for any `aircraft_id IS NULL` row, so the
misattribution recurred on every deploy.

## Decision

The matcher is now strict:

1. **Serial-authoritative.** If `drone_serial` is present on the parsed
   flight, the matcher requires an exact case-insensitive match against
   `Aircraft.serial_number`. **No fall-through to model matching.** If
   the serial is present but unmatched, the flight stays unattributed
   (`aircraft_id = NULL`).
2. **Model-only fallback (no serial).** Only when `drone_serial` is
   absent does the matcher consult model names. It uses exact
   normalized equality and requires the result to be unambiguous —
   exactly one fleet aircraft of that model. Two or more matches →
   unattributed.
3. **No prefix matching, no substring matching.** Removed entirely.

Every branch outcome (matched serial, matched unique model, ambiguous
model, unmatched) logs at INFO under `doc.flights`, so unattributed
flights are diagnosable from logs alone (per the repo's "Logging &
Troubleshooting" standard in `CLAUDE.md`).

The startup backfill is unchanged structurally: it still only operates
on `aircraft_id IS NULL` rows, so this ADR's tightening reduces — not
increases — the volume of writes performed at startup.

## Consequences

**Positive:**
- A new fleet entry can no longer absorb unrelated flights via prefix
  collision.
- Manual aircraft assignments made through the Flights edit UI remain
  durable across deploys (already true; now also true for flights that
  previously would have been re-fuzzy-matched on restart).
- Battery-by-aircraft views become trustworthy because they derive from
  flight attribution.
- Operators get a log line per upload telling them whether the flight
  matched and how, or why it stayed unattributed.

**Negative:**
- Flight logs whose serial is absent or doesn't match a fleet record
  will arrive with `aircraft_id = NULL` and require manual assignment
  from the Flights UI.
- DJI naming variance (e.g. `M30T` vs `Matrice 30T`) is now handled
  *only* by the `_DJI_ALIASES` table in `_normalize_model()`. New
  aliases must be added there explicitly when a new model surface
  shows up.
- Existing flights that were already misattributed by the old fuzzy
  rules are not auto-corrected — those rows have `aircraft_id` set, so
  the backfill skips them. Operators must detach them manually via the
  Flights → Edit → Aircraft → clear flow.

## Alternatives considered

- **Keep fuzzy fallback, but require serial match when fleet has ≥2
  aircraft of the same model.** Rejected — this still attributes
  serial-bearing flights to the wrong drone whenever the serial is
  unmatched (DJI flight logs sometimes carry a serial format the user
  hasn't yet entered into the fleet, e.g. with leading zeros).
- **Strict serial-only attribution; never match by model.** Rejected
  for v2.63.14 because it removes the "I added one drone to the fleet
  and uploaded a log for it without typing the serial" first-run
  experience. The unique-model fallback preserves that path while
  refusing ambiguous matches.
- **Backfill that re-evaluates every flight, not just NULL ones.**
  Rejected — would silently rewrite operator-curated assignments on
  every container restart.

## References

- Code: `backend/app/routers/flight_library.py` (`_match_fleet_aircraft`)
- Code: `backend/app/main.py` (startup backfill, lines 308-346)
- Code: `backend/app/routers/flight_library.py` (manual backfill route, `/backfill-aircraft`)
- Frontend: `frontend/src/pages/Flights.tsx` (manual aircraft reassignment UI, `editForm.aircraft_id`)
- Origin: introduced as fuzzy three-pass matcher in commit `852f701` (v2.49.0, 2026-03-26)
