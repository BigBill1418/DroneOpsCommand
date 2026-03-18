use crate::{ParsedFlight, TelemetryData, TrackPoint};

/// Parse a Litchi CSV flight log.
/// Litchi CSV columns typically: latitude, longitude, altitude(m), speed(m/s),
/// datetime(utc), etc.
pub fn parse_litchi_csv(
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

    // Check if this looks like a Litchi CSV (must have latitude/longitude columns)
    let header_lower: Vec<String> = headers.iter().map(|h| h.to_lowercase().trim().to_string()).collect();
    let lat_idx = header_lower.iter().position(|h| h == "latitude" || h == "lat");
    let lon_idx = header_lower.iter().position(|h| h == "longitude" || h == "lng" || h == "lon");

    if lat_idx.is_none() || lon_idx.is_none() {
        return Err(format!("{}: not a valid Litchi CSV (missing latitude/longitude columns)", filename));
    }

    let lat_idx = lat_idx.unwrap();
    let lon_idx = lon_idx.unwrap();
    let alt_idx = header_lower.iter().position(|h| h.contains("altitude") || h == "alt" || h == "altitude(m)");
    let speed_idx = header_lower.iter().position(|h| h.contains("speed") || h == "speed(m/s)");
    let time_idx = header_lower.iter().position(|h| h.contains("datetime") || h.contains("time") || h.contains("timestamp"));

    let mut track = Vec::new();
    let mut altitudes = Vec::new();
    let mut speeds_vec = Vec::new();
    let mut timestamps = Vec::new();
    let mut max_alt: f64 = 0.0;
    let mut max_speed: f64 = 0.0;
    let mut total_distance: f64 = 0.0;
    let mut prev_lat: Option<f64> = None;
    let mut prev_lon: Option<f64> = None;
    let mut home_lat: Option<f64> = None;
    let mut home_lon: Option<f64> = None;
    let mut start_time: Option<String> = None;
    let mut last_time: Option<String> = None;

    for result in reader.records() {
        let record = match result {
            Ok(r) => r,
            Err(_) => continue,
        };

        let lat: f64 = match record.get(lat_idx).and_then(|v| v.parse().ok()) {
            Some(v) if v.abs() > 0.001 => v,
            _ => continue,
        };
        let lon: f64 = match record.get(lon_idx).and_then(|v| v.parse().ok()) {
            Some(v) if v.abs() > 0.001 => v,
            _ => continue,
        };

        let alt = alt_idx.and_then(|i| record.get(i)).and_then(|v| v.parse::<f64>().ok()).unwrap_or(0.0);
        let speed = speed_idx.and_then(|i| record.get(i)).and_then(|v| v.parse::<f64>().ok()).unwrap_or(0.0);
        let time = time_idx.and_then(|i| record.get(i)).map(|v| v.to_string());

        if home_lat.is_none() {
            home_lat = Some(lat);
            home_lon = Some(lon);
        }
        if start_time.is_none() {
            start_time = time.clone();
        }
        last_time = time.clone();

        if let (Some(plat), Some(plon)) = (prev_lat, prev_lon) {
            total_distance += haversine(plat, plon, lat, lon);
        }
        prev_lat = Some(lat);
        prev_lon = Some(lon);

        if alt > max_alt { max_alt = alt; }
        if speed > max_speed { max_speed = speed; }

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
        if let Some(t) = time {
            timestamps.push(t);
        }
    }

    if track.is_empty() {
        return Err(format!("{}: no valid GPS data found in Litchi CSV", filename));
    }

    // Estimate duration from timestamps or point count
    let duration_secs = estimate_duration(&start_time, &last_time, track.len());

    let telemetry = if !altitudes.is_empty() {
        Some(TelemetryData {
            timestamps,
            altitude: altitudes,
            speed: speeds_vec,
            battery_pct: None,
            battery_voltage: None,
            battery_temp: None,
            satellites: None,
            signal_strength: None,
            distance_from_home: None,
        })
    } else {
        None
    };

    Ok(ParsedFlight {
        name: filename.to_string(),
        drone_model: Some("Litchi Flight".to_string()),
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
        battery_data: None,
        source: "litchi_csv".to_string(),
        file_hash: hash.to_string(),
        original_filename: filename.to_string(),
        raw_metadata: None,
    })
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
    // Try parsing timestamps to calculate duration
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
    // Fallback: assume ~1 second per data point
    point_count as f64
}
