use crate::{BatteryData, ParsedFlight, TelemetryData, TrackPoint};

/// Parse a DJI .txt binary flight log.
///
/// DJI logs v13+ are AES-encrypted and require a keychain from DJI's API.
/// This uses the `dji-log-parser` crate which handles all known DJI log versions.
///
/// When a DJI_API_KEY is provided, encrypted frame data (full telemetry, GPS track)
/// is decoded. Without it, we still extract summary data from the log header
/// (details struct), which always contains duration, distance, max height/speed,
/// aircraft info, and timestamps.
pub fn parse_dji_log(
    data: &[u8],
    filename: &str,
    hash: &str,
    api_key: Option<&str>,
) -> Result<ParsedFlight, String> {
    use dji_log_parser::DJILog;

    let log = DJILog::from_bytes(data.to_vec())
        .map_err(|e| format!("{}: failed to parse DJI log: {}", filename, e))?;

    // ── Header metadata (always available, even for encrypted logs) ──────
    let details = &log.details;

    let drone_model = {
        let raw = format!("{:?}", details.product_type);
        // Clean up Debug representation — e.g. "Mavic3" stays, "Unknown(42)" stays
        if raw.is_empty() { None } else { Some(raw) }
    };
    let drone_serial = if details.aircraft_sn.is_empty() {
        None
    } else {
        Some(details.aircraft_sn.clone())
    };
    let battery_serial = if details.battery_sn.is_empty() {
        None
    } else {
        Some(details.battery_sn.clone())
    };
    let aircraft_name = if details.aircraft_name.is_empty() {
        None
    } else {
        Some(details.aircraft_name.clone())
    };

    // Header-level summary data (fallback when frames are unavailable)
    let header_duration = details.total_time;           // seconds (f64)
    let header_distance = details.total_distance as f64; // meters (f32→f64)
    let header_max_height = details.max_height as f64;   // meters AGL (f32→f64)
    let header_max_hspeed = details.max_horizontal_speed as f64; // m/s (f32→f64)

    let start_time = {
        let ts = details.start_time.to_rfc3339();
        Some(ts)
    };

    let header_home_lat = if details.latitude.abs() > 0.001 {
        Some(details.latitude)
    } else {
        None
    };
    let header_home_lon = if details.longitude.abs() > 0.001 {
        Some(details.longitude)
    } else {
        None
    };

    tracing::info!(
        "{}: DJI log v{} — product={:?} sn={} duration={:.0}s distance={:.0}m maxAlt={:.0}m maxSpd={:.1}m/s",
        filename, log.version, details.product_type,
        drone_serial.as_deref().unwrap_or("n/a"),
        header_duration, header_distance, header_max_height, header_max_hspeed,
    );

    // ── Attempt to decode frames (requires keychain for v13+) ───────────
    let keychains = if let Some(key) = api_key {
        if key.is_empty() {
            tracing::info!("{}: DJI_API_KEY is empty, skipping keychain fetch", filename);
            None
        } else {
            match log.fetch_keychains(key) {
                Ok(kc) => {
                    tracing::info!("{}: fetched DJI keychains successfully", filename);
                    Some(kc)
                }
                Err(e) => {
                    tracing::warn!(
                        "{}: failed to fetch DJI keychains: {}. Using header data only.",
                        filename, e
                    );
                    None
                }
            }
        }
    } else {
        tracing::info!(
            "{}: no DJI_API_KEY configured — encrypted frame data unavailable, using header data",
            filename
        );
        None
    };

    let frames = match log.frames(keychains) {
        Ok(f) => {
            tracing::info!("{}: decoded {} frames", filename, f.len());
            f
        }
        Err(e) => {
            tracing::warn!(
                "{}: could not decode frames: {}. Using header summary data.",
                filename, e
            );
            Vec::new()
        }
    };

    // ── Extract GPS track and telemetry from frames ─────────────────────
    let mut track = Vec::new();
    let timestamps = Vec::new();
    let mut altitudes = Vec::new();
    let mut speeds = Vec::new();
    let mut battery_pcts = Vec::new();
    let mut battery_voltages = Vec::new();
    let mut battery_temps = Vec::new();
    let mut satellites_vec = Vec::new();
    let mut max_alt: f64 = 0.0;
    let mut max_speed: f64 = 0.0;
    let mut total_distance: f64 = 0.0;
    let mut prev_lat: Option<f64> = None;
    let mut prev_lon: Option<f64> = None;
    // Elapsed flight-time (seconds) of the previous accepted point, used to
    // derive per-segment Δt for the C1 outlier gate (ADR-0028).
    let mut prev_fly_time: Option<f64> = None;
    // Count of haversine segments rejected by the outlier gate (a teleport
    // would otherwise inflate total_distance by orders of magnitude).
    let mut dropped_segments: u64 = 0;
    let mut home_lat: Option<f64> = None;
    let mut home_lon: Option<f64> = None;
    let mut start_voltage: Option<f64> = None;
    let mut end_voltage: Option<f64> = None;
    let mut min_voltage: Option<f64> = None;
    let mut max_temp: Option<f64> = None;

    for frame in &frames {
        let osd = &frame.osd;
        let lat = osd.latitude;
        let lon = osd.longitude;
        let alt = osd.height as f64;
        let spd = ((osd.x_speed as f64).powi(2) + (osd.y_speed as f64).powi(2)).sqrt();

        if lat.abs() > 0.001 && lon.abs() > 0.001 {
            track.push(TrackPoint {
                lat,
                lng: lon,
                alt,
                timestamp: None,
                speed: Some(spd),
                heading: Some(osd.yaw as f64),
            });

            if home_lat.is_none() {
                home_lat = Some(lat);
                home_lon = Some(lon);
            }

            let cur_fly_time = osd.fly_time as f64;
            if let (Some(plat), Some(plon)) = (prev_lat, prev_lon) {
                let d = haversine(plat, plon, lat, lon);
                // Δt from DJI's elapsed flight-time counter (seconds). It is
                // coarse (often integer seconds), so when two consecutive
                // frames share a second the gate falls back to a raw-distance
                // bound — see crate::gate::segment_ok.
                let dt = prev_fly_time.map(|pt| cur_fly_time - pt);
                if crate::gate::segment_ok(d, dt) {
                    total_distance += d;
                } else {
                    dropped_segments += 1;
                }
            }
            prev_lat = Some(lat);
            prev_lon = Some(lon);
            prev_fly_time = Some(cur_fly_time);
        }

        altitudes.push(alt);
        speeds.push(spd);
        if alt > max_alt { max_alt = alt; }
        if spd > max_speed { max_speed = spd; }
        satellites_vec.push(osd.gps_num as u32);

        // Battery data
        let battery = &frame.battery;
        let voltage = frame_battery_voltage(battery.voltage);
        let pct = battery.charge_level as f64;
        let temp = battery.temperature as f64;

        battery_pcts.push(pct);
        battery_voltages.push(voltage);
        battery_temps.push(temp);

        if start_voltage.is_none() { start_voltage = Some(voltage); }
        end_voltage = Some(voltage);
        min_voltage = Some(min_voltage.map_or(voltage, |v: f64| v.min(voltage)));
        max_temp = Some(max_temp.map_or(temp, |t: f64| t.max(temp)));
    }

    // ── Use frame data when available, fall back to header summary ───────
    let has_frames = !frames.is_empty();

    // Duration (airtime). The authoritative value is the DJI log header's
    // `details.total_time` (`header_duration`). The previous implementation
    // discarded it and estimated `frames.len() / 10.0` — a hard-coded 10 Hz
    // divisor that inflated 15 Hz airframes (Mavic 4 Pro) by exactly 1.5× and
    // halved ~5 Hz airframes (DJI FPV). See ADR-0027. We now prefer the header
    // whenever present/positive, and only fall back to a frames-based estimate
    // derived from ACTUAL frame timestamps (model-agnostic), never a constant.
    let (fly_time_span, datetime_span) = if frames.len() >= 2 {
        let first = &frames[0];
        let last = &frames[frames.len() - 1];
        // DJI's own elapsed flight-time counter (seconds).
        let fly = (last.osd.fly_time - first.osd.fly_time) as f64;
        // Wall-clock span between the first and last decoded frame (seconds).
        let dt = (last.custom.date_time - first.custom.date_time)
            .num_milliseconds() as f64
            / 1000.0;
        (Some(fly), Some(dt))
    } else {
        (None, None)
    };
    // M9 (ADR-0028): a corrupt header `total_time` far larger than the actual
    // wall-clock flight span is rejected in favour of the datetime span before
    // it is trusted. For sane logs this is a no-op (header within 1.5× span).
    let bounded_header = crate::gate::sanity_bound_header(header_duration, datetime_span);
    let final_duration = choose_duration(bounded_header, fly_time_span, datetime_span);
    let final_distance = if has_frames && total_distance > 0.0 {
        total_distance
    } else {
        header_distance
    };
    let final_max_alt = if has_frames && max_alt > 0.0 {
        max_alt
    } else {
        header_max_height
    };
    let final_max_speed = if has_frames && max_speed > 0.0 {
        max_speed
    } else {
        header_max_hspeed
    };
    let final_home_lat = home_lat.or(header_home_lat);
    let final_home_lon = home_lon.or(header_home_lon);

    let point_count = track.len();

    let telemetry = if !altitudes.is_empty() {
        Some(TelemetryData {
            timestamps,
            altitude: altitudes,
            speed: speeds,
            battery_pct: if battery_pcts.is_empty() { None } else { Some(battery_pcts) },
            battery_voltage: if battery_voltages.is_empty() { None } else { Some(battery_voltages) },
            battery_temp: if battery_temps.is_empty() { None } else { Some(battery_temps) },
            satellites: if satellites_vec.is_empty() { None } else { Some(satellites_vec) },
            signal_strength: None,
            distance_from_home: None,
        })
    } else {
        None
    };

    let battery_data = if start_voltage.is_some() || battery_serial.is_some() {
        Some(BatteryData {
            serial: battery_serial.clone(),
            start_voltage,
            end_voltage,
            min_voltage,
            max_temp,
            discharge_mah: None,
        })
    } else {
        None
    };

    if dropped_segments > 0 {
        tracing::warn!(
            "{}: C1 outlier gate dropped {} GPS segment(s) from distance sum — \
             a teleport/bad-fix would otherwise have inflated total_distance",
            filename, dropped_segments,
        );
    }

    tracing::info!(
        "{}: final → duration={:.0}s distance={:.0}m maxAlt={:.0}m maxSpd={:.1}m/s points={} frames={} dropped_segs={} (from_frames={})",
        filename, final_duration, final_distance, final_max_alt, final_max_speed,
        point_count, frames.len(), dropped_segments, has_frames,
    );

    Ok(ParsedFlight {
        name: filename.to_string(),
        drone_model,
        drone_serial: drone_serial.clone(),
        battery_serial: battery_serial.clone(),
        start_time,
        duration_secs: final_duration,
        total_distance: final_distance,
        max_altitude: final_max_alt,
        max_speed: final_max_speed,
        home_lat: final_home_lat,
        home_lon: final_home_lon,
        point_count,
        gps_track: track,
        telemetry,
        battery_data,
        source: "dji_txt".to_string(),
        file_hash: hash.to_string(),
        original_filename: filename.to_string(),
        raw_metadata: Some(serde_json::json!({
            "product_type": format!("{:?}", details.product_type),
            "aircraft_name": aircraft_name,
            "aircraft_sn": drone_serial,
            "battery_sn": &battery_serial,
            "rc_sn": &details.rc_sn,
            "camera_sn": &details.camera_sn,
            "app_version": &details.app_version,
            "log_version": log.version,
            "header_duration": bounded_header,
            "header_duration_raw": header_duration,
            "header_distance": header_distance,
            "header_max_height": header_max_height,
            "header_max_hspeed": header_max_hspeed,
            "frames_decoded": has_frames,
            "frame_count": frames.len(),
            "dropped_segments": dropped_segments,
        })),
    })
}

/// Choose the flight's airtime in seconds (ADR-0027).
///
/// `header_duration` is the DJI log header's `details.total_time` — the
/// airframe's own recorded airtime, and the authoritative source. It is
/// preferred whenever present and positive.
///
/// Only when the header is absent/zero do we fall back to a frames-based
/// estimate, and that estimate is derived from ACTUAL frame timestamps —
/// DJI's `osd.fly_time` elapsed-flight-time counter first, then the wall-clock
/// span of `custom.date_time` — never a hard-coded sample rate. This makes the
/// fallback model-agnostic: it does not assume any particular frame cadence.
fn choose_duration(
    header_duration: f64,
    fly_time_span: Option<f64>,
    datetime_span: Option<f64>,
) -> f64 {
    if header_duration > 0.0 {
        return header_duration;
    }
    if let Some(s) = fly_time_span {
        if s > 0.0 {
            return s;
        }
    }
    if let Some(s) = datetime_span {
        if s > 0.0 {
            return s;
        }
    }
    header_duration // 0.0 — nothing better is available
}

/// Normalise a decoded frame battery voltage to volts.
///
/// `dji-log-parser` (0.5.7) already returns `FrameBattery.voltage` in volts:
/// its `SmartBattery` and `CenterBattery` record parsers map the raw `u16`
/// with `/1000.0` (crate `src/record/smart_battery.rs` and
/// `src/record/center_battery.rs`). No further scaling belongs here — a prior
/// `/ 1000.0` divided a second time, turning a 15.2 V pack into 0.0152 V and
/// disagreeing with the Airdata parser, which stores volts raw.
#[inline]
fn frame_battery_voltage(volts: f32) -> f64 {
    volts as f64
}

/// Haversine distance in meters between two lat/lon points
fn haversine(lat1: f64, lon1: f64, lat2: f64, lon2: f64) -> f64 {
    let r = 6371000.0;
    let dlat = (lat2 - lat1).to_radians();
    let dlon = (lon2 - lon1).to_radians();
    let a = (dlat / 2.0).sin().powi(2)
        + lat1.to_radians().cos() * lat2.to_radians().cos() * (dlon / 2.0).sin().powi(2);
    let c = 2.0 * a.sqrt().atan2((1.0 - a).sqrt());
    r * c
}

#[cfg(test)]
mod tests {
    use super::{choose_duration, frame_battery_voltage};

    // dji-log-parser already returns FrameBattery.voltage in VOLTS (its record
    // parsers apply /1000.0). The old inline `battery.voltage as f64 / 1000.0`
    // divided a second time: a 15.2 V pack became 0.0152 V. The normaliser must
    // pass volts through unchanged so DJI agrees with the Airdata parser.
    #[test]
    fn frame_voltage_is_volts_not_millivolts() {
        // Tolerance covers the f32→f64 widening (15.2f32 ≈ 15.2000007).
        assert!((frame_battery_voltage(15.2) - 15.2).abs() < 1e-3);
        assert!((frame_battery_voltage(25.2) - 25.2).abs() < 1e-3);
        // Regression guard against the historical double-divide (→ 0.0152 V).
        assert!(frame_battery_voltage(15.2) > 1.0);
    }

    // Mavic 4 Pro logs at 15 Hz. A real 580.7s flight produces 8708 frames;
    // the OLD code returned 8708/10 = 870.8s — a 1.500× inflation. The header
    // value must win regardless of frame count.
    #[test]
    fn header_preferred_no_15hz_inflation() {
        let header = 580.7;
        // Frame-derived spans agree with the header here; header still wins.
        assert_eq!(choose_duration(header, Some(580.7), Some(580.7)), 580.7);
        // Even if a frames-based estimate were wildly off (the old 870.8 bug),
        // it is never consulted while the header is present.
        assert_eq!(choose_duration(header, Some(870.8), Some(870.8)), 580.7);
    }

    // DJI FPV logs at ~5 Hz. The OLD code returned frames/10 ≈ 0.5× — halving
    // the airtime. The header must win and restore the true duration.
    #[test]
    fn header_preferred_no_5hz_undercount() {
        assert_eq!(choose_duration(300.0, Some(150.0), Some(150.0)), 300.0);
    }

    // No header → derive from DJI's own elapsed flight-time counter.
    #[test]
    fn fallback_uses_fly_time_span() {
        assert_eq!(choose_duration(0.0, Some(123.4), Some(999.0)), 123.4);
    }

    // fly_time flat/degenerate → fall back to the wall-clock frame span.
    #[test]
    fn fallback_uses_datetime_span_when_fly_time_degenerate() {
        assert_eq!(choose_duration(0.0, Some(0.0), Some(245.0)), 245.0);
        assert_eq!(choose_duration(0.0, None, Some(245.0)), 245.0);
    }

    // Nothing usable → 0.0 (no fabricated duration).
    #[test]
    fn no_header_no_frames_yields_zero() {
        assert_eq!(choose_duration(0.0, None, None), 0.0);
        assert_eq!(choose_duration(0.0, Some(0.0), Some(0.0)), 0.0);
    }
}
