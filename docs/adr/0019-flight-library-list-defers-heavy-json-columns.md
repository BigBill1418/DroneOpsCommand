# ADR-0019 — Flight-library list defers heavy per-flight JSON columns

- **Status:** Accepted
- **Date:** 2026-06-10
- **Version:** v2.68.5
- **Supersedes / relates to:** ADR-0017 (flight date/timezone), the
  `/api/flight-library` list endpoint, and the mission-picker flight loader
  (`frontend/src/pages/MissionFlightsEdit.tsx`).

## Context — production incident (2026-06-10 ~11:25 PM PT)

Operator reported two symptoms:

1. **The DroneOpsCommand mission picker could no longer see uploaded flights.**
   Flights uploaded via DroneOpsSync did not appear when attaching a flight to
   a mission. This was a regression — it had worked before.
2. **DroneOpsSync uploads were extremely slow.**

### Investigation (evidence, not assumption)

- The mission picker (`MissionFlightsEdit.loadFlights`) calls
  `GET /api/flight-library` **first**, and on **any** error silently falls back
  to `GET /api/flights` — the OpenDroneLog (ODL) proxy. ODL is unreachable in
  production (`Cannot connect to OpenDroneLog at http://192.168.50.20:3001`), so
  the fallback returns nothing useful.
- Ground-truth DB check (`droneops-standby-db`, the promoted primary on BOS-HQ):
  the `flights` table **did** contain the freshly uploaded rows (e.g.
  `DJI-Matrice-4TD_20260610_0002/0003`). The sync write path was healthy. So the
  rows existed but were invisible to the picker.
- Reproducing the endpoint with a real JWT inside the backend container:
  - `GET /api/flight-library?limit=5` → **HTTP 200**, fast, today's flights present.
  - `limit=50` → 1.9 s / 79 KB · `limit=200` → 4.1 s / 265 KB ·
    `limit=250+` → **"Empty reply from server"**, then the API **stopped
    accepting connections entirely**.
- The mission picker uses the **default `limit=500`**.
- Host `dmesg` (kernel cgroup, authoritative) showed:
  `Memory cgroup out of memory: Killed process … (uvicorn) … anon-rss:1036196kB`
  — the uvicorn worker exceeded the container's `mem_limit: 1g` and was
  OOM-killed. (`docker inspect` reported `OOMKilled=false` because the kill
  targeted a child process within the memcg, not PID 1 — the flag is
  unreliable for that case; the kernel log is authoritative.) `RestartCount`
  had climbed to 9.

### Root cause

`list_flights` builds `select(Flight)`. SQLAlchemy loads **all mapped columns**
by default, including the heavy per-flight JSON columns `gps_track`,
`telemetry`, and `raw_metadata`. A single flight's `gps_track` can hold ~19,000
GPS points; for the top 500 flights these columns total ~33–44 MB of *TOASTed
(compressed)* on-disk JSON. Decompressing and deserializing that into Python
objects (lists of dicts of floats — ~10–20× the text size in process memory)
for 500 rows at once pushed the worker past the 1 GB limit and the kernel
OOM-killed it mid-serialization.

`FlightResponse` (the list response schema) does **not** include any of those
three columns — they were pure waste, loaded only to be discarded.

This single fault explains **both** symptoms:

- **Visibility:** the picker's `limit=500` request OOM-killed the worker →
  request failed → frontend silently fell back to the unreachable ODL proxy →
  uploaded flights vanished from the picker.
- **Slowness:** the API worker was crash-looping (RestartCount 9). Uploads
  racing a repeatedly-OOM-killed/restarting backend were slow and unreliable.

## Decision

Defer the heavy JSON columns in the list query:

```python
LIST_DEFERRED_COLUMNS = (Flight.gps_track, Flight.telemetry, Flight.raw_metadata)
query = select(Flight).options(*[defer(col) for col in LIST_DEFERRED_COLUMNS])...
```

`FlightResponse` never serializes these columns, so deferral is transparent to
every client. The flight **detail** (`GET /api/flight-library/{id}`),
**telemetry**, and **track** routes load their own data on demand and are
unaffected — they fetch a single flight, never 500.

The heavy-column set is a named module constant so the regression test asserts
against the same source of truth the endpoint uses.

## Consequences

- The list query's emitted SELECT no longer references `gps_track`,
  `telemetry`, or `raw_metadata`; the response payload and per-row memory drop
  by orders of magnitude. The 500-row picker request returns well within the
  worker's memory budget — no OOM, no crash-loop, no silent ODL fallback.
- Regression guard: `tests/test_flight_library_list_defers_heavy_columns.py`
  compiles the production query against the Postgres dialect and asserts the
  heavy columns are excluded while the light columns the picker needs remain. A
  control test proves the assertion is meaningful (the plain query *does* select
  them), and a coverage test forces a deliberate decision if a new large JSON
  column is added to `Flight`.

## Follow-ups (separate, not blocking this fix)

- **Frontend fail-soft is too quiet.** `MissionFlightsEdit.loadFlights` treats
  *any* `/api/flight-library` error as a cue to fall back to a dead ODL proxy,
  masking real backend failures. Per the project's "no silent failures" rule
  this should surface the primary error (and only fall back on a genuine
  404/not-configured), so a future API regression is visible instead of
  presenting as "no flights." Tracked for a follow-up; not required to restore
  visibility (the backend defer alone fixes it).
- **Boot-time backfill log spam.** The startup aircraft-backfill emits hundreds
  of `fleet-match … unmatched` INFO lines on every boot; worth quieting.
