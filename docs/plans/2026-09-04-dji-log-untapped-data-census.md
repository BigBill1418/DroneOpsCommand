# DJI flight-log untapped-data census (2026-09-04)

**Status:** research record, nothing built. Bill asked "what ELSE is in those log
files that we can also extract and utilize?" This is the primary-source answer.

## Method

- Read `flight-parser/src/dji.rs` (what we extract today) and the vendored
  `dji-log-parser 0.5.7` crate source (what the decoder can hand us).
- Built a throwaway census binary against the same crate and ran it on one
  real prod log per airframe (7 logs, copied from BOS-HQ
  `/data/uploads/flight_logs/`, decoded with the production DJI key):
  Avata 2, FPV, Matrice 30, Mavic 3 Pro, Mavic 4 Pro (`Unknown(137)`),
  `Unknown(139)`, Matrice 4TD (`Unknown(178)`). All 210 prod `dji_txt`
  flights are v14 logs with `frames_decoded=true`, so everything below is
  available for every flight already in the database (the original files
  are retained; a reprocess pass can backfill).
- Census output lives only in the session scratchpad; this doc is the record.

## What we extract today (per flight)

Aircraft/battery/RC/camera serials, app version, start time, duration,
distance, max AGL height, max horizontal speed, home point, GPS track
(lat/lng/AGL/speed/heading, **no timestamps**), and telemetry series for
AGL altitude, speed, battery %, voltage, temperature and satellite count,
plus a battery summary (start/end/min V, max temp).

## Tier 0 — already decoded in memory on every import, currently dropped

These fields are on the `Frame` struct the parser iterates today. Cost is a
few lines each in `dji.rs` plus a schema/JSON change downstream.

| Data | Source field | What it enables |
|---|---|---|
| Per-point timestamps | `frame.custom.date_time` | `TrackPoint.timestamp` and `TelemetryData.timestamps` are both left empty today. Fixes time-sync for replay / video export and lets every derived metric be time-weighted instead of frame-weighted. |
| RC link quality | `rc.downlink_signal`, `rc.uplink_signal` (0–90, occasionally 104) | `signal_strength` is served by `flight_library.py` but is always null. M4TD log shows downlink at 0 for 10,422 of 13,870 frames and 18 "Remote controller disconnected" warnings — a link-quality map per flight is real operator value. |
| Distance from home | `home.latitude/longitude` vs `osd` | `distance_from_home` series is always null today. Also gives max range per flight. |
| Flight-mode timeline | `osd.flyc_state` (EngineStart, AssistedTakeoff, GPSAtti, GPSSport, GPSWaypoint, FPV, GoHome, ConfirmLanding…), `osd.flight_action` (RCOnekeyGoHome, VertLowLimitLanding, RCAssistantTakeoff…), `osd.flyc_command` | Takeoff/landing counts, RTH events (M4TD: 775 frames in GoHome after an RC one-key RTH), sport-mode seconds, automated-mission seconds (Mavic 3 Pro log: 1,377 frames in GPSWaypoint), manual vs automated ratio. |
| Camera activity | `camera.is_photo` edges, `camera.is_video`, `sd_card_state` | Per-flight photo shutter count (M4TD: 5 edges vs header `capture_num` 4) and video seconds (Avata 2: 573 frames recording). Correlates flights with `mission_images` deliverables. |
| Gimbal pointing | `gimbal.pitch/roll/yaw`, `gimbal.mode`, `is_stuck`, `*_at_limit` | Camera pointing at each photo edge (with aircraft yaw) → image footprint / look direction; gimbal-stuck flag as a maintenance signal. |
| Aircraft attitude + vertical rate | `osd.pitch/roll/yaw`, `osd.z_speed`, `osd.x/y_speed_max`, `osd.z_speed_max` | Max climb/descent rate (header also has `max_vertical_speed`), attitude envelope. |
| MSL altitude | `osd.altitude` (ASL), `osd.vps_height`, `home.altitude` | We keep AGL only. M4TD flight: 339.8 m MSL = 145.9 home + 193.9 AGL. MSL matters for airspace ceilings / LAANC; VPS height is the low-altitude precision source. |
| Battery current + capacity | `battery.current` (A), `current_capacity`/`full_capacity` (mAh), `cell_voltages[]`, `cell_voltage_deviation`, `min/max_temperature` | Energy per flight (∫V·I), **`discharge_mah` for `battery_logs` (column exists, always null today)**, per-cell imbalance as a health metric (M4TD log shows a 4.1 V deviation spike — worth a look), true min/max pack temp. |
| Safety/health flags | `is_vibrating`, `is_compass_error`, `is_barometer_dead_in_air`, `is_acceletor_over_range`, `is_motor_blocked`, `wave_error`, `is_not_enough_force`, `is_out_of_limit`, `is_near_height_limit`, `is_near_distance_limit`, `voltage_warning`, `imu_init_fail_reason`, `motor_start_failed_cause` | All zero across the 7 samples, but a per-flight "anomaly flags" summary is free and feeds maintenance. |
| Config snapshot | `home.height_limit`, `go_home_height`, `max_allowed_height`, `is_beginner_mode`, `go_home_mode` | Height cap in force per flight (457 m on the M4TD, 500 m on the Mavics, 50 m on the Avata 2, and a 30 m cap for part of the Mavic 3 Pro flight). Useful context on any Part-107 altitude exceedance discussion (ADR-0029/0031 caveats stand). |
| Human-readable event log | `app.tip`, `app.warn` (frame) / `AppTip`, `AppWarn`, `AppSeriousWarn` (records) | Real strings from the samples: "GEO: You are in a Warning Zone (Airport)", "High Wind Velocity", "The remaining battery is only enough for RTH", "Remote controller disconnected. Adjust antennas" ×18, "Strong signal interference", "Obstacle sensing failed", "Switched to S (Sport) mode". This is the mission-report "events" section. **Caveat:** ~20% of strings come back with a garbled prefix from the crate's decrypt/append path; the tail of the message is intact, so dedupe on the clean suffix. |

Header-only extras also dropped today: `max_vertical_speed`, `capture_num`
(photos), `video_time` (seconds), `take_off_altitude` (appears to be
10× metres in the M4TD log — units need confirming before use),
`app_platform` (Android / DJIFly / Linux=Goggles).

## Tier 1 — in the raw record stream, one `records()` call away

The parser calls `log.frames()`; these record types never reach a `Frame`.

| Record | Count in M4TD log | What it enables |
|---|---|---|
| `AppGPS` | 657 | **Phone/RC position track.** Pilot location over time → aircraft-to-pilot distance series (VLOS distance), take-off spot vs pilot spot. Not present in Goggles-generated Avata/FPV logs. |
| `SmartBatteryGroup::SmartBatteryStatic` | 1 per battery | **Battery cycle count, designed capacity, full-charge voltage** straight from the pack. The crate reads these big-endian-wrong: `loop_times` 5888 = 0x1700 → 23 cycles (M4TD), 2304 → 9 cycles (M30); `designed_capacity` 1899520 → 7,420 mAh (M4TD, matches the pack spec), 1505282 → 5,880 mAh (M30 TB30, matches). Byte-swap shim needed; then `batteries.cycle_count` / `health_pct` can be auto-maintained instead of hand-entered. |
| `Camera` | 1,379 | `sd_card_remain_capacity`, `remain_photo_num`, `remain_video_timer`, `record_time`, `is_recording`, `is_heat`. Card-space-at-landing warning; recording seconds cross-check. |
| `Firmware` | 6 | Component firmware versions (camera 40.0.15 on the M4TD, 10.0.89 on the M30). Fleet firmware tracking in `aircraft.specs`; DJI-2027 longevity plan input. |
| `ComponentSerial` | 1 | Full 20-char aircraft serial (header has the 16-char form). |
| `MCParams` | 139 | Failsafe behaviour (GoHome vs Hover), obstacle-avoidance and MVO enabled/disabled per flight. Avata 2 flew with avoidance off; the enterprise birds on. |
| `OFDM` | 5,538 | Video-link signal percent series, separate from the RC link. |
| `RCDisplayField` / `RC` | 4,147 / 1,263 | Stick positions (364–1684) → pilot-input intensity, hands-off vs manual seconds. |

Not present in any of the 7 logs: embedded JPEG "moment pics" (0 records),
`RCGPS` (superseded by `AppGPS`), `VirtualStick`, `Deform`. Header address
fields are always "Map Loading" — useless.

## Tier 2 — present but undecodable with the current crate

`Unknown` record types make up ~30% of records by count in the newer logs
(types 5, 17, 26, 45, 48, 54, 55, 57, 63, 253, 254). Type 48 (80 bytes)
appears once per OSD frame on the M4TD/M30 and is almost certainly a newer
OSD extension. No decoder exists in `dji-log-parser 0.5.7`; check upstream
before any reverse-engineering. Not worth our time today.

## Housekeeping findings (not new data, but surfaced by the census)

- `flights.drone_model` stores the literal `Unknown(178)` / `Unknown(137)` /
  `Unknown(139)` for 150 of 210 DJI flights because the crate's
  `ProductType` enum predates the Mavic 4 Pro and Matrice 4 series. The
  header's `aircraft_name` ("Matrice 4TD", "DJI Mavic 4 Pro") is only in
  `raw_metadata`. Aircraft attribution is by serial (ADR-0007) so nothing is
  mis-attributed, but the column is ugly. Cheap fix: fall back to
  `aircraft_name` when product type is Unknown.
- Avata 2 logs report `gps_num` 0 throughout (Goggles log format), so the
  satellite series is meaningless for that airframe.

## Suggested order if/when this is picked up

1. Tier 0 timestamps + RC signal + distance-from-home + flight-mode timeline +
   camera edges (parser only, JSON blob grows; no schema change).
2. Battery: `discharge_mah`, energy, cell deviation into `battery_logs`;
   byte-swapped `SmartBatteryStatic` → `batteries.cycle_count`.
3. Event log (App* messages) into the flight record and the mission report.
4. `AppGPS` pilot track → VLOS distance series.
5. Reprocess pass over the retained logs to backfill. (Correction 2026-09-04: only 182 of the 210 have a retained file; see the ingestion plan §8.)

Nothing here is scheduled. Operator decides.
