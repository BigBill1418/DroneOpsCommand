# ADR-0049: EyesOn reads flight records through a scoped, read-only service token

- **Status:** Accepted. **Live and enabled on BOS-HQ since 2026-09-27 22:41 PDT** (v2.96.0,
  merge `a6317aa`; token hash set; verified 503 → 401/401/200, see PROGRESS). On any other
  install both routes answer `503` until `EYESON_SERVICE_TOKEN_SHA256` is set.
- **Date:** 2026-09-27
- **Consumer:** EyesOn **ADR-0065** (session ↔ flight-record matching). Operator approval
  2026-09-27: *"you can match fligt logs - yes"*.
- **Relates to:** ADR-0019 (heavy-column OOM; list must defer `gps_track`/`telemetry`),
  ADR-0043 (`flight_details` / `flight_series`), ADR-0047/0048 (operator SSO, service accounts).

## Context

EyesOn wants to read a stream drop against what the aircraft was doing. The 2026-09-16 PDT
evening had two of three stream drops land seconds before an M4TD flight record starts. To
see that automatically, EyesOn needs (a) the flights overlapping a time window and (b) one
flight's time-stamped samples.

Every existing flight route sits behind `get_current_user`: a local JWT or a Cloudflare
Access identity. The machine-caller precedent (ADR-0048) is a **service account**: a full
`users` row whose password lives on the calling host.

## Decision

A new router, `backend/app/routers/eyeson_integration.py`, prefix `/api/integrations/eyeson`:

| Route | Returns |
|---|---|
| `GET /flights?start=&end=` | Flights whose record interval overlaps the window (≤ 48 h). Scalar columns only (id, name, start_time, duration_secs, drone_model, drone_serial, source, first/last_frame_at). Never selects `gps_track` / `telemetry` / `raw_metadata`. A test asserts it. |
| `GET /flights/{id}/timeline?max_points=` | One flight: `telemetry` (absolute timestamps; altitude, speed, battery %, signal, distance from home), `track` (t, lat, lng, heading) and `frame` (`origin` = `first_frame_at`, `t_offset_s`, `rc_uplink`, `rc_downlink`, `vps_height_m`). Each is downsampled through the shared `select_indices`, so every point is a real recorded sample. |

**Auth:** the header `X-EyesOn-Service-Token`. It is SHA-256'd and compared in constant time
(`hmac.compare_digest`) to `EYESON_SERVICE_TOKEN_SHA256`. Only the hash is stored on this
host. The raw token is stored only on the EyesOn host. Hash unset → `503` (dark on self-hosted,
OSS and demo). Missing or wrong token → `401`, and a rejected attempt logs the first 8 hex
characters of its hash. A test fails if any route on this router lacks the dependency.

**Frame-bound plausibility.** `flight_details.first_frame_at` is `1970-01-01 00:00:00` on two of
the three 2026-09-16 M4TD flights (0002, 0001). The frame clock did not decode. Trusting that
value makes the flight overlap every window. The overlap filter uses a frame bound only when
it is within 5 min of the header-derived bound. The raw value is still returned. EyesOn
applies the same rule. **This is a data-quality defect in the flight-details ingest. It is not
fixed here and is recorded for FP-1.**

## Alternatives rejected

- **A service-account login (ADR-0048 pattern).** It is zero code, but the credential on the
  EyesOn host would read and **write** customers, invoices and missions. EyesOn needs two
  read-only views of `flights`. A leak of the scoped token exposes flight telemetry, not the
  business.
- **Reuse a device API key.** Those keys authorise uploads from field controllers. Widening
  them to reads mixes two trust boundaries, and a stolen controller key would then read the
  whole flight library.
- **EyesOn reads Postgres directly.** That couples EyesOn to this schema and gives it a DB
  credential. It also bypasses the ADR-0019 discipline.

## Consequences

- To enable, generate a random token on BOS-HQ. Put `sha256(token)` in `~/droneops/.env` as
  `EYESON_SERVICE_TOKEN_SHA256` and the raw token in `~/EyesOn/.env` as `DRONEOPS_SERVICE_TOKEN`
  (both mode 600). Recreate both backends. The token is also stored in 1Password Fleet.
- EyesOn reaches the backend at `http://10.99.0.4:8000`. It is the same host, and the traffic
  crosses the WireGuard interface address, never the public internet. This was verified
  2026-09-27 from inside `eyeson-api-1`: `/api/health` → 200.
- Rotation: replace both values and recreate both containers. The old token is refused
  immediately.
