use crate::{BatteryData, ParsedFlight, TelemetryData, TrackPoint};

/// Parse an Airdata CSV export.
/// Airdata exports have columns like: latitude, longitude, altitude_above_seaLevel(feet),
/// height_above_takeoff(feet), speed(mph), datetime(utc), battery_percent, etc.
pub fn parse_airdata_csv(
    data: &[u8],
    filename: &str,
    hash: &str,
) -> Result<ParsedFlight, String> {
    let content = std::str::from_utf8(data)
        .map_err(|e| format!("{}: invalid UTF-8: {}", filename, e))?;

    let mut reader = csv::ReaderBuilder::new()
        .flexible(true)
        .from_reader(content.as_bytes());

    let headers = reader.headers()
        .map_err(|e| format!("{}: failed to read CSV headers: {}", filename, e))?
        .clone();

    let header_lower: Vec<String> = headers.iter().map(|h| h.to_lowercase().trim().to_string()).collect();

    // Airdata uses various column naming patterns
    let lat_idx = find_col(&header_lower, &["latitude", "lat"]);
    let lon_idx = find_col(&header_lower, &["longitude", "lng", "lon"]);

    if lat_idx.is_none() || lon_idx.is_none() {
        return Err(format!("{}: not a valid Airdata CSV (missing lat/lon columns)", filename));
    }

    let lat_idx = lat_idx.unwrap();
    let lon_idx = lon_idx.unwrap();

    // Height/altitude — Airdata often uses feet
    let alt_idx = find_col(&header_lower, &["height_above_takeoff(feet)", "altitude_above_seaLevel(feet)", "altitude(m)", "altitude", "height"]);
    let speed_idx = find_col(&header_lower, &["speed(mph)", "speed(m/s)", "speed"]);
    let time_idx = find_col(&header_lower, &["datetime(utc)", "datetime", "time", "timestamp"]);
    let batt_idx = find_col(&header_lower, &["battery_percent", "battery(%)", "battery"]);
    let voltage_idx = find_col(&header_lower, &["voltage(v)", "battery_voltage", "voltage"]);
    let satellites_idx = find_col(&header_lower, &["satellites", "gps_satellites", "nsats"]);

    // Detect if altitude is in feet
    let alt_is_feet = alt_idx.map(|i| header_lower[i].contains("feet")).unwrap_or(false);
    let speed_is_mph = speed_idx.map(|i| header_lower[i].contains("mph")).unwrap_or(false);

    let mut track = Vec::new();
    let mut altitudes = Vec::new();
    let mut speeds_vec = Vec::new();
    let mut timestamps = Vec::new();
    let mut battery_pcts = Vec::new();
    let mut battery_voltages = Vec::new();
    let mut sats_vec: Vec<u32> = Vec::new();
    let mut max_alt: f64 = 0.0;
    let mut max_speed: f64 = 0.0;
    let mut total_distance: f64 = 0.0;
    let mut prev_lat: Option<f64> = None;
    let mut prev_lon: Option<f64> = None;
    let mut home_lat: Option<f64> = None;
    let mut home_lon: Option<f64> = None;
    let mut start_time: Option<String> = None;
    let mut last_time: Option<String> = None;
    let mut start_voltage: Option<f64> = None;
    let mut end_voltage: Option<f64> = None;
    let mut min_voltage: Option<f64> = None;

    for result in reader.records() {
        let record = match result {
            Ok(r) => r,
            Err(_) => continue,
        };

        let lat: f64 = match record.get(lat_idx).and_then(|v| v.parse::<f64>().ok()) {
            Some(v) if v.abs() > 0.001 => v,
            _ => continue,
        };
        let lon: f64 = match record.get(lon_idx).and_then(|v| v.parse().ok()) {
            Some(v) if v.abs() > 0.001 => v,
            _ => continue,
        };

        let mut alt = alt_idx.and_then(|i| record.get(i)).and_then(|v| v.parse::<f64>().ok()).unwrap_or(0.0);
        let mut speed = speed_idx.and_then(|i| record.get(i)).and_then(|v| v.parse::<f64>().ok()).unwrap_or(0.0);
        let time = time_idx.and_then(|i| record.get(i)).map(|v| v.to_string());

        // Convert units to metric
        if alt_is_feet { alt *= 0.3048; }  // feet to meters
        if speed_is_mph { speed *= 0.44704; }  // mph to m/s

        if home_lat.is_none() {
            home_lat = Some(lat);
            home_lon = Some(lon);
        }
        if start_time.is_none() { start_time = time.clone(); }
        last_time = time.clone();

        if let (Some(plat), Some(plon)) = (prev_lat, prev_lon) {
            total_distance += haversine(plat, plon, lat, lon);
        }
        prev_lat = Some(lat);
        prev_lon = Some(lon);

        if alt > max_alt { max_alt = alt; }
        if speed > max_speed { max_speed = speed; }

        // Battery data
        if let Some(pct) = batt_idx.and_then(|i| record.get(i)).and_then(|v| v.parse::<f64>().ok()) {
            battery_pcts.push(pct);
        }
        if let Some(v) = voltage_idx.and_then(|i| record.get(i)).and_then(|v| v.parse::<f64>().ok()) {
            battery_voltages.push(v);
            if start_voltage.is_none() { start_voltage = Some(v); }
            end_voltage = Some(v);
            min_voltage = Some(min_voltage.map_or(v, |mv: f64| mv.min(v)));
        }
        if let Some(s) = satellites_idx.and_then(|i| record.get(i)).and_then(|v| v.parse::<u32>().ok()) {
            sats_vec.push(s);
        }

        track.push(TrackPoint {
            lat,
            lng: lon,
            alt,
            timestamp: time.clone(),
            speed: Some(speed),
            heading: None,
        });
        altitudes.push(alt);
        speeds_vec.push(speed);
        if let Some(t) = time { timestamps.push(t); }
    }

    if track.is_empty() {
        return Err(format!("{}: no valid GPS data found in Airdata CSV", filename));
    }

    let duration_secs = estimate_duration(&start_time, &last_time, track.len());

    let telemetry = Some(TelemetryData {
        timestamps,
        altitude: altitudes,
        speed: speeds_vec,
        battery_pct: if battery_pcts.is_empty() { None } else { Some(battery_pcts) },
        battery_voltage: if battery_voltages.is_empty() { None } else { Some(battery_voltages) },
        battery_temp: None,
        satellites: if sats_vec.is_empty() { None } else { Some(sats_vec) },
        signal_strength: None,
        distance_from_home: None,
    });

    let battery_data = if start_voltage.is_some() {
        Some(BatteryData {
            serial: None,
            start_voltage,
            end_voltage,
            min_voltage,
            max_temp: None,
            discharge_mah: None,
        })
    } else {
        None
    };

    Ok(ParsedFlight {
        name: filename.to_string(),
        drone_model: Some("Airdata Import".to_string()),
        drone_serial: None,
        battery_serial: None,
        start_time,
        duration_secs,
        total_distance,
        max_altitude: max_alt,
        max_speed,
        home_lat,
        home_lon,
        point_count: track.len(),
        gps_track: track,
        telemetry,
        battery_data,
        source: "airdata_csv".to_string(),
        file_hash: hash.to_string(),
        original_filename: filename.to_string(),
        raw_metadata: None,
    })
}

fn find_col(headers: &[String], candidates: &[&str]) -> Option<usize> {
    for candidate in candidates {
        if let Some(idx) = headers.iter().position(|h| h == *candidate || h.contains(candidate)) {
            return Some(idx);
        }
    }
    None
}

fn haversine(lat1: f64, lon1: f64, lat2: f64, lon2: f64) -> f64 {
    let r = 6371000.0;
    let dlat = (lat2 - lat1).to_radians();
    let dlon = (lon2 - lon1).to_radians();
    let a = (dlat / 2.0).sin().powi(2)
        + lat1.to_radians().cos() * lat2.to_radians().cos() * (dlon / 2.0).sin().powi(2);
    let c = 2.0 * a.sqrt().atan2((1.0 - a).sqrt());
    r * c
}

fn estimate_duration(start: &Option<String>, end: &Option<String>, point_count: usize) -> f64 {
    if let (Some(s), Some(e)) = (start, end) {
        if let (Ok(start_dt), Ok(end_dt)) = (
            chrono::NaiveDateTime::parse_from_str(s, "%Y-%m-%d %H:%M:%S"),
            chrono::NaiveDateTime::parse_from_str(e, "%Y-%m-%d %H:%M:%S"),
        ) {
            let diff = end_dt.signed_duration_since(start_dt);
            if diff.num_seconds() > 0 {
                return diff.num_seconds() as f64;
            }
        }
    }
    point_count as f64
}
