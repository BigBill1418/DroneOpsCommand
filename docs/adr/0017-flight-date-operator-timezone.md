# ADR-0017 — Flight calendar dates are operator-local, not UTC

- **Status:** Accepted
- **Date:** 2026-06-02
- **Supersedes:** —
- **Related:** ADR-0009 (additive-nullable schema), the `_parse_datetime`
  storage-as-naive-UTC convention in `flight_library.py`.

## Context

A flight flown the evening of **2026-06-01 at 20:27 PDT** displayed in the
portal — and was named — as **2026-06-02**. Investigation against the live
production row confirmed the *instant* was captured correctly:

```
name:       DJI-Matrice-4TD_20260602_0001
start_time: 2026-06-02 03:27:27   (naive, stored as UTC)
```

`2026-06-02 03:27 UTC` is exactly `2026-06-01 20:27 PDT`. The data was not
captured wrong. The bug was in **reducing that UTC instant to a calendar
date**, which happened in two places, both of which took the UTC date:

1. **Display.** `start_time` was serialized with `.isoformat()` on a *naive*
   datetime → `"2026-06-02T03:27:27"` with **no `Z`/offset**. The frontend did
   `new Date(...).toLocaleDateString()`; per the ECMAScript spec an
   offset-less ISO date-*time* is parsed as **browser-local**, so the UTC date
   was rendered verbatim as June 2.
2. **Name.** `_generate_flight_name` called `start_time.strftime("%Y%m%d")`
   directly on the naive-UTC value → `20260602`, baking the wrong date into
   the stored `name` column.

A drone operator's "flight date" is the local date where the flight was flown.
The system had no concept of that local timezone, so it defaulted to UTC.

## Decision

**Define a flight's calendar date as the date of its instant in a fixed
operator timezone**, `settings.operator_timezone` (default
`America/Los_Angeles`, overridable via `OPERATOR_TIMEZONE`).

- **Storage unchanged.** Instants remain naive UTC in the DB. This is a
  presentation/derivation fix, not a schema change — so it self-corrects all
  history for *display* with no migration.
- **Wire format fixed.** All flight datetimes are serialized **UTC-aware**
  (explicit `+00:00`) via `app/utils/timezone.iso_utc`, so a client can never
  misread a naive timestamp as local wall-clock. Applied to `FlightResponse`
  (Pydantic `field_serializer`) and every manual `.isoformat()` emit site
  (`flight_library`, `missions`, `pilots`).
- **Display centralized.** The frontend formats every flight date/time through
  `frontend/src/lib/datetime.ts`, which pins `timeZone: 'America/Los_Angeles'`
  — making the displayed date **independent of the viewer's browser timezone**
  (important for shared/client views). It also defensively coerces any
  offset-less timestamp to UTC.
- **Name derivation fixed.** `_generate_flight_name` derives `YYYYMMDD` via
  `local_date_compact` (operator-local).
- **Backfill.** `scripts/backfill_flight_local_dates.py` rewrites the date
  token inside existing auto-generated `name`s to the operator-local date,
  preserving label + sequence, skipping operator-customized names and
  collisions. Dry-run by default.

### Why a fixed operator TZ (not per-flight GPS-derived)

BarnardHQ flies essentially all jobs in the Pacific zone. A fixed, configured
timezone is deterministic, dependency-free, and correct for every current
flight. Per-flight GPS→timezone lookup is the gold standard for a multi-region
operator and can be layered on later (the home-point lat/lon is already stored
on each flight) by replacing the single `operator_tz()` call — the seam is in
one module. Until then, an operator traveling to another zone changes one
setting.

## Consequences

- Displayed flight dates/times are now operator-local and viewer-independent.
- The fix is retroactive for display with **zero data migration**; only the
  cosmetic stored `name` token needs the one-shot backfill.
- A naive timestamp can no longer leak onto the API for flights — `iso_utc`
  stamps UTC at every emit site. New flight-datetime emit sites MUST use
  `iso_utc` (backend) and `lib/datetime` helpers (frontend).
- Client-facing PDF reports were **not** affected — they render the
  operator-entered `mission_date` (a date-only field), not `start_time`.

## Failover & resilience

No schema change; storage convention unchanged. Standby promotion is
unaffected. The backfill script is idempotent and safe to re-run on the
promoted primary.
