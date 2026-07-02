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
    let speed_idx = header_lower.iter().position(|h| h.contains("speed"));
    // Litchi exports speed in mph by default (header `speed(mph)`); some
    // exports use km/h. The number carries no unit, so detect it from the
    // matched header and normalise to m/s below. Treating an mph value as m/s
    // inflates max_speed and every track speed by ~2.237× (km/h by ~3.6×).
    let (speed_is_mph, speed_is_kmh) = match speed_idx {
        Some(i) => {
            let h = header_lower[i].as_str();
            (
                h.contains("mph"),
                h.contains("km/h") || h.contains("kmh") || h.contains("kph") || h.contains("km-h"),
            )
        }
        None => (false, false),
    };
    // Prefer an explicit `datetime` column. Litchi CSVs also carry a numeric
    // epoch-ms `timestamp` column; binding it as the wall-clock source yields
    // gross duration errors (it is not a parseable datetime), so it must never
    // win over `datetime(utc)` and is excluded from the substring fallback.
    let time_idx = header_lower.iter().position(|h| h.contains("datetime"))
        .or_else(|| header_lower.iter().position(|h| h.contains("time") && !h.contains("timestamp")));

    let mut track = Vec::new();
    let mut altitudes = Vec::new();
    let mut speeds_vec = Vec::new();
    let mut timestamps = Vec::new();
    let mut max_alt: f64 = 0.0;
    let mut max_speed: f64 = 0.0;
    let mut total_distance: f64 = 0.0;
    let mut prev_lat: Option<f64> = None;
    let mut prev_lon: Option<f64> = None;
    let mut prev_ts: Option<chrono::NaiveDateTime> = None;
    let mut dropped_segments: u64 = 0;
    let mut home_lat: Option<f64> = None;
    let mut home_lon: Option<f64> = None;
    let mut start_time: Option<String> = None;
    let mut last_time: Option<String> = None;

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

        let alt = alt_idx.and_then(|i| record.get(i)).and_then(|v| v.parse::<f64>().ok()).unwrap_or(0.0);
        let mut speed = speed_idx.and_then(|i| record.get(i)).and_then(|v| v.parse::<f64>().ok()).unwrap_or(0.0);
        let time = time_idx.and_then(|i| record.get(i)).map(|v| v.to_string());

        // Normalise speed to m/s (Litchi exports mph or km/h; detected above).
        if speed_is_mph { speed *= 0.44704; }
        else if speed_is_kmh { speed *= 0.277778; }

        if home_lat.is_none() {
            home_lat = Some(lat);
            home_lon = Some(lon);
        }
        if start_time.is_none() {
            start_time = time.clone();
        }
        last_time = time.clone();

        let cur_ts = time.as_deref().and_then(parse_ts);
        if let (Some(plat), Some(plon)) = (prev_lat, prev_lon) {
            let d = haversine(plat, plon, lat, lon);
            let dt = match (prev_ts, cur_ts) {
                (Some(p), Some(c)) => Some(c.signed_duration_since(p).num_milliseconds() as f64 / 1000.0),
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

    if dropped_segments > 0 {
        tracing::warn!(
            "{}: C1 outlier gate dropped {} GPS segment(s) from Litchi distance sum",
            filename, dropped_segments,
        );
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

fn parse_ts(s: &str) -> Option<chrono::NaiveDateTime> {
    let trimmed = s.trim();
    chrono::NaiveDateTime::parse_from_str(trimmed, "%Y-%m-%d %H:%M:%S")
        .or_else(|_| chrono::NaiveDateTime::parse_from_str(trimmed, "%Y-%m-%dT%H:%M:%S"))
        .or_else(|_| chrono::NaiveDateTime::parse_from_str(trimmed, "%Y/%m/%d %H:%M:%S"))
        .ok()
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
/// sample rate, which is the Litchi CSV default but not guaranteed — a
/// higher-rate export with unparseable timestamps will under-report duration.
/// Timestamps are therefore always preferred; the fallback is best-effort.
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
    // Fallback: assume ~1 second per data point (see L2 caveat above).
    point_count as f64
}

#[cfg(test)]
mod tests {
    use super::parse_litchi_csv;

    // Litchi exports speed in mph by default. Treating the number as m/s
    // inflates every speed by ~2.237×. 10 mph must become 4.4704 m/s.
    #[test]
    fn mph_speed_converted_to_ms() {
        let csv = "\
latitude,longitude,altitude(m),speed(mph),datetime(utc)
45.5000,-122.6000,10.0,5.0,2024-01-01 12:00:00
45.5001,-122.6000,12.0,10.0,2024-01-01 12:00:01
";
        let f = parse_litchi_csv(csv.as_bytes(), "t.csv", "h").unwrap();
        assert!((f.max_speed - 4.4704).abs() < 1e-6, "max_speed = {}", f.max_speed);
        let speeds = &f.telemetry.as_ref().unwrap().speed;
        assert!((speeds[0] - 5.0 * 0.44704).abs() < 1e-6, "speed[0] = {}", speeds[0]);
    }

    // Some Litchi exports use km/h; 18 km/h must become 5.0 m/s.
    #[test]
    fn kmh_speed_converted_to_ms() {
        let csv = "\
latitude,longitude,altitude(m),speed(km/h),datetime(utc)
45.5000,-122.6000,10.0,9.0,2024-01-01 12:00:00
45.5001,-122.6000,12.0,18.0,2024-01-01 12:00:01
";
        let f = parse_litchi_csv(csv.as_bytes(), "t.csv", "h").unwrap();
        assert!((f.max_speed - 5.0).abs() < 1e-3, "max_speed = {}", f.max_speed);
    }

    // Litchi CSVs carry a numeric epoch-ms `timestamp` column alongside
    // `datetime(utc)`. The `datetime` column must win even when `timestamp`
    // appears first, or duration collapses to the point-count fallback.
    #[test]
    fn prefers_datetime_over_numeric_timestamp() {
        let csv = "\
latitude,longitude,altitude(m),speed(mph),timestamp,datetime(utc)
45.5000,-122.6000,10.0,5.0,1704110400000,2024-01-01 12:00:00
45.5001,-122.6000,12.0,10.0,1704110460000,2024-01-01 12:01:00
";
        let f = parse_litchi_csv(csv.as_bytes(), "t.csv", "h").unwrap();
        assert_eq!(f.duration_secs, 60.0, "duration_secs = {}", f.duration_secs);
        assert_eq!(f.start_time.as_deref(), Some("2024-01-01 12:00:00"));
    }
}
