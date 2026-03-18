use crate::{BatteryData, ParsedFlight, TelemetryData, TrackPoint};

/// Parse a DJI .txt binary flight log.
///
/// DJI logs v13+ are AES-encrypted and require a keychain from DJI's API.
/// This uses the `dji-log-parser` crate which handles all known DJI log versions.
pub fn parse_dji_log(
    data: &[u8],
    filename: &str,
    hash: &str,
    _api_key: Option<&str>,
) -> Result<ParsedFlight, String> {
    // Attempt to parse the DJI log using the dji-log-parser crate
    use dji_log_parser::DJILog;

    let log = DJILog::from_bytes(data.to_vec())
        .map_err(|e| format!("{}: failed to parse DJI log: {}", filename, e))?;

    // Get basic metadata from the log header
    let details = &log.details;

    let drone_model = Some(format!("{:?}", details.product_type));

    // Try to get frames — pass None for keychains (works for unencrypted logs)
    let frames = match log.frames(None) {
        Ok(f) => f,
        Err(e) => {
            tracing::warn!(
                "{}: Could not decode frames: {}. Returning metadata only.",
                filename, e
            );
            Vec::new()
        }
    };

    // Extract GPS track and telemetry from frames
    let mut track = Vec::new();
    let mut timestamps = Vec::new();
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
    let mut battery_serial: Option<String> = None;
    let mut start_voltage: Option<f64> = None;
    let mut end_voltage: Option<f64> = None;
    let mut min_voltage: Option<f64> = None;
    let mut max_temp: Option<f64> = None;

    for frame in &frames {
        // Extract OSD (On-Screen Display) data — primary flight telemetry
        // In dji-log-parser 0.5.7, osd and battery are direct structs, not Options
        let osd = &frame.osd;
        let lat = osd.latitude;
        let lon = osd.longitude;
        let alt = osd.altitude as f64;
        let spd = osd.speed as f64;

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

            // Calculate distance from previous point
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
        satellites_vec.push(osd.satellite_count as u32);

        // Extract battery data
        let battery = &frame.battery;
        let voltage = battery.voltage as f64 / 1000.0; // mV to V
        let pct = battery.charge_percent as f64;
        let temp = battery.temperature as f64;

        battery_pcts.push(pct);
        battery_voltages.push(voltage);
        battery_temps.push(temp);

        if start_voltage.is_none() { start_voltage = Some(voltage); }
        end_voltage = Some(voltage);
        min_voltage = Some(min_voltage.map_or(voltage, |v: f64| v.min(voltage)));
        max_temp = Some(max_temp.map_or(temp, |t: f64| t.max(temp)));
    }

    // Estimate duration from frame count (~10 frames/sec typical for DJI logs)
    let duration_secs = if !frames.is_empty() {
        frames.len() as f64 / 10.0
    } else {
        0.0
    };
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

    Ok(ParsedFlight {
        name: filename.to_string(),
        drone_model,
        drone_serial: None,
        battery_serial,
        start_time: None,
        duration_secs,
        total_distance,
        max_altitude: max_alt,
        max_speed,
        home_lat,
        home_lon,
        point_count,
        gps_track: track,
        telemetry,
        battery_data,
        source: "dji_txt".to_string(),
        file_hash: hash.to_string(),
        original_filename: filename.to_string(),
        raw_metadata: Some(serde_json::json!({
            "product_type": format!("{:?}", details.product_type),
            "app_version": details.app_version,
        })),
    })
}

/// Haversine distance in meters between two lat/lon points
fn haversine(lat1: f64, lon1: f64, lat2: f64, lon2: f64) -> f64 {
    let r = 6371000.0; // Earth radius in meters
    let dlat = (lat2 - lat1).to_radians();
    let dlon = (lon2 - lon1).to_radians();
    let a = (dlat / 2.0).sin().powi(2)
        + lat1.to_radians().cos() * lat2.to_radians().cos() * (dlon / 2.0).sin().powi(2);
    let c = 2.0 * a.sqrt().atan2((1.0 - a).sqrt());
    r * c
}
