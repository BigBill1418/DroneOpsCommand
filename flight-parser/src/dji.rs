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

            if let (Some(plat), Some(plon)) = (prev_lat, prev_lon) {
                total_distance += haversine(plat, plon, lat, lon);
            }
            prev_lat = Some(lat);
            prev_lon = Some(lon);
        }

        altitudes.push(alt);
        speeds.push(spd);
        if alt > max_alt { max_alt = alt; }
        if spd > max_speed { max_speed = spd; }
        satellites_vec.push(osd.gps_num as u32);

        // Battery data
        let battery = &frame.battery;
        let voltage = battery.voltage as f64 / 1000.0;
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

    let final_duration = if has_frames {
        frames.len() as f64 / 10.0 // ~10 frames/sec typical
    } else {
        header_duration
    };
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

    tracing::info!(
        "{}: final → duration={:.0}s distance={:.0}m maxAlt={:.0}m maxSpd={:.1}m/s points={} frames={} (from_frames={})",
        filename, final_duration, final_distance, final_max_alt, final_max_speed,
        point_count, frames.len(), has_frames,
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
            "header_duration": header_duration,
            "header_distance": header_distance,
            "header_max_height": header_max_height,
            "header_max_hspeed": header_max_hspeed,
            "frames_decoded": has_frames,
            "frame_count": frames.len(),
        })),
    })
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
