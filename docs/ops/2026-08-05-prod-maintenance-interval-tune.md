# 2026-08-05 — Prod maintenance-alert clear + interval tune (data-only)

**Operator:** Bill (via Claude Code session)
**Scope:** prod instance only (`droneops.barnardhq.com`, `droneops-standby-db` on BOS-HQ).
Demo and managed stacks untouched. No code or schema change — `maintenance_schedules`
rows only; `maintenance_records` (history) untouched.

## What changed

1. **Cleared 8 overdue maintenance schedules** by resetting `last_performed`
   to `2026-08-05` (identical semantics to the app's own Skip / Defer-all
   endpoints in `backend/app/routers/maintenance.py`):
   - Avata 2: Battery Health Check, Firmware Review, IMU Calibration,
     Remote Controller Inspection, Sensor Cleaning
   - Matrice 4TD: Battery Health Check, Firmware Review, Sensor Cleaning
2. **Extended all nine 30-day intervals to 90 days** (`interval_days 30 → 90`)
   on Bill's direction — Sensor Cleaning, Battery Health Check, Firmware
   Review across Avata 2 / Matrice 30T / Matrice 4TD. The 30-day cadence was
   nagging faster than his actual maintenance rhythm.
3. **Deferred two additional 90-day items** on the Matrice 4TD that were
   inside the due-soon window (last done 2026-05-16, due 2026-08-14):
   IMU Calibration and Remote Controller Inspection → `last_performed`
   reset to `2026-08-05`.

## Verified end state (queried live post-change)

Zero overdue items by both the date-based and flight-hours-based checks that
`GET /api/maintenance/status` uses. Next date-based due item anywhere:
Avata 2 Gimbal Calibration, 2026-10-04.

## Correction (same day, ~03:20 UTC): record-based alerts were missed

The first "verified zero overdue" claim was **wrong**. `GET /api/maintenance/due`
has a **second alert source**: `maintenance_records.next_due_date` (any record
due within 7 days alerts). Four March-2026 service records carried stale
`next_due_date` values (2026-05-15 / 2026-06-15 / 2026-08-01), producing the
"82d / 51d OVERDUE" dashboard alerts on Matrice 30T, Mini 5 Pro, Mavic 3 Pro,
and Avata 2. Compounding it, the earlier verification queries JOINed through
`maintenance_schedules`, hiding the 4+ prod aircraft that have records but no
schedules (Mini 5 Pro, Mavic 3 Pro, etc.).

Fix: set `next_due_date = NULL` on those 4 records (service history retained).
Re-verified all four dashboard alert sources at zero: schedule date-based,
schedule never-performed, record `next_due_date`, and battery
(`health_pct < 40 OR cycle_count > 200`).

## Gotchas for future sessions

- The `seed-defaults` endpoint only creates schedules for aircraft that have
  **none**, so it will not clobber these tuned intervals — but if an aircraft's
  schedules are ever deleted and re-seeded, the seeds restore the 30-day
  defaults and the nagging returns.
- `POST /maintenance/defer-all-overdue` only defers day-based schedules
  (`interval_days`); hours-based overdue (from `maintenance_records.flight_hours_at`)
  is not cleared by it. Not an issue this time — no hours-based item was overdue.
