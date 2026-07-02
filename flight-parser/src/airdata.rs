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

    // Height/altitude — Airdata often uses feet. Prefer AGL (height above
    // takeoff, both feet and metric) over MSL (sea level); the sea-level
    // candidate is last resort. NB the earlier candidate string had a stray
    // capital 'L' matched against a lowercased header, so it never matched —
    // dead code, now lowercased and demoted below the AGL/relative columns so
    // a metric export with both height_above_takeoff(m) and
    // altitude_above_sealevel(m) reports the AGL value, not MSL.
    let alt_idx = find_col(&header_lower, &[
        "height_above_takeoff(feet)",
        "height_above_takeoff(m)",
        "altitude(m)",
        "altitude",
        "height",
        "altitude_above_sealevel(feet)",
    ]);
    let speed_idx = find_col(&header_lower, &["speed(mph)", "speed(km/h)", "speed(m/s)", "speed"]);
    let time_idx = find_col(&header_lower, &["datetime(utc)", "datetime", "time", "timestamp"]);
    let batt_idx = find_col(&header_lower, &["battery_percent", "battery(%)", "battery"]);
    let voltage_idx = find_col(&header_lower, &["voltage(v)", "battery_voltage", "voltage"]);
    let satellites_idx = find_col(&header_lower, &["satellites", "gps_satellites", "nsats"]);

    // Detect altitude/speed units from the matched header.
    let alt_is_feet = alt_idx.map(|i| header_lower[i].contains("feet")).unwrap_or(false);
    let speed_hdr = speed_idx.map(|i| header_lower[i].as_str()).unwrap_or("");
    let speed_is_mph = speed_hdr.contains("mph");
    // Metric Airdata exports report km/h; without this branch the value is
    // treated as m/s and inflated ~3.6×.
    let speed_is_kmh = speed_hdr.contains("km/h") || speed_hdr.contains("kmh")
        || speed_hdr.contains("kph") || speed_hdr.contains("km-h");

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
    // Previous accepted point's parsed timestamp + dropped-segment count for
    // the C1 outlier gate (ADR-0028).
    let mut prev_ts: Option<chrono::NaiveDateTime> = None;
    let mut dropped_segments: u64 = 0;
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
        let lon: f64 = match record.get(lon_idx).and_then(|v| v.parse::<f64>().ok()) {
            Some(v) if v.abs() > 0.001 => v,
            _ => continue,
        };

        let mut alt = alt_idx.and_then(|i| record.get(i)).and_then(|v| v.parse::<f64>().ok()).unwrap_or(0.0);
        let mut speed = speed_idx.and_then(|i| record.get(i)).and_then(|v| v.parse::<f64>().ok()).unwrap_or(0.0);
        let time = time_idx.and_then(|i| record.get(i)).map(|v| v.to_string());

        // Convert units to metric
        if alt_is_feet { alt *= 0.3048; }  // feet to meters
        if speed_is_mph { speed *= 0.44704; }  // mph to m/s
        else if speed_is_kmh { speed *= 0.277778; }  // km/h to m/s

        if home_lat.is_none() {
            home_lat = Some(lat);
            home_lon = Some(lon);
        }
        if start_time.is_none() { start_time = time.clone(); }
        last_time = time.clone();

        let cur_ts = time.as_deref().and_then(parse_ts);
        if let (Some(plat), Some(plon)) = (prev_lat, prev_lon) {
            let d = haversine(plat, plon, lat, lon);
            let dt = match (prev_ts, cur_ts) {
                (Some(p), Some(c)) => {
                    let s = c.signed_duration_since(p).num_milliseconds() as f64 / 1000.0;
                    Some(s)
                }
                _ => None,
            };
            if crate::gate::segment_ok(d, dt) {
                total_distance += d;
            } else {
                dropped_segments += 1;
            }
        }
        prev_lat = Some(lat);
        prev_lon = Some(lon);
        prev_ts = cur_ts;

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

    if dropped_segments > 0 {
        tracing::warn!(
            "{}: C1 outlier gate dropped {} GPS segment(s) from Airdata distance sum",
            filename, dropped_segments,
        );
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

/// Parse an Airdata timestamp ("YYYY-MM-DD HH:MM:SS", optionally with a
/// trailing fractional/zone suffix we ignore) into a NaiveDateTime for the
/// per-segment Δt used by the C1 outlier gate. Returns None on any failure —
/// the gate then falls back to its raw-distance bound.
fn parse_ts(s: &str) -> Option<chrono::NaiveDateTime> {
    let trimmed = s.trim();
    chrono::NaiveDateTime::parse_from_str(trimmed, "%Y-%m-%d %H:%M:%S")
        .or_else(|_| chrono::NaiveDateTime::parse_from_str(trimmed, "%Y-%m-%dT%H:%M:%S"))
        .or_else(|_| chrono::NaiveDateTime::parse_from_str(trimmed, "%Y/%m/%d %H:%M:%S"))
        .ok()
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

/// Estimate flight duration from first/last timestamps, falling back to the
/// point count. L2 (ADR-0028) caveat: the point-count fallback ASSUMES a ~1 Hz
/// sample rate — preferred path is always the parsed timestamp span.
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

#[cfg(test)]
mod tests {
    use super::parse_airdata_csv;

    // Metric Airdata exports report speed in km/h. Without a km/h branch the
    // value is treated as m/s and inflated ~3.6×. 18 km/h must become 5.0 m/s.
    #[test]
    fn kmh_speed_converted_to_ms() {
        let csv = "\
latitude,longitude,altitude(m),speed(km/h),datetime(utc)
45.5000,-122.6000,10.0,9.0,2024-01-01 12:00:00
45.5001,-122.6000,12.0,18.0,2024-01-01 12:00:01
";
        let f = parse_airdata_csv(csv.as_bytes(), "t.csv", "h").unwrap();
        assert!((f.max_speed - 5.0).abs() < 1e-3, "max_speed = {}", f.max_speed);
    }

    // Existing behaviour must hold: mph → m/s (regression guard).
    #[test]
    fn mph_speed_converted_to_ms() {
        let csv = "\
latitude,longitude,altitude(m),speed(mph),datetime(utc)
45.5000,-122.6000,10.0,5.0,2024-01-01 12:00:00
45.5001,-122.6000,12.0,10.0,2024-01-01 12:00:01
";
        let f = parse_airdata_csv(csv.as_bytes(), "t.csv", "h").unwrap();
        assert!((f.max_speed - 4.4704).abs() < 1e-6, "max_speed = {}", f.max_speed);
    }

    // AGL (height above takeoff) must be preferred over MSL (sea level) for
    // max_altitude. A metric export exposing both must report the AGL value.
    #[test]
    fn prefers_agl_over_sealevel() {
        let csv = "\
latitude,longitude,altitude_above_seaLevel(m),height_above_takeoff(m),speed(m/s),datetime(utc)
45.5000,-122.6000,500.0,50.0,1.0,2024-01-01 12:00:00
45.5001,-122.6000,505.0,55.0,2.0,2024-01-01 12:00:01
";
        let f = parse_airdata_csv(csv.as_bytes(), "t.csv", "h").unwrap();
        assert!((f.max_altitude - 55.0).abs() < 1e-6, "max_altitude = {}", f.max_altitude);
    }

    // The lowercased sea-level candidate must still match and detect feet when
    // it is the only altitude column (regression guard on unit conversion).
    #[test]
    fn sealevel_feet_converted_to_meters() {
        let csv = "\
latitude,longitude,altitude_above_seaLevel(feet),speed(m/s),datetime(utc)
45.5000,-122.6000,328.084,1.0,2024-01-01 12:00:00
45.5001,-122.6000,328.084,2.0,2024-01-01 12:00:01
";
        let f = parse_airdata_csv(csv.as_bytes(), "t.csv", "h").unwrap();
        // 328.084 ft = 100.0 m
        assert!((f.max_altitude - 100.0).abs() < 1e-2, "max_altitude = {}", f.max_altitude);
    }
}
