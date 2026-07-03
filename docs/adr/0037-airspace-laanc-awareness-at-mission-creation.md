# ADR-0037 — Airspace / LAANC awareness at mission creation (operator-facing pre-flight)

- **Status:** Accepted
- **Date:** 2026-07-03
- **Relates to:** [ADR-0029](0029-mission-reports-are-client-deliverables-not-compliance-audits.md) (client report never editorializes compliance — reinforced here as a hard boundary).

## Context

DroneOpsCommand already ingests live airspace/weather data — the weather
router (`backend/app/routers/weather.py`) fetches AviationWeather METAR, FAA
TFRs, NOTAMs, NWS alerts, and Open-Meteo conditions. But that data was
**dashboard-only**: it was surfaced on the ops Dashboard for a single
configured location and was **not tied to mission creation**. When an operator
created or scheduled a mission at a specific location, nothing told them
whether that site sat in controlled airspace, whether LAANC authorization was
likely required, or whether an active TFR overlapped the area.

Two things were also missing from the existing feeds entirely:

1. **Airspace-class determination.** METAR/TFR/NOTAM feeds do not tell you the
   airspace *class* at an arbitrary lat/lon. There was no controlled-vs-
   uncontrolled classifier.
2. **A mission-creation-time entry point** for any of it.

## Decision

Add an **operator-facing pre-flight airspace check** available at
mission-creation / scheduling time. It answers, for a coordinate:

- Airspace class at the site (controlled **B/C/D/E-surface** vs uncontrolled **G**).
- Whether **LAANC authorization is likely required** (controlled ⇒ likely).
- The **controlling facility**, when resolvable.
- **Nearby active TFRs.**
- **Weather suitability** (flight category + wind).

### New service — `backend/app/services/airspace.py`

- `fetch_airspace_class(lat, lon)` — queries the **FAA public Class Airspace
  ArcGIS FeatureServer** (free, no key) with a point-in-polygon geometry query.
  No intersecting polygon ⇒ uncontrolled Class G. Returns a **structured error
  dict, never raises**.
- `derive_laanc_requirement(class, e_surface)` — pure logic. **Tri-state**:
  `True` (B/C/D or E-surface), `False` (G / E-above-surface), **`None`
  (undetermined)**. Fabricating "not required" from missing data is the
  dangerous failure mode in an aviation-safety context, so unknown stays
  unknown and drives a *verify-manually* advisory.
- `assemble_preflight(...)` — pure assembler that combines the airspace class,
  the **reused** weather-router TFR/METAR/weather results, and emits neutral,
  factual **advisories**. It never renders a compliance verdict.
- `extract_latlon(area_coordinates)` — best-effort coordinate extraction from a
  mission's free-form `area_coordinates` JSON (flat lat/lon + aliases, nested
  `center`, GeoJSON Point/Polygon-centroid).

### New endpoints — `backend/app/routers/missions.py`

- `GET /api/missions/airspace-preflight?lat=&lon=&airport=` — primary entry
  point. `airport` defaults to the configured weather station.
- `GET /api/missions/{mission_id}/preflight` — resolves the coordinate from the
  mission's `area_coordinates`; returns a `no_coordinates` advisory shell if
  none is derivable.

The existing AviationWeather TFR/METAR and Open-Meteo fetchers are **reused
verbatim** (imported from `app.routers.weather`); they were not reinvented.

### Compute on demand — do NOT persist a snapshot

The preflight is computed on demand and is **never stored on the mission**.
Rationale:

1. **Staleness / safety.** TFRs activate and expire, and weather forecasts move.
   A snapshot captured at mission-creation (possibly weeks before the flight)
   would be stale — and dangerously misleading — by flight day. On-demand
   computation at the moment the operator looks is the correct read.
2. **Failover safety.** No schema change, no migration — zero risk to the
   standby-promotion path (fleet convention: additive/nullable only).
3. **Coordinates are optional.** `area_coordinates` is free-form and frequently
   null; a query-param endpoint decouples the check from mission persistence and
   serves the "evaluate a candidate location" scheduling case.

The mission-create path (`create_mission`) is deliberately **left unchanged** —
adding 3–4 synchronous external fetches to a core write would add latency and
failure surface for data that is best fetched on demand.

### Graceful degradation

Every feed is fetched concurrently via `asyncio.gather(..., return_exceptions=True)`.
Any feed failing (or even *raising*) degrades to partial data + a `partial_data`
advisory and `degraded: true` — it **never** propagates as a 500 that blocks the
operator. When airspace can't be determined, `laanc_likely_required` is `null`
(not a fabricated bool) with an `airspace_unavailable` advisory.

## Hard boundary — operator-facing ONLY, never in the client report

This is pre-flight **awareness**, not a compliance audit, and not a client
artifact. Per ADR-0029, the client report must never editorialize about
airspace or regulatory limits. Enforced structurally:

- The preflight is never persisted on the mission and never passed to any report
  builder (`claude_llm`, `ollama`, `pdf_generator`, `map_renderer`,
  `flight_metrics`, `report_audience`, `celery_tasks`, `email_service`).
- No advisory contains a compliance verdict ("violation", "illegal",
  "non-compliant", etc.) — asserted by unit test.
- A static guard test (`tests/test_report_never_references_airspace.py`) fails
  if any report-generation module or the mission response schema ever references
  `airspace`/`laanc`/`preflight`/`tfr`/`controlling_facility`.

**Not built (out of scope):** a full FAA USS / LAANC auto-authorization
integration. DroneOpsCommand is **not** an FAA-approved USS; the operator still
authorizes through an approved USS (e.g. Aloft). Every response carries a
`disclaimer` saying exactly this.

## Consequences

- Operators get airspace/LAANC/TFR/weather awareness before flying, at the point
  of scheduling — closing the "airspace is dashboard-only" gap.
- No new dependency, no migration, no change to the report path.
- The FAA ArcGIS endpoint is a live external dependency; its failure is fully
  contained (advisory + `degraded`), so an outage never blocks mission work.
- Response `laanc_likely_required` is `bool | null` — consumers must treat
  `null` as "undetermined, verify manually", not as `false`.

## Response shape

```jsonc
{
  "location": { "lat": 44.12, "lon": -123.22, "airport_ref": "KEUG" },
  "airspace_class": "D",                 // B|C|D|E|G|null
  "laanc_likely_required": true,         // true|false|null(undetermined)
  "controlling_facility": "EUGENE MAHLON SWEET FLD",
  "tfrs": [ /* active TFRs only */ ],
  "weather": { "conditions": {…}, "metar": {…}, "flight_category": "VFR" },
  "advisories": [ { "code": "laanc_likely_required", "severity": "caution", "message": "…" } ],
  "degraded": false,
  "disclaimer": "Operator-facing pre-flight awareness only. …not an FAA-approved USS…",
  "generated_at": "2026-07-03T…Z"
}
```
