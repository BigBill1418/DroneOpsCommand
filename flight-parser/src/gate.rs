//! GPS outlier / teleport gate for distance summation (ADR-0028, C1).
//!
//! Raw consecutive-point haversine summation is poisoned by a single
//! teleport: one bad fix turns a 3.6 km flight into a 12,583 km "orbital"
//! distance (the real f57c9373 OpenDroneLog row). The only pre-existing
//! filter rejected the null-island point (0,0) — it did nothing against a
//! valid-looking but physically-impossible jump.
//!
//! [`segment_ok`] is the per-segment physical-plausibility test applied
//! before a haversine segment is added to a flight's total distance. It is
//! intentionally identical in shape to the backend's
//! `app.services.flight_metrics.gated_track_distance` so the Rust parser, the
//! OpenDroneLog ingest clamp, and the data-repair migration all agree.

/// Hard physical upper bound on horizontal ground speed (m/s). 60 m/s ≈
/// 134 mph — far above any consumer/prosumer multirotor's max (~30 m/s) with
/// generous margin, so a *real* fast pass is never dropped, but a teleport
/// (thousands of m/s) always is.
pub const MAX_SEGMENT_SPEED_MS: f64 = 60.0;

/// Fallback bound (metres) used when no per-segment Δt is available. No
/// legitimate consecutive-fix gap exceeds this; a single segment longer than
/// half a kilometre with unknown timing is a teleport.
pub const MAX_SEGMENT_DISTANCE_M: f64 = 500.0;

/// Decide whether a haversine segment is physically plausible and should be
/// summed into the flight's total distance.
///
/// * When `dt_secs` is known and positive, gate on implied speed
///   (`dist_m / dt_secs <= MAX_SEGMENT_SPEED_MS`).
/// * Otherwise (no usable Δt — e.g. coarse 1 Hz elapsed-time counters where
///   consecutive frames share a timestamp), gate on raw segment length
///   (`dist_m <= MAX_SEGMENT_DISTANCE_M`).
pub fn segment_ok(dist_m: f64, dt_secs: Option<f64>) -> bool {
    match dt_secs {
        Some(dt) if dt > 0.0 => dist_m / dt <= MAX_SEGMENT_SPEED_MS,
        _ => dist_m <= MAX_SEGMENT_DISTANCE_M,
    }
}

/// Sanity-bound a header-reported duration against the wall-clock frame span
/// (ADR-0028, M9). DJI's `details.total_time` is authoritative and normally
/// trusted verbatim, but a corrupt header value (e.g. a garbage `total_time`
/// far larger than the actual flight) would otherwise pass through unchecked.
/// When BOTH a positive header duration and a positive datetime span exist and
/// the header exceeds the wall-clock span by more than `1.5×`, the header is
/// rejected in favour of the datetime span. Returns the duration to use.
pub fn sanity_bound_header(header: f64, datetime_span: Option<f64>) -> f64 {
    if header > 0.0 {
        if let Some(span) = datetime_span {
            if span > 0.0 && header > 1.5 * span {
                return span;
            }
        }
    }
    header
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn accepts_normal_segment_with_dt() {
        // 7 m in 0.1 s = 70 m/s? No — 7/0.1 = 70 > 60 → rejected. Use a
        // realistic 10 Hz fix: 3 m in 0.1 s = 30 m/s, accepted.
        assert!(segment_ok(3.0, Some(0.1)));
    }

    #[test]
    fn rejects_teleport_with_dt() {
        // 12,000,000 m in 1 s = orbital → rejected.
        assert!(!segment_ok(12_000_000.0, Some(1.0)));
    }

    #[test]
    fn accepts_normal_segment_without_dt() {
        assert!(segment_ok(7.6, None)); // largest real f57c9373 segment
        assert!(segment_ok(MAX_SEGMENT_DISTANCE_M, None)); // boundary inclusive
    }

    #[test]
    fn rejects_long_segment_without_dt() {
        assert!(!segment_ok(MAX_SEGMENT_DISTANCE_M + 0.1, None));
        assert!(!segment_ok(50_000.0, None));
    }

    #[test]
    fn zero_or_negative_dt_falls_back_to_distance_gate() {
        // Coarse 1 Hz counter: consecutive frames share the same second → dt 0.
        assert!(segment_ok(7.0, Some(0.0)));      // short segment kept
        assert!(!segment_ok(9999.0, Some(0.0)));  // teleport dropped
        assert!(segment_ok(7.0, Some(-1.0)));     // negative dt → distance gate
    }

    #[test]
    fn header_within_bound_is_trusted() {
        assert_eq!(sanity_bound_header(580.7, Some(580.7)), 580.7);
        assert_eq!(sanity_bound_header(580.7, Some(400.0)), 580.7); // 580.7 < 1.5*400
        assert_eq!(sanity_bound_header(300.0, None), 300.0); // no span → trust header
    }

    #[test]
    fn corrupt_header_is_bounded_to_datetime_span() {
        // header 100000 s vs wall-clock 600 s → reject header.
        assert_eq!(sanity_bound_header(100_000.0, Some(600.0)), 600.0);
    }
}
