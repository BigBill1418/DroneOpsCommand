# ADR-0033: Avata 2 missing from client report — root cause, code fix, and data remediation

- Status: Accepted
- Date: 2026-07-03
- Related: ADR-0007 (serial-first fleet-aircraft matching), ADR-0029 (report audience guard — untouched)

## Context

Operator flew the 2026-07-02 "Springfield Drifters Promo" mission
(`e6f51b78-7d0c-4061-850a-2f0c3ff08340`, customer River Molyneaux) with a DJI
Avata 2, attached the flight data, generated the client report — and the Avata 2
was **absent from the report** while the DJI Mini 5 Pro on the same mission
appeared. Flight data was fully intact (two Avata flights, ~13 min each, 8k+ GPS
points).

**Root cause — a one-field fleet-registration gap, not a parser/attach bug:**
- The fleet `DJI Avata 2` aircraft record (`eebaaa4f`) had a **blank
  `serial_number`**.
- `_match_fleet_aircraft` (`backend/app/routers/flight_library.py`) is
  serial-first and **strict** (ADR-0007): a flight carrying a serial that
  matches no fleet record returns `None` and deliberately does **not** fall back
  to model-name matching. The Avata flights' serial `1581F6W8A242N0A3` matched
  nothing → `flights.aircraft_id` left NULL → junction `aircraft_id` NULL.
- The report read only `MissionFlight.aircraft` and substituted the literal
  `"Unknown"` (LLM prompt) / omitted the flight (PDF "Aircraft used" card),
  discarding the flight's own parsed `drone_model`.
- Historical Avata flights were linked (they show canonical "DJI Avata 2")
  because they were matched under an older model-matching regime before ADR-0007;
  everything since drifted unlinked. Snapshot at incident: **21 linked ("DJI
  Avata 2") vs 12 unlinked ("Avata2") Avata flights.**

## Decision

**1. Code fix (defense-in-depth) — shipped, PR #34 (`a58893a`).**
`backend/app/routers/reports.py` gained `_aircraft_label(mf, live)` with a
fallback chain: linked fleet `model_name` → live `Flight.drone_name`/`drone_model`
→ cache → `"Unknown"` only as last resort; used by both the LLM aggregation and
the PDF card. An attached flight is now **never** silently dropped or anonymized,
for any future unregistered drone. ADR-0007 matcher and ADR-0029 audience guard
were intentionally left unchanged (loosening the matcher risks fleet-wide
mis-attribution). Test: `test_report_unrecognized_aircraft_label.py` (2 fail → 34
pass, zero regressions).

**2. Data remediation (root cause) — applied to prod DB 2026-07-03.**
Registered the drone's serial and backfilled the historical rows, in one
transaction against `droneops-standby-db` (the promoted primary):

```sql
UPDATE aircraft SET serial_number='1581F6W8A242N0A3'
  WHERE id='eebaaa4f-1f2e-49f0-826a-a88d789f4578' AND (serial_number IS NULL OR serial_number='');   -- 1 row
UPDATE flights SET aircraft_id='eebaaa4f-1f2e-49f0-826a-a88d789f4578'
  WHERE drone_serial='1581F6W8A242N0A3' AND aircraft_id IS NULL;                                       -- 12 rows
UPDATE mission_flights SET aircraft_id='eebaaa4f-1f2e-49f0-826a-a88d789f4578'
  WHERE aircraft_id IS NULL AND flight_id IN (SELECT id FROM flights WHERE drone_serial='1581F6W8A242N0A3');  -- 4 rows
```
Result: unlinked Avata flights 12 → 0; the newest mission's junction now resolves
`DJI Avata 2 ×2 + DJI Mini 5 Pro ×1`. A regenerated report renders the Avata 2
correctly with its aircraft card. (The junction `flight_data_cache` JSON snapshot
still carries the pre-fix `aircraft: null`; it is not read for the aircraft label
and is harmless, but a detach/re-attach would refresh it if ever needed.)

## Consequences

- **Standing operational rule:** register a fleet aircraft's **serial number**
  when adding a drone; under ADR-0007 a blank serial means every flight of that
  drone stays unlinked (report shows the raw model via the new fallback, but no
  aircraft image/specs card until the serial is registered). Add-drone UX/checks
  should require or strongly prompt for the serial.
- The report is now robust to unregistered aircraft — this class of "aircraft
  vanished from the report" cannot recur silently.
- **Deploy note (important):** this repo is `.deployer-disabled` — the fleet NOC
  deployer pulls git but does **not** rebuild/restart it. The fix (and the
  earlier ADR-0032 parser fix) sat in `main` un-deployed until a manual rebuild
  (`docker compose build backend worker beat flight-parser && up -d --no-deps …`)
  on BOS-HQ 2026-07-03. **`deployer-state.json` can show `success` for a commit
  that is only pulled, not running** — verify container build time, not deployer
  status, when confirming a DOC deploy is live.
