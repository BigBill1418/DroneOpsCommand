//! Tier 0 extended-log extraction — everything reachable from `log.frames()`.
//!
//! FP-1 phase P1 (ADR-0043 / `docs/plans/2026-09-04-flight-details-data-ingestion.md`
//! §2.2, §2.5, §2.6). This is a set of accumulators driven from the frame loop
//! that already exists in [`crate::dji`] — there is **no second decode** and no
//! second keychain round-trip.
//!
//! # Units (ADR-0032)
//!
//! Metres, m/s, volts, amps, °C, degrees, mAh, Wh, seconds, Hz — with the unit
//! in the name. Values arrive from `dji-log-parser` 0.5.7 already scaled:
//! `SmartBatteryDynamic.current_current` maps `|x| / 1000` so it is **amps**,
//! `current_voltage` maps `/1000` so it is **volts**, `temperature` maps `/10`
//! so it is **°C**, and capacities are raw **mAh**. Nothing here re-scales any
//! of them; ADR-0032 exists because a second `/1000.0` on voltage turned a
//! 15.2 V pack into 0.0152 V.
//!
//! # Full resolution, and the rounding that pays for it (§2.5)
//!
//! Two rules, in this order:
//!
//! 1. Every **scalar** is computed at full frame resolution — maxima, minima,
//!    edge counts and the `∫V·I dt` / `∫I dt` integrals never see a reduced
//!    array.
//! 2. Every **series** is stored at full resolution, one value per frame. No
//!    decimation at rest; that happens only in the API layer.
//!
//! Rounding is not downsampling. `193.90000000000001` → `193.9` drops spurious
//! f64 mantissa digits and nothing else: every sample is kept, and the stored
//! text is ~4x smaller, which is what makes full resolution affordable.
//!
//! # Missing samples are `null`, never `0.0`
//!
//! Series values are `Option<f64>` and serialise to JSON `null` when the
//! aircraft did not report a value — an RC link that has not yet produced an
//! OFDM record, or a distance-from-home with no GPS fix. Substituting `0.0`
//! would put a number on a chart that the aircraft never reported, which is
//! the fabrication ADR-0028's posture forbids. A gap reads as a gap.

use std::collections::HashMap;

use chrono::{DateTime, Utc};
use dji_log_parser::frame::Frame;
use serde::{Deserialize, Serialize};

// ── Per-quantity precision (§2.5) ──────────────────────────────────────
// Each is the finest precision the underlying sensor can justify. Emitting
// more digits stores noise; emitting fewer discards signal.

/// 0.1 m — below any DJI altitude/range sensor's real accuracy.
pub const DP_DISTANCE_M: i16 = 1;
/// 0.01 m/s.
pub const DP_SPEED_MS: i16 = 2;
/// 0.1 deg.
pub const DP_ANGLE_DEG: i16 = 1;
/// mV — matches the crate's own `/1000.0` mapping.
pub const DP_VOLTAGE_V: i16 = 3;
/// 0.01 A.
pub const DP_CURRENT_A: i16 = 2;
/// Integers as reported.
pub const DP_PERCENT: i16 = 0;
/// 10 ms — finer than any observed frame cadence.
pub const DP_TIME_S: i16 = 2;

pub const UNIT_M: &str = "m";
pub const UNIT_MS: &str = "m/s";
pub const UNIT_DEG: &str = "deg";
pub const UNIT_V: &str = "V";
pub const UNIT_A: &str = "A";
pub const UNIT_PCT: &str = "pct";
pub const UNIT_S: &str = "s";

/// Round to `dp` decimal places.
///
/// Non-finite input yields `None` — an f64 NaN/inf has no JSON encoding, and
/// emitting `0.0` for one would be inventing a reading.
#[inline]
pub fn round_dp(value: f64, dp: i16) -> Option<f64> {
    if !value.is_finite() {
        return None;
    }
    let factor = 10f64.powi(dp as i32);
    let rounded = (value * factor).round() / factor;
    // Normalise -0.0 to 0.0. Rust's `Sum` for floats folds from -0.0, so a
    // mode that never occurred sums to -0.0 and serialises as "-0.0" — which
    // is numerically equal to zero but reads as a defect on a screen and in a
    // JSON diff.
    Some(if rounded == 0.0 { 0.0 } else { rounded })
}

// ── Wire types ─────────────────────────────────────────────────────────

/// One full-resolution series, mapping 1:1 onto a `flight_series` row.
#[derive(Debug, Serialize, Deserialize, Clone, PartialEq)]
pub struct SeriesBlock {
    pub source: String,
    pub name: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub unit: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub precision_dp: Option<i16>,
    pub values: Vec<Option<f64>>,
}

impl SeriesBlock {
    fn new(source: &str, name: &str, unit: &str, dp: i16, values: Vec<Option<f64>>) -> Self {
        SeriesBlock {
            source: source.to_string(),
            name: name.to_string(),
            unit: Some(unit.to_string()),
            precision_dp: Some(dp),
            values,
        }
    }
}

/// One deduplicated event (§2.6).
#[derive(Debug, Serialize, Deserialize, Clone, PartialEq)]
pub struct EventRecord {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub t_offset_s: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub last_t_offset_s: Option<f64>,
    pub kind: String,
    pub severity: String,
    pub message: String,
    pub count: u32,
    pub garbled: bool,
}

/// One contiguous-state rollup for the phase histogram.
#[derive(Debug, Serialize, Deserialize, Clone, PartialEq)]
pub struct PhaseEntry {
    pub state: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub seconds: Option<f64>,
    pub frames: u32,
}

/// The `details` payload attached to a `ParsedFlight`.
///
/// Every field is optional: the backend writes NULL for anything absent, and a
/// log whose frames did not decode yields a mostly-NULL row rather than zeros.
#[derive(Debug, Serialize, Deserialize, Clone, Default)]
pub struct FlightDetailsOut {
    pub schema_version: i16,
    pub parser_version: String,
    pub crate_version: String,

    // decode provenance
    pub frame_count: Option<i64>,
    pub frame_hz_est: Option<f64>,
    pub first_frame_at: Option<String>,
    pub last_frame_at: Option<String>,

    // altitude
    pub max_altitude_msl_m: Option<f64>,
    pub min_altitude_msl_m: Option<f64>,
    pub home_altitude_msl_m: Option<f64>,
    pub max_vps_height_m: Option<f64>,
    pub take_off_altitude_raw: Option<f64>,
    pub take_off_altitude_units: Option<String>,

    // range / rates
    pub max_distance_from_home_m: Option<f64>,
    pub max_climb_rate_ms: Option<f64>,
    pub max_descent_rate_ms: Option<f64>,
    pub header_max_vertical_speed_ms: Option<f64>,

    // phases
    pub takeoff_count: Option<i32>,
    pub landing_count: Option<i32>,
    pub rth_count: Option<i32>,
    pub sport_mode_seconds: Option<f64>,
    pub waypoint_mode_seconds: Option<f64>,
    pub manual_mode_seconds: Option<f64>,

    // camera
    pub photo_count: Option<i64>,
    pub header_capture_num: Option<i64>,
    pub video_seconds: Option<f64>,
    pub header_video_time_s: Option<f64>,

    // RC link
    pub rc_downlink_min: Option<i32>,
    pub rc_downlink_avg: Option<f64>,
    pub rc_downlink_max: Option<i32>,
    pub rc_uplink_min: Option<i32>,
    pub rc_uplink_avg: Option<f64>,
    pub rc_uplink_max: Option<i32>,
    pub rc_zero_downlink_frames: Option<i64>,
    pub rc_disconnect_events: Option<i32>,

    // battery, this flight
    pub battery_current_max_a: Option<f64>,
    pub battery_energy_wh: Option<f64>,
    pub battery_discharge_mah: Option<f64>,
    pub battery_cell_count: Option<i32>,
    pub battery_cell_deviation_max_v: Option<f64>,
    pub battery_temp_min_c: Option<f64>,
    pub battery_temp_max_c: Option<f64>,
    pub battery_full_capacity_mah: Option<f64>,
    pub battery_current_capacity_mah: Option<f64>,

    // config in force
    pub height_limit_m: Option<f64>,
    pub go_home_height_m: Option<f64>,
    pub max_allowed_height_m: Option<f64>,
    pub is_beginner_mode: Option<bool>,

    // identity
    pub app_platform: Option<String>,

    // rollups
    pub event_count: Option<i64>,
    pub warning_event_count: Option<i64>,
    pub anomaly_flag_count: Option<i32>,

    // groups
    pub phases: Vec<PhaseEntry>,
    pub events: Vec<EventRecord>,
    pub config: serde_json::Value,
    pub health: serde_json::Value,
    pub sd_card: serde_json::Value,
    pub serials: serde_json::Value,

    pub series: Vec<SeriesBlock>,
}

// ── §2.6 event cleaning ────────────────────────────────────────────────

/// Minimum length of a cleaned message before it is treated as a real string.
const MIN_MESSAGE_LEN: usize = 8;

/// Trim a garbled prefix from a decoded app message (§2.6 rule 1).
///
/// ~20 % of `app.tip` / `app.warn` strings arrive from the crate's
/// decrypt/append path with a mangled prefix and an intact suffix. The anchor
/// is the first ASCII uppercase letter that begins a run of at least three
/// ASCII letters — i.e. the start of a real word. Everything before it is
/// dropped and `garbled` is set.
///
/// Returns `(cleaned, garbled)`. Leading whitespace alone is not "garbled".
///
/// **Never reconstructs.** If no anchor exists, only printable ASCII is kept
/// and the caller downgrades a too-short remnant to `kind: "unparsed"`. An
/// unknown stays unknown (ADR-0028).
pub fn clean_event_message(raw: &str) -> (String, bool) {
    let chars: Vec<char> = raw.chars().collect();

    let anchor = (0..chars.len()).find(|&i| {
        if !chars[i].is_ascii_uppercase() {
            return false;
        }
        chars[i..]
            .iter()
            .take_while(|c| c.is_ascii_alphabetic())
            .count()
            >= 3
    });

    match anchor {
        Some(k) => {
            // Only material dropped before the anchor counts as garbling;
            // ordinary leading whitespace does not.
            let garbled = chars[..k].iter().any(|c| !c.is_whitespace());
            let cleaned: String = chars[k..].iter().collect::<String>().trim().to_string();
            (cleaned, garbled)
        }
        None => {
            let kept: String = chars
                .iter()
                .filter(|c| c.is_ascii_graphic() || **c == ' ')
                .collect();
            let cleaned = kept.trim().to_string();
            let garbled = cleaned.chars().count() != raw.trim().chars().count();
            (cleaned, garbled)
        }
    }
}

/// The crate injects its own mode-change tips into `app.tip`. We emit mode
/// transitions as structured `kind: "mode"` events from `flyc_state` instead,
/// so the textual duplicate is dropped rather than counted twice.
const CRATE_MODE_TIP_PREFIX: &str = "Flight mode changed to";

// ── Accumulator ────────────────────────────────────────────────────────

/// Which clock the `t_offset_s` series is derived from.
///
/// Recorded in `config.time_base` so a reader can tell a wall-clock offset
/// from DJI's own elapsed-flight-time counter. When neither is usable the
/// series is omitted entirely rather than filled with zeros.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TimeBase {
    /// `frame.custom.date_time` — real wall clock.
    DateTime,
    /// `frame.osd.fly_time` — DJI's elapsed-flight-time counter.
    FlyTime,
    /// Neither advanced across the log.
    None,
}

impl TimeBase {
    fn as_str(self) -> &'static str {
        match self {
            TimeBase::DateTime => "date_time",
            TimeBase::FlyTime => "fly_time",
            TimeBase::None => "none",
        }
    }
}

/// Decide the time base from the decoded frames.
///
/// `FrameCustom::default()` is the Unix epoch, so a log with no `Custom`
/// records yields identical timestamps on every frame. Treating that as a
/// clock would stamp 1970 on every sample, so it is rejected and DJI's own
/// `fly_time` counter is used instead.
pub fn choose_time_base(frames: &[Frame]) -> TimeBase {
    if frames.len() < 2 {
        return TimeBase::None;
    }
    let first = &frames[0];
    let last = &frames[frames.len() - 1];
    if last.custom.date_time > first.custom.date_time {
        return TimeBase::DateTime;
    }
    if last.osd.fly_time > first.osd.fly_time {
        return TimeBase::FlyTime;
    }
    TimeBase::None
}

#[derive(Default)]
struct EventAccum {
    /// Insertion-ordered records, deduped on (kind, severity, cleaned message).
    order: Vec<EventRecord>,
    index: HashMap<(String, String, String), usize>,
}

impl EventAccum {
    fn push(&mut self, kind: &str, severity: &str, raw: &str, t: Option<f64>) {
        let (cleaned, garbled) = clean_event_message(raw);
        if cleaned.is_empty() {
            return;
        }
        // Rule 3: too short to be a message we can stand behind. Keep the
        // remnant, label it unparsed, never guess at what it was.
        let (kind, garbled) = if cleaned.chars().count() < MIN_MESSAGE_LEN {
            ("unparsed", true)
        } else {
            (kind, garbled)
        };

        let key = (kind.to_string(), severity.to_string(), cleaned.clone());
        match self.index.get(&key) {
            Some(&i) => {
                let rec = &mut self.order[i];
                rec.count += 1;
                if t.is_some() {
                    rec.last_t_offset_s = t;
                }
            }
            None => {
                self.index.insert(key, self.order.len());
                self.order.push(EventRecord {
                    t_offset_s: t,
                    last_t_offset_s: t,
                    kind: kind.to_string(),
                    severity: severity.to_string(),
                    message: cleaned,
                    count: 1,
                    garbled,
                });
            }
        }
    }
}

/// Streaming Tier 0 accumulator. Feed every frame in order, then `finish`.
pub struct DetailsAccum {
    time_base: TimeBase,
    t0_date: Option<DateTime<Utc>>,
    t0_fly: Option<f64>,
    first_frame_at: Option<DateTime<Utc>>,
    last_frame_at: Option<DateTime<Utc>>,

    frames_seen: u32,
    prev_t: Option<f64>,
    cur_t: Option<f64>,

    // series (index-aligned to t_offset)
    t_offset: Vec<Option<f64>>,
    altitude_msl: Vec<Option<f64>>,
    vps_height: Vec<Option<f64>>,
    distance_from_home: Vec<Option<f64>>,
    z_speed: Vec<Option<f64>>,
    rc_downlink: Vec<Option<f64>>,
    rc_uplink: Vec<Option<f64>>,
    gimbal_pitch: Vec<Option<f64>>,
    gimbal_roll: Vec<Option<f64>>,
    gimbal_yaw: Vec<Option<f64>>,
    aircraft_pitch: Vec<Option<f64>>,
    aircraft_roll: Vec<Option<f64>>,
    aircraft_yaw: Vec<Option<f64>>,
    battery_current: Vec<Option<f64>>,
    cell_deviation: Vec<Option<f64>>,

    // scalars
    max_alt_msl: Option<f64>,
    min_alt_msl: Option<f64>,
    home_alt_msl: Option<f64>,
    max_vps: Option<f64>,
    max_distance_home: Option<f64>,
    max_climb: Option<f64>,
    max_descent: Option<f64>,

    // phases
    phase_frames: HashMap<String, u32>,
    phase_seconds: HashMap<String, f64>,
    phase_order: Vec<String>,
    prev_state: Option<String>,
    prev_on_ground: Option<bool>,
    takeoff_count: i32,
    landing_count: i32,
    rth_count: i32,
    prev_in_rth: bool,

    // camera
    prev_is_photo: bool,
    prev_is_video: bool,
    photo_count: i64,
    video_seconds: f64,
    sd_min_state: Option<String>,
    sd_ever_inserted: bool,

    // RC
    rc_down_min: Option<u8>,
    rc_down_max: Option<u8>,
    rc_down_sum: f64,
    rc_down_n: u64,
    rc_up_min: Option<u8>,
    rc_up_max: Option<u8>,
    rc_up_sum: f64,
    rc_up_n: u64,
    rc_zero_downlink_frames: i64,
    rc_disconnect_events: i32,
    prev_downlink_zero: bool,

    // battery
    current_max: Option<f64>,
    energy_ws: f64,
    charge_as: f64,
    cell_count: Option<u8>,
    cell_dev_max: Option<f64>,
    temp_min: Option<f64>,
    temp_max: Option<f64>,
    full_capacity_mah: Option<f64>,
    current_capacity_mah: Option<f64>,
    prev_voltage: Option<f64>,
    prev_current: Option<f64>,

    // config, last value seen in flight
    height_limit_m: Option<f64>,
    go_home_height_m: Option<f64>,
    max_allowed_height_m: Option<f64>,
    is_beginner_mode: Option<bool>,
    go_home_mode: Option<String>,

    // health flag frame-counts
    health: HashMap<&'static str, u64>,

    events: EventAccum,
}

/// Frame flags counted into the `health` group. Each is a per-frame boolean on
/// `FrameOSD`/`FrameGimbal`; the stored value is the number of frames the flag
/// was asserted, not a yes/no, so "vibrating for 4 frames" is distinguishable
/// from "vibrating for the whole flight".
const HEALTH_FLAGS: &[&str] = &[
    "is_vibrating",
    "is_compass_error",
    "is_motor_blocked",
    "is_barometer_dead_in_air",
    "is_acceletor_over_range",
    "is_not_enough_force",
    "is_out_of_limit",
    "is_propeller_catapult",
    "wave_error",
    "gimbal_is_stuck",
    "voltage_warning",
];

impl DetailsAccum {
    pub fn new(time_base: TimeBase) -> Self {
        let mut accum = DetailsAccum {
            time_base,
            t0_date: None,
            t0_fly: None,
            first_frame_at: None,
            last_frame_at: None,
            frames_seen: 0,
            prev_t: None,
            cur_t: None,
            t_offset: Vec::new(),
            altitude_msl: Vec::new(),
            vps_height: Vec::new(),
            distance_from_home: Vec::new(),
            z_speed: Vec::new(),
            rc_downlink: Vec::new(),
            rc_uplink: Vec::new(),
            gimbal_pitch: Vec::new(),
            gimbal_roll: Vec::new(),
            gimbal_yaw: Vec::new(),
            aircraft_pitch: Vec::new(),
            aircraft_roll: Vec::new(),
            aircraft_yaw: Vec::new(),
            battery_current: Vec::new(),
            cell_deviation: Vec::new(),
            max_alt_msl: None,
            min_alt_msl: None,
            home_alt_msl: None,
            max_vps: None,
            max_distance_home: None,
            max_climb: None,
            max_descent: None,
            phase_frames: HashMap::new(),
            phase_seconds: HashMap::new(),
            phase_order: Vec::new(),
            prev_state: None,
            prev_on_ground: None,
            takeoff_count: 0,
            landing_count: 0,
            rth_count: 0,
            prev_in_rth: false,
            prev_is_photo: false,
            prev_is_video: false,
            photo_count: 0,
            video_seconds: 0.0,
            sd_min_state: None,
            sd_ever_inserted: false,
            rc_down_min: None,
            rc_down_max: None,
            rc_down_sum: 0.0,
            rc_down_n: 0,
            rc_up_min: None,
            rc_up_max: None,
            rc_up_sum: 0.0,
            rc_up_n: 0,
            rc_zero_downlink_frames: 0,
            rc_disconnect_events: 0,
            prev_downlink_zero: false,
            current_max: None,
            energy_ws: 0.0,
            charge_as: 0.0,
            cell_count: None,
            cell_dev_max: None,
            temp_min: None,
            temp_max: None,
            full_capacity_mah: None,
            current_capacity_mah: None,
            prev_voltage: None,
            prev_current: None,
            height_limit_m: None,
            go_home_height_m: None,
            max_allowed_height_m: None,
            is_beginner_mode: None,
            go_home_mode: None,
            health: HashMap::new(),
            events: EventAccum::default(),
        };
        for flag in HEALTH_FLAGS {
            accum.health.insert(flag, 0);
        }
        accum
    }

    /// Elapsed seconds for this frame under the chosen time base.
    fn frame_time(&mut self, frame: &Frame) -> Option<f64> {
        match self.time_base {
            TimeBase::DateTime => {
                let dt = frame.custom.date_time;
                if self.t0_date.is_none() {
                    self.t0_date = Some(dt);
                }
                let t0 = self.t0_date.unwrap();
                Some((dt - t0).num_milliseconds() as f64 / 1000.0)
            }
            TimeBase::FlyTime => {
                let fly = frame.osd.fly_time as f64;
                if self.t0_fly.is_none() {
                    self.t0_fly = Some(fly);
                }
                Some(fly - self.t0_fly.unwrap())
            }
            TimeBase::None => None,
        }
    }

    pub fn push_frame(&mut self, frame: &Frame) {
        self.frames_seen += 1;
        let osd = &frame.osd;

        // ── time base ──
        self.prev_t = self.cur_t;
        self.cur_t = self.frame_time(frame);
        // Interval [prev, cur] is attributed to the state held at `prev`.
        let dt = match (self.prev_t, self.cur_t) {
            (Some(p), Some(c)) if c > p => Some(c - p),
            _ => None,
        };
        self.t_offset.push(self.cur_t.and_then(|t| round_dp(t, DP_TIME_S)));

        if self.time_base == TimeBase::DateTime {
            let dt_stamp = frame.custom.date_time;
            if self.first_frame_at.is_none() {
                self.first_frame_at = Some(dt_stamp);
            }
            self.last_frame_at = Some(dt_stamp);
        }

        // ── altitude / VPS ──
        let alt_msl = osd.altitude as f64;
        self.altitude_msl.push(round_dp(alt_msl, DP_DISTANCE_M));
        self.max_alt_msl = Some(self.max_alt_msl.map_or(alt_msl, |m: f64| m.max(alt_msl)));
        self.min_alt_msl = Some(self.min_alt_msl.map_or(alt_msl, |m: f64| m.min(alt_msl)));

        let vps = osd.vps_height as f64;
        self.vps_height.push(round_dp(vps, DP_DISTANCE_M));
        self.max_vps = Some(self.max_vps.map_or(vps, |m: f64| m.max(vps)));

        let home = &frame.home;
        if home.altitude != 0.0 {
            self.home_alt_msl = Some(home.altitude as f64);
        }

        // ── distance from home ──
        // Only when BOTH the home point and the current fix are real. A
        // missing fix is a null sample, never a 0.0 that would draw the
        // aircraft at the home point.
        let dist = if home.latitude.abs() > 0.001
            && home.longitude.abs() > 0.001
            && osd.latitude.abs() > 0.001
            && osd.longitude.abs() > 0.001
        {
            Some(crate::dji::haversine(
                home.latitude,
                home.longitude,
                osd.latitude,
                osd.longitude,
            ))
        } else {
            None
        };
        self.distance_from_home
            .push(dist.and_then(|d| round_dp(d, DP_DISTANCE_M)));
        if let Some(d) = dist {
            self.max_distance_home = Some(self.max_distance_home.map_or(d, |m: f64| m.max(d)));
        }

        // ── vertical rate / attitude ──
        let z = osd.z_speed as f64;
        self.z_speed.push(round_dp(z, DP_SPEED_MS));
        if z > 0.0 {
            self.max_climb = Some(self.max_climb.map_or(z, |m: f64| m.max(z)));
        } else if z < 0.0 {
            // Stored as a positive magnitude — "max descent rate 4.2 m/s"
            // reads correctly, "-4.2" invites a sign mistake downstream.
            let mag = -z;
            self.max_descent = Some(self.max_descent.map_or(mag, |m: f64| m.max(mag)));
        }
        self.aircraft_pitch
            .push(round_dp(osd.pitch as f64, DP_ANGLE_DEG));
        self.aircraft_roll
            .push(round_dp(osd.roll as f64, DP_ANGLE_DEG));
        self.aircraft_yaw
            .push(round_dp(osd.yaw as f64, DP_ANGLE_DEG));

        // ── gimbal ──
        let gimbal = &frame.gimbal;
        self.gimbal_pitch
            .push(round_dp(gimbal.pitch as f64, DP_ANGLE_DEG));
        self.gimbal_roll
            .push(round_dp(gimbal.roll as f64, DP_ANGLE_DEG));
        self.gimbal_yaw
            .push(round_dp(gimbal.yaw as f64, DP_ANGLE_DEG));

        // ── RC link ──
        match frame.rc.downlink_signal {
            Some(v) => {
                self.rc_downlink.push(Some(v as f64));
                self.rc_down_min = Some(self.rc_down_min.map_or(v, |m: u8| m.min(v)));
                self.rc_down_max = Some(self.rc_down_max.map_or(v, |m: u8| m.max(v)));
                self.rc_down_sum += v as f64;
                self.rc_down_n += 1;
                if v == 0 {
                    self.rc_zero_downlink_frames += 1;
                    if !self.prev_downlink_zero {
                        // Rising edge only: one disconnect, not one per frame
                        // the link stayed down.
                        self.rc_disconnect_events += 1;
                    }
                    self.prev_downlink_zero = true;
                } else {
                    self.prev_downlink_zero = false;
                }
            }
            None => self.rc_downlink.push(None),
        }
        match frame.rc.uplink_signal {
            Some(v) => {
                self.rc_uplink.push(Some(v as f64));
                self.rc_up_min = Some(self.rc_up_min.map_or(v, |m: u8| m.min(v)));
                self.rc_up_max = Some(self.rc_up_max.map_or(v, |m: u8| m.max(v)));
                self.rc_up_sum += v as f64;
                self.rc_up_n += 1;
            }
            None => self.rc_uplink.push(None),
        }

        // ── battery ──
        let battery = &frame.battery;
        let voltage = battery.voltage as f64;
        let current = battery.current as f64;
        self.battery_current.push(round_dp(current, DP_CURRENT_A));
        self.cell_deviation
            .push(round_dp(battery.cell_voltage_deviation as f64, DP_VOLTAGE_V));

        if current != 0.0 {
            self.current_max = Some(self.current_max.map_or(current, |m: f64| m.max(current)));
        }
        // Trapezoidal integration over the real inter-frame interval — never a
        // frame count times an assumed cadence (the ADR-0027 mistake).
        if let (Some(step), Some(pv), Some(pc)) = (dt, self.prev_voltage, self.prev_current) {
            self.energy_ws += 0.5 * (pv * pc + voltage * current) * step;
            self.charge_as += 0.5 * (pc + current) * step;
        }
        self.prev_voltage = Some(voltage);
        self.prev_current = Some(current);

        if battery.cell_num > 0 {
            self.cell_count = Some(battery.cell_num);
        }
        let dev = battery.cell_voltage_deviation as f64;
        if dev > 0.0 {
            self.cell_dev_max = Some(self.cell_dev_max.map_or(dev, |m: f64| m.max(dev)));
        }
        let temp = battery.temperature as f64;
        if temp != 0.0 {
            self.temp_min = Some(self.temp_min.map_or(temp, |m: f64| m.min(temp)));
            self.temp_max = Some(self.temp_max.map_or(temp, |m: f64| m.max(temp)));
        }
        if battery.full_capacity > 0 {
            self.full_capacity_mah = Some(battery.full_capacity as f64);
        }
        if battery.current_capacity > 0 {
            self.current_capacity_mah = Some(battery.current_capacity as f64);
        }

        // ── phases ──
        let state = match osd.flyc_state {
            Some(s) => format!("{:?}", s),
            None => "Unknown".to_string(),
        };
        if !self.phase_frames.contains_key(&state) {
            self.phase_order.push(state.clone());
        }
        *self.phase_frames.entry(state.clone()).or_insert(0) += 1;
        if let (Some(step), Some(prev)) = (dt, self.prev_state.clone()) {
            *self.phase_seconds.entry(prev).or_insert(0.0) += step;
        }
        if self.prev_state.as_deref() != Some(state.as_str()) {
            if let Some(prev) = &self.prev_state {
                self.events.push(
                    "mode",
                    "info",
                    &format!("Flight mode {} to {}", prev, state),
                    self.cur_t,
                );
            }
            self.prev_state = Some(state.clone());
        }

        // Takeoff / landing from the ground/sky bit — a direct physical
        // signal, not an inference from mode names.
        let on_ground = osd.is_on_ground;
        if let Some(prev) = self.prev_on_ground {
            if prev && !on_ground {
                self.takeoff_count += 1;
            } else if !prev && on_ground {
                self.landing_count += 1;
            }
        }
        self.prev_on_ground = Some(on_ground);

        let in_rth = state.contains("GoHome");
        if in_rth && !self.prev_in_rth {
            self.rth_count += 1;
        }
        self.prev_in_rth = in_rth;

        // ── camera ──
        let camera = &frame.camera;
        // RISING EDGES ONLY. `is_photo` is asserted for as long as the shutter
        // record is in scope, so counting frames would multiply one photo by
        // the frame rate.
        if camera.is_photo && !self.prev_is_photo {
            self.photo_count += 1;
        }
        self.prev_is_photo = camera.is_photo;
        // `dt` is the interval [prev_frame, this_frame], so it belongs to the
        // recording state that HELD during it — the previous frame's flag, not
        // this one's. Same convention as the phase histogram above; using the
        // current frame's flag instead shifts every interval by one sample and
        // silently drops the last stretch of a recording.
        if self.prev_is_video {
            if let Some(step) = dt {
                self.video_seconds += step;
            }
        }
        self.prev_is_video = camera.is_video;
        if camera.sd_card_is_inserted {
            self.sd_ever_inserted = true;
        }
        if let Some(s) = &camera.sd_card_state {
            self.sd_min_state = Some(format!("{:?}", s));
        }

        // ── config in force ──
        if home.height_limit != 0.0 {
            self.height_limit_m = Some(home.height_limit as f64);
        }
        if home.go_home_height != 0 {
            self.go_home_height_m = Some(home.go_home_height as f64);
        }
        if home.max_allowed_height != 0.0 {
            self.max_allowed_height_m = Some(home.max_allowed_height as f64);
        }
        self.is_beginner_mode = Some(home.is_beginner_mode);
        if let Some(m) = &home.go_home_mode {
            self.go_home_mode = Some(format!("{:?}", m));
        }

        // ── health flags ──
        let flags: [(&str, bool); 11] = [
            ("is_vibrating", osd.is_vibrating),
            ("is_compass_error", osd.is_compass_error),
            ("is_motor_blocked", osd.is_motor_blocked),
            ("is_barometer_dead_in_air", osd.is_barometer_dead_in_air),
            ("is_acceletor_over_range", osd.is_acceletor_over_range),
            ("is_not_enough_force", osd.is_not_enough_force),
            ("is_out_of_limit", osd.is_out_of_limit),
            ("is_propeller_catapult", osd.is_propeller_catapult),
            ("wave_error", osd.wave_error),
            ("gimbal_is_stuck", gimbal.is_stuck),
            ("voltage_warning", osd.voltage_warning > 0),
        ];
        for (name, asserted) in flags {
            if asserted {
                *self.health.get_mut(name).unwrap() += 1;
            }
        }

        // ── events ──
        // The crate joins multiple messages per frame with "; ".
        for part in frame.app.tip.split("; ") {
            let part = part.trim();
            if part.is_empty() || part.starts_with(CRATE_MODE_TIP_PREFIX) {
                continue;
            }
            self.events.push("tip", "info", part, self.cur_t);
        }
        for part in frame.app.warn.split("; ") {
            let part = part.trim();
            if part.is_empty() {
                continue;
            }
            self.events.push("warn", "warning", part, self.cur_t);
        }
    }

    fn series_blocks(self_t_offset: Vec<Option<f64>>, blocks: Vec<SeriesBlock>) -> Vec<SeriesBlock> {
        let mut out = Vec::with_capacity(blocks.len() + 1);
        if self_t_offset.iter().any(|v| v.is_some()) {
            out.push(SeriesBlock::new(
                "frame",
                "t_offset_s",
                UNIT_S,
                DP_TIME_S,
                self_t_offset,
            ));
        }
        out.extend(blocks);
        out
    }

    /// Materialise the payload. `header` supplies the values that come from the
    /// log header rather than the frames.
    pub fn finish(self, header: HeaderExtras) -> FlightDetailsOut {
        let frames = self.frames_seen as i64;

        let span_s = match (self.first_frame_at, self.last_frame_at) {
            (Some(a), Some(b)) if b > a => Some((b - a).num_milliseconds() as f64 / 1000.0),
            _ => None,
        };
        // Hz from (n-1) intervals, not n samples — the off-by-one that makes a
        // 15 Hz airframe read as 15.001 Hz.
        let frame_hz_est = match span_s {
            Some(s) if s > 0.0 && frames > 1 => round_dp((frames - 1) as f64 / s, 2),
            _ => None,
        };

        let phases: Vec<PhaseEntry> = self
            .phase_order
            .iter()
            .map(|state| PhaseEntry {
                state: state.clone(),
                seconds: self
                    .phase_seconds
                    .get(state)
                    .and_then(|s| round_dp(*s, DP_TIME_S)),
                frames: *self.phase_frames.get(state).unwrap_or(&0),
            })
            .collect();

        let mode_seconds = |needle: &str| -> Option<f64> {
            let total: f64 = self
                .phase_seconds
                .iter()
                .filter(|(state, _)| state.contains(needle))
                .map(|(_, s)| *s)
                .sum();
            if self.phase_seconds.is_empty() {
                None
            } else {
                round_dp(total, DP_TIME_S)
            }
        };
        // "GPSSport" is the Sport-mode variant; "Manual" must match exactly so
        // the Atti* modes are not swept in with it.
        let sport_mode_seconds = mode_seconds("Sport");
        let waypoint_mode_seconds = mode_seconds("Waypoint");
        let manual_mode_seconds = if self.phase_seconds.is_empty() {
            None
        } else {
            round_dp(*self.phase_seconds.get("Manual").unwrap_or(&0.0), DP_TIME_S)
        };

        let events = self.events.order;
        let event_count = events.iter().map(|e| e.count as i64).sum::<i64>();
        let warning_event_count = events
            .iter()
            .filter(|e| e.severity != "info")
            .map(|e| e.count as i64)
            .sum::<i64>();
        let anomaly_flag_count = self.health.values().filter(|n| **n > 0).count() as i32;

        // Every flag is present with its frame-count, including the zeros.
        // A key that vanishes when a flag never fired is indistinguishable
        // from a build that stopped tracking it, which is the reading a
        // future maintainer must not have to guess at.
        let health = serde_json::Value::Object(
            self.health
                .iter()
                .map(|(k, v)| (k.to_string(), serde_json::json!(*v)))
                .collect::<serde_json::Map<String, serde_json::Value>>(),
        );

        let blocks = vec![
            SeriesBlock::new("frame", "altitude_msl_m", UNIT_M, DP_DISTANCE_M, self.altitude_msl),
            SeriesBlock::new("frame", "vps_height_m", UNIT_M, DP_DISTANCE_M, self.vps_height),
            SeriesBlock::new("frame", "distance_from_home_m", UNIT_M, DP_DISTANCE_M, self.distance_from_home),
            SeriesBlock::new("frame", "z_speed_ms", UNIT_MS, DP_SPEED_MS, self.z_speed),
            SeriesBlock::new("frame", "rc_downlink", UNIT_PCT, DP_PERCENT, self.rc_downlink),
            SeriesBlock::new("frame", "rc_uplink", UNIT_PCT, DP_PERCENT, self.rc_uplink),
            SeriesBlock::new("frame", "gimbal_pitch_deg", UNIT_DEG, DP_ANGLE_DEG, self.gimbal_pitch),
            SeriesBlock::new("frame", "gimbal_roll_deg", UNIT_DEG, DP_ANGLE_DEG, self.gimbal_roll),
            SeriesBlock::new("frame", "gimbal_yaw_deg", UNIT_DEG, DP_ANGLE_DEG, self.gimbal_yaw),
            SeriesBlock::new("frame", "aircraft_pitch_deg", UNIT_DEG, DP_ANGLE_DEG, self.aircraft_pitch),
            SeriesBlock::new("frame", "aircraft_roll_deg", UNIT_DEG, DP_ANGLE_DEG, self.aircraft_roll),
            SeriesBlock::new("frame", "aircraft_yaw_deg", UNIT_DEG, DP_ANGLE_DEG, self.aircraft_yaw),
            SeriesBlock::new("frame", "battery_current_a", UNIT_A, DP_CURRENT_A, self.battery_current),
            SeriesBlock::new("frame", "cell_voltage_deviation_v", UNIT_V, DP_VOLTAGE_V, self.cell_deviation),
        ];
        let series = if frames == 0 {
            Vec::new()
        } else {
            Self::series_blocks(self.t_offset, blocks)
        };

        FlightDetailsOut {
            schema_version: 1,
            parser_version: env!("CARGO_PKG_VERSION").to_string(),
            crate_version: header.crate_version,

            frame_count: Some(frames),
            frame_hz_est,
            first_frame_at: self.first_frame_at.map(|d| d.to_rfc3339()),
            last_frame_at: self.last_frame_at.map(|d| d.to_rfc3339()),

            max_altitude_msl_m: self.max_alt_msl.and_then(|v| round_dp(v, DP_DISTANCE_M)),
            min_altitude_msl_m: self.min_alt_msl.and_then(|v| round_dp(v, DP_DISTANCE_M)),
            home_altitude_msl_m: self.home_alt_msl.and_then(|v| round_dp(v, DP_DISTANCE_M)),
            max_vps_height_m: self.max_vps.and_then(|v| round_dp(v, DP_DISTANCE_M)),
            take_off_altitude_raw: header.take_off_altitude_raw,
            // §9 C-1: appears to be 10x metres on one airframe, one sample.
            // Stored RAW and marked unconfirmed — a guessed x0.1 that is wrong
            // puts a fabricated altitude on a screen.
            take_off_altitude_units: header
                .take_off_altitude_raw
                .map(|_| "unconfirmed".to_string()),

            max_distance_from_home_m: self
                .max_distance_home
                .and_then(|v| round_dp(v, DP_DISTANCE_M)),
            max_climb_rate_ms: self.max_climb.and_then(|v| round_dp(v, DP_SPEED_MS)),
            max_descent_rate_ms: self.max_descent.and_then(|v| round_dp(v, DP_SPEED_MS)),
            header_max_vertical_speed_ms: header.max_vertical_speed_ms,

            takeoff_count: Some(self.takeoff_count),
            landing_count: Some(self.landing_count),
            rth_count: Some(self.rth_count),
            sport_mode_seconds,
            waypoint_mode_seconds,
            manual_mode_seconds,

            photo_count: Some(self.photo_count),
            header_capture_num: header.capture_num,
            video_seconds: round_dp(self.video_seconds, DP_TIME_S),
            header_video_time_s: header.video_time_s,

            rc_downlink_min: self.rc_down_min.map(|v| v as i32),
            rc_downlink_avg: if self.rc_down_n > 0 {
                round_dp(self.rc_down_sum / self.rc_down_n as f64, 2)
            } else {
                None
            },
            rc_downlink_max: self.rc_down_max.map(|v| v as i32),
            rc_uplink_min: self.rc_up_min.map(|v| v as i32),
            rc_uplink_avg: if self.rc_up_n > 0 {
                round_dp(self.rc_up_sum / self.rc_up_n as f64, 2)
            } else {
                None
            },
            rc_uplink_max: self.rc_up_max.map(|v| v as i32),
            rc_zero_downlink_frames: Some(self.rc_zero_downlink_frames),
            rc_disconnect_events: Some(self.rc_disconnect_events),

            battery_current_max_a: self.current_max.and_then(|v| round_dp(v, DP_CURRENT_A)),
            // Joules → watt-hours, amp-seconds → milliamp-hours.
            battery_energy_wh: if self.energy_ws != 0.0 {
                round_dp(self.energy_ws / 3600.0, 3)
            } else {
                None
            },
            battery_discharge_mah: if self.charge_as != 0.0 {
                round_dp(self.charge_as * 1000.0 / 3600.0, 1)
            } else {
                None
            },
            battery_cell_count: self.cell_count.map(|v| v as i32),
            battery_cell_deviation_max_v: self.cell_dev_max.and_then(|v| round_dp(v, DP_VOLTAGE_V)),
            battery_temp_min_c: self.temp_min.and_then(|v| round_dp(v, 1)),
            battery_temp_max_c: self.temp_max.and_then(|v| round_dp(v, 1)),
            battery_full_capacity_mah: self.full_capacity_mah,
            battery_current_capacity_mah: self.current_capacity_mah,

            height_limit_m: self.height_limit_m.and_then(|v| round_dp(v, DP_DISTANCE_M)),
            go_home_height_m: self.go_home_height_m.and_then(|v| round_dp(v, DP_DISTANCE_M)),
            max_allowed_height_m: self
                .max_allowed_height_m
                .and_then(|v| round_dp(v, DP_DISTANCE_M)),
            is_beginner_mode: self.is_beginner_mode,

            app_platform: header.app_platform,

            event_count: Some(event_count),
            warning_event_count: Some(warning_event_count),
            anomaly_flag_count: Some(anomaly_flag_count),

            phases,
            events,
            // Recorded height limits are DATA. Nothing here compares them to
            // any regulatory ceiling and nothing downstream may either
            // (ADR-0029 / ADR-0031).
            config: serde_json::json!({
                "time_base": self.time_base.as_str(),
                "go_home_mode": self.go_home_mode,
                "is_beginner_mode": self.is_beginner_mode,
            }),
            health,
            sd_card: serde_json::json!({
                "card_inserted_seen": self.sd_ever_inserted,
                "last_state": self.sd_min_state,
            }),
            serials: header.serials,
            series,
        }
    }
}

/// Values that come from the log header rather than the frame stream.
#[derive(Debug, Default, Clone)]
pub struct HeaderExtras {
    pub crate_version: String,
    pub max_vertical_speed_ms: Option<f64>,
    pub capture_num: Option<i64>,
    pub video_time_s: Option<f64>,
    pub take_off_altitude_raw: Option<f64>,
    pub app_platform: Option<String>,
    pub serials: serde_json::Value,
}

#[cfg(test)]
mod tests {
    use super::*;
    use dji_log_parser::record::osd::FlightMode;

    fn accum() -> DetailsAccum {
        DetailsAccum::new(TimeBase::FlyTime)
    }

    /// A frame at `t` seconds of elapsed flight time, in `mode`.
    fn frame_at(t: f32, mode: FlightMode) -> Frame {
        let mut f = Frame::default();
        f.osd.fly_time = t;
        f.osd.flyc_state = Some(mode);
        f
    }

    // ── §2.5 rounding ──────────────────────────────────────────────────

    #[test]
    fn round_dp_drops_only_mantissa_noise() {
        // The literal case from the plan: no information is lost, the stored
        // text is ~4x smaller.
        assert_eq!(round_dp(193.90000000000001, 1), Some(193.9));
        assert_eq!(round_dp(0.30000000000000004, 2), Some(0.3));
    }

    #[test]
    fn round_dp_uses_the_documented_precision_per_quantity() {
        assert_eq!(round_dp(123.456789, DP_DISTANCE_M), Some(123.5));
        assert_eq!(round_dp(12.345678, DP_SPEED_MS), Some(12.35));
        assert_eq!(round_dp(45.678, DP_ANGLE_DEG), Some(45.7));
        assert_eq!(round_dp(15.234567, DP_VOLTAGE_V), Some(15.235));
        assert_eq!(round_dp(11.987654, DP_CURRENT_A), Some(11.99));
        assert_eq!(round_dp(87.6, DP_PERCENT), Some(88.0));
        assert_eq!(round_dp(1.23456, DP_TIME_S), Some(1.23));
    }

    #[test]
    fn round_dp_never_emits_negative_zero() {
        // Rust's `Sum` for floats folds from -0.0, so a mode that never
        // occurred sums to -0.0 and serialises as "-0.0" — numerically zero,
        // but it reads as a defect on a screen and churns every JSON diff.
        assert_eq!(round_dp(-0.0, 2).map(|v| v.to_string()), Some("0".to_string()));
        assert_eq!(round_dp(-0.001, 2).map(|v| v.to_string()), Some("0".to_string()));
        let empty_sum: f64 = std::iter::empty::<f64>().sum();
        assert!(empty_sum.is_sign_negative(), "premise: std folds from -0.0");
        assert_eq!(round_dp(empty_sum, 2).map(|v| v.to_string()), Some("0".to_string()));
    }

    #[test]
    fn round_dp_rejects_non_finite_rather_than_emitting_zero() {
        // NaN/inf have no JSON encoding. A 0.0 here would be a fabricated
        // reading, so the sample becomes an explicit null instead.
        assert_eq!(round_dp(f64::NAN, 2), None);
        assert_eq!(round_dp(f64::INFINITY, 2), None);
    }

    // ── §2.2 phase histogram over a synthetic frame vector ─────────────

    #[test]
    fn phase_histogram_attributes_seconds_to_the_state_that_held_them() {
        let mut a = accum();
        // 0-3 s hovering, 3-5 s sport, 5-6 s hovering again.
        for (t, mode) in [
            (0.0, FlightMode::Hover),
            (1.0, FlightMode::Hover),
            (2.0, FlightMode::Hover),
            (3.0, FlightMode::GPSSport),
            (4.0, FlightMode::GPSSport),
            (5.0, FlightMode::Hover),
            (6.0, FlightMode::Hover),
        ] {
            a.push_frame(&frame_at(t, mode));
        }
        let out = a.finish(HeaderExtras::default());

        let by_state: HashMap<_, _> = out
            .phases
            .iter()
            .map(|p| (p.state.as_str(), (p.seconds, p.frames)))
            .collect();
        // Hover holds [0,1),[1,2),[2,3) then [5,6),[6,-] → 4 s of intervals.
        assert_eq!(by_state["Hover"], (Some(4.0), 5));
        // Sport holds [3,4) and [4,5) → 2 s.
        assert_eq!(by_state["GPSSport"], (Some(2.0), 2));
        assert_eq!(out.sport_mode_seconds, Some(2.0));
        // Total attributed seconds equals the flight span; nothing is lost or
        // double-counted between states.
        let total: f64 = out.phases.iter().filter_map(|p| p.seconds).sum();
        assert_eq!(total, 6.0);
    }

    #[test]
    fn phase_order_is_first_seen_not_hash_order() {
        let mut a = accum();
        for (t, m) in [
            (0.0, FlightMode::AutoTakeoff),
            (1.0, FlightMode::Hover),
            (2.0, FlightMode::GPSSport),
            (3.0, FlightMode::AutoLanding),
        ] {
            a.push_frame(&frame_at(t, m));
        }
        let out = a.finish(HeaderExtras::default());
        let order: Vec<_> = out.phases.iter().map(|p| p.state.as_str()).collect();
        assert_eq!(order, ["AutoTakeoff", "Hover", "GPSSport", "AutoLanding"]);
    }

    #[test]
    fn manual_seconds_does_not_sweep_in_the_atti_modes() {
        let mut a = accum();
        for (t, m) in [
            (0.0, FlightMode::Manual),
            (1.0, FlightMode::AttiLimited),
            (2.0, FlightMode::AttiHover),
            (3.0, FlightMode::AttiHover),
        ] {
            a.push_frame(&frame_at(t, m));
        }
        let out = a.finish(HeaderExtras::default());
        assert_eq!(out.manual_mode_seconds, Some(1.0));
    }

    #[test]
    fn rth_counts_entries_not_frames() {
        let mut a = accum();
        for (t, m) in [
            (0.0, FlightMode::Hover),
            (1.0, FlightMode::GoHome),
            (2.0, FlightMode::GoHome),
            (3.0, FlightMode::GoHome),
            (4.0, FlightMode::Hover),
            (5.0, FlightMode::GoHome),
        ] {
            a.push_frame(&frame_at(t, m));
        }
        let out = a.finish(HeaderExtras::default());
        assert_eq!(out.rth_count, Some(2));
    }

    #[test]
    fn takeoff_and_landing_come_from_the_ground_bit() {
        let mut a = accum();
        for (t, on_ground) in [
            (0.0, true),
            (1.0, false),
            (2.0, false),
            (3.0, true),
            (4.0, false),
            (5.0, true),
        ] {
            let mut f = frame_at(t, FlightMode::Hover);
            f.osd.is_on_ground = on_ground;
            a.push_frame(&f);
        }
        let out = a.finish(HeaderExtras::default());
        assert_eq!(out.takeoff_count, Some(2));
        assert_eq!(out.landing_count, Some(2));
    }

    // ── §2.2 camera: rising edges only ─────────────────────────────────

    #[test]
    fn photo_counter_counts_rising_edges_only() {
        let mut a = accum();
        // Two shutter events; the flag is asserted for several frames each.
        for (t, is_photo) in [
            (0.0, false),
            (1.0, true),
            (2.0, true),
            (3.0, true),
            (4.0, false),
            (5.0, true),
            (6.0, false),
        ] {
            let mut f = frame_at(t, FlightMode::Hover);
            f.camera.is_photo = is_photo;
            a.push_frame(&f);
        }
        let out = a.finish(HeaderExtras::default());
        assert_eq!(
            out.photo_count,
            Some(2),
            "counting asserted FRAMES instead of edges would report 4"
        );
    }

    #[test]
    fn a_photo_flag_asserted_on_the_first_frame_still_counts() {
        let mut a = accum();
        let mut f = frame_at(0.0, FlightMode::Hover);
        f.camera.is_photo = true;
        a.push_frame(&f);
        a.push_frame(&frame_at(1.0, FlightMode::Hover));
        assert_eq!(a.finish(HeaderExtras::default()).photo_count, Some(1));
    }

    #[test]
    fn video_seconds_sums_real_intervals_not_frame_counts() {
        let mut a = accum();
        for (t, rec) in [(0.0, false), (1.0, true), (3.0, true), (7.0, false)] {
            let mut f = frame_at(t, FlightMode::Hover);
            f.camera.is_video = rec;
            a.push_frame(&f);
        }
        // Recording across [1,3) and [3,7) → 2 + 4 = 6 s.
        assert_eq!(a.finish(HeaderExtras::default()).video_seconds, Some(6.0));
    }

    // ── §2.6 event cleaning, dedupe, garbled flag ──────────────────────

    #[test]
    fn a_clean_message_is_not_flagged_garbled() {
        let (msg, garbled) = clean_event_message("Remote controller disconnected. Adjust antennas");
        assert_eq!(msg, "Remote controller disconnected. Adjust antennas");
        assert!(!garbled);
    }

    #[test]
    fn a_garbled_prefix_is_trimmed_and_flagged() {
        let (msg, garbled) = clean_event_message("\u{1}\u{7f}\u{3}xQ\u{12}Remote controller disconnected");
        assert_eq!(msg, "Remote controller disconnected");
        assert!(garbled, "trimming anything must set the flag");
    }

    #[test]
    fn leading_whitespace_alone_is_not_garbling() {
        let (msg, garbled) = clean_event_message("   Gimbal pitch axis endpoint reached.");
        assert_eq!(msg, "Gimbal pitch axis endpoint reached.");
        assert!(!garbled);
    }

    #[test]
    fn a_short_remnant_is_never_reconstructed() {
        // ADR-0028's posture: an unknown stays unknown.
        let mut a = accum();
        let mut f = frame_at(0.0, FlightMode::Hover);
        f.app.warn = "\u{2}\u{3}Ok".to_string();
        a.push_frame(&f);
        let out = a.finish(HeaderExtras::default());
        assert_eq!(out.events.len(), 1);
        assert_eq!(out.events[0].kind, "unparsed");
        assert!(out.events[0].garbled);
        assert_eq!(out.events[0].message, "Ok");
    }

    #[test]
    fn event_dedupe_collapses_n_identical_strings_into_one_record() {
        // The M4TD census case: 18 separate "Remote controller disconnected"
        // strings. Without dedupe `events` is a spam log, not a report section.
        let mut a = accum();
        for i in 0..18 {
            let mut f = frame_at(i as f32, FlightMode::Hover);
            f.app.warn = "Remote controller disconnected. Adjust antennas".to_string();
            a.push_frame(&f);
        }
        let out = a.finish(HeaderExtras::default());
        let warns: Vec<_> = out.events.iter().filter(|e| e.kind == "warn").collect();
        assert_eq!(warns.len(), 1);
        assert_eq!(warns[0].count, 18);
        assert_eq!(warns[0].t_offset_s, Some(0.0));
        assert_eq!(warns[0].last_t_offset_s, Some(17.0));
        // The rollup counts occurrences, not deduped records.
        assert_eq!(out.event_count, Some(18));
        assert_eq!(out.warning_event_count, Some(18));
    }

    #[test]
    fn dedupe_keys_on_the_cleaned_string_so_garbled_variants_merge() {
        let mut a = accum();
        let mut f1 = frame_at(0.0, FlightMode::Hover);
        f1.app.warn = "Remote controller disconnected".to_string();
        a.push_frame(&f1);
        let mut f2 = frame_at(1.0, FlightMode::Hover);
        f2.app.warn = "\u{4}\u{1}Remote controller disconnected".to_string();
        a.push_frame(&f2);
        let out = a.finish(HeaderExtras::default());
        let warns: Vec<_> = out.events.iter().filter(|e| e.kind == "warn").collect();
        assert_eq!(warns.len(), 1, "the same message must not split on its prefix");
        assert_eq!(warns[0].count, 2);
    }

    #[test]
    fn multiple_messages_in_one_frame_are_split_on_the_crate_separator() {
        let mut a = accum();
        let mut f = frame_at(0.0, FlightMode::Hover);
        f.app.tip = "Gimbal pitch axis endpoint reached.; Gimbal roll axis endpoint reached."
            .to_string();
        a.push_frame(&f);
        let out = a.finish(HeaderExtras::default());
        assert_eq!(out.events.iter().filter(|e| e.kind == "tip").count(), 2);
    }

    #[test]
    fn the_crates_own_mode_change_tip_is_not_double_counted() {
        // The crate appends "Flight mode changed to X." to app.tip itself. We
        // emit structured `mode` events from flyc_state, so the textual copy
        // is dropped — otherwise every transition appears twice.
        let mut a = accum();
        let mut f0 = frame_at(0.0, FlightMode::Hover);
        f0.app.tip = "Flight mode changed to Hover.".to_string();
        a.push_frame(&f0);
        let mut f1 = frame_at(1.0, FlightMode::GPSSport);
        f1.app.tip = "Flight mode changed to GPSSport.".to_string();
        a.push_frame(&f1);
        let out = a.finish(HeaderExtras::default());
        assert_eq!(out.events.iter().filter(|e| e.kind == "tip").count(), 0);
        let modes: Vec<_> = out.events.iter().filter(|e| e.kind == "mode").collect();
        assert_eq!(modes.len(), 1);
        assert_eq!(modes[0].message, "Flight mode Hover to GPSSport");
        assert_eq!(modes[0].severity, "info");
    }

    #[test]
    fn mode_events_do_not_count_as_warnings() {
        let mut a = accum();
        a.push_frame(&frame_at(0.0, FlightMode::Hover));
        a.push_frame(&frame_at(1.0, FlightMode::GPSSport));
        let out = a.finish(HeaderExtras::default());
        assert_eq!(out.event_count, Some(1));
        assert_eq!(out.warning_event_count, Some(0));
    }

    // ── ADR-0032 unit assertions ───────────────────────────────────────

    #[test]
    fn battery_voltage_and_current_pass_through_in_volts_and_amps() {
        // dji-log-parser already scales both (`/1000`, and `.abs()/1000` for
        // current). A second divide here is the exact ADR-0032 defect that
        // turned a 15.2 V pack into 0.0152 V.
        let mut a = accum();
        for t in 0..2 {
            let mut f = frame_at(t as f32, FlightMode::Hover);
            f.battery.voltage = 15.2;
            f.battery.current = 11.5;
            a.push_frame(&f);
        }
        let out = a.finish(HeaderExtras::default());
        assert_eq!(out.battery_current_max_a, Some(11.5));
        let current = out
            .series
            .iter()
            .find(|s| s.name == "battery_current_a")
            .unwrap();
        assert_eq!(current.unit.as_deref(), Some("A"));
        assert_eq!(current.values[0], Some(11.5));
        assert!(out.battery_current_max_a.unwrap() > 1.0);
    }

    #[test]
    fn energy_and_charge_integrate_over_real_time_not_frame_counts() {
        // 15 V at 10 A for 10 s = 1500 J = 0.41667 Wh, and 100 A·s = 27.78 mAh.
        // A frames-times-assumed-cadence integration (the ADR-0027 mistake in a
        // new place) would give a different answer for the same flight logged
        // at a different rate — so the interval is taken from the clock.
        let mut a = accum();
        for t in [0.0f32, 10.0] {
            let mut f = frame_at(t, FlightMode::Hover);
            f.battery.voltage = 15.0;
            f.battery.current = 10.0;
            a.push_frame(&f);
        }
        let out = a.finish(HeaderExtras::default());
        assert_eq!(out.battery_energy_wh, Some(0.417));
        assert_eq!(out.battery_discharge_mah, Some(27.8));
    }

    #[test]
    fn energy_is_rate_invariant() {
        // The same physical flight logged at 1 Hz and at 5 Hz must integrate
        // to the same energy. This is the property a frame-count integration
        // would violate.
        let integrate = |step: f32, n: u32| {
            let mut a = accum();
            for i in 0..=n {
                let mut f = frame_at(i as f32 * step, FlightMode::Hover);
                f.battery.voltage = 15.0;
                f.battery.current = 10.0;
                a.push_frame(&f);
            }
            a.finish(HeaderExtras::default()).battery_energy_wh
        };
        assert_eq!(integrate(1.0, 10), integrate(0.2, 50));
    }

    #[test]
    fn distances_and_rates_carry_adr0032_units() {
        let mut a = accum();
        for t in [0.0f32, 1.0] {
            let mut f = frame_at(t, FlightMode::Hover);
            f.osd.altitude = 193.90000000000001_f32;
            f.osd.z_speed = 3.456;
            f.home.latitude = 44.0521;
            f.home.longitude = -123.0868;
            f.osd.latitude = 44.0621;
            f.osd.longitude = -123.0868;
            a.push_frame(&f);
        }
        let out = a.finish(HeaderExtras::default());
        assert_eq!(out.max_altitude_msl_m, Some(193.9));
        assert_eq!(out.max_climb_rate_ms, Some(3.46));
        // ~0.01 deg of latitude ≈ 1.11 km.
        let d = out.max_distance_from_home_m.unwrap();
        assert!((d - 1112.0).abs() < 5.0, "distance was {d} m");

        for (name, unit) in [
            ("altitude_msl_m", "m"),
            ("z_speed_ms", "m/s"),
            ("gimbal_pitch_deg", "deg"),
            ("cell_voltage_deviation_v", "V"),
            ("battery_current_a", "A"),
            ("rc_downlink", "pct"),
            ("t_offset_s", "s"),
        ] {
            let s = out
                .series
                .iter()
                .find(|s| s.name == name)
                .unwrap_or_else(|| panic!("series {name} missing"));
            assert_eq!(s.unit.as_deref(), Some(unit), "wrong unit on {name}");
        }
    }

    #[test]
    fn descent_rate_is_a_positive_magnitude() {
        let mut a = accum();
        for t in [0.0f32, 1.0] {
            let mut f = frame_at(t, FlightMode::Hover);
            f.osd.z_speed = -4.2;
            a.push_frame(&f);
        }
        let out = a.finish(HeaderExtras::default());
        assert_eq!(out.max_descent_rate_ms, Some(4.2));
        assert_eq!(out.max_climb_rate_ms, None);
    }

    // ── series shape ───────────────────────────────────────────────────

    #[test]
    fn every_series_is_full_resolution_and_index_aligned() {
        let mut a = accum();
        for i in 0..500 {
            a.push_frame(&frame_at(i as f32 * 0.1, FlightMode::Hover));
        }
        let out = a.finish(HeaderExtras::default());
        assert_eq!(out.frame_count, Some(500));
        assert!(!out.series.is_empty());
        for s in &out.series {
            assert_eq!(
                s.values.len(),
                500,
                "series {} is not full resolution / not aligned",
                s.name
            );
        }
        // t_offset_s leads, so a reader can bind the time base first.
        assert_eq!(out.series[0].name, "t_offset_s");
    }

    #[test]
    fn a_missing_rc_sample_is_null_not_zero() {
        // A null reads as "no report"; a 0 reads as "signal lost", which is a
        // different and alarming claim about the flight.
        let mut a = accum();
        let mut f0 = frame_at(0.0, FlightMode::Hover);
        f0.rc.downlink_signal = None;
        a.push_frame(&f0);
        let mut f1 = frame_at(1.0, FlightMode::Hover);
        f1.rc.downlink_signal = Some(72);
        a.push_frame(&f1);

        let out = a.finish(HeaderExtras::default());
        let s = out.series.iter().find(|s| s.name == "rc_downlink").unwrap();
        assert_eq!(s.values, vec![None, Some(72.0)]);
        assert_eq!(out.rc_downlink_min, Some(72));
        assert_eq!(out.rc_downlink_max, Some(72));
        assert_eq!(out.rc_downlink_avg, Some(72.0));
        assert_eq!(
            out.rc_zero_downlink_frames,
            Some(0),
            "an absent sample is not a zero-signal frame"
        );
    }

    #[test]
    fn distance_from_home_is_null_without_a_fix() {
        let mut a = accum();
        // No home point yet.
        a.push_frame(&frame_at(0.0, FlightMode::Hover));
        let mut f = frame_at(1.0, FlightMode::Hover);
        f.home.latitude = 44.0521;
        f.home.longitude = -123.0868;
        f.osd.latitude = 44.0521;
        f.osd.longitude = -123.0868;
        a.push_frame(&f);
        let out = a.finish(HeaderExtras::default());
        let s = out
            .series
            .iter()
            .find(|s| s.name == "distance_from_home_m")
            .unwrap();
        assert_eq!(s.values[0], None);
        assert_eq!(s.values[1], Some(0.0));
    }

    #[test]
    fn rc_disconnects_count_rising_edges_not_frames() {
        let mut a = accum();
        for (t, v) in [
            (0.0, 70u8),
            (1.0, 0),
            (2.0, 0),
            (3.0, 0),
            (4.0, 65),
            (5.0, 0),
        ] {
            let mut f = frame_at(t, FlightMode::Hover);
            f.rc.downlink_signal = Some(v);
            a.push_frame(&f);
        }
        let out = a.finish(HeaderExtras::default());
        assert_eq!(out.rc_disconnect_events, Some(2));
        assert_eq!(out.rc_zero_downlink_frames, Some(4));
    }

    // ── time base ──────────────────────────────────────────────────────

    #[test]
    fn an_epoch_default_timestamp_is_not_treated_as_a_clock() {
        // FrameCustom::default() is the Unix epoch. Trusting it would stamp
        // 1970 on every sample of every log that has no Custom records.
        let frames: Vec<Frame> = (0..5)
            .map(|i| frame_at(i as f32, FlightMode::Hover))
            .collect();
        assert_eq!(choose_time_base(&frames), TimeBase::FlyTime);

        let flat: Vec<Frame> = (0..5).map(|_| Frame::default()).collect();
        assert_eq!(choose_time_base(&flat), TimeBase::None);

        assert_eq!(choose_time_base(&[]), TimeBase::None);
        assert_eq!(choose_time_base(&frames[..1]), TimeBase::None);
    }

    #[test]
    fn no_time_base_yields_no_timestamps_and_no_hz_estimate() {
        let mut a = DetailsAccum::new(TimeBase::None);
        for _ in 0..5 {
            a.push_frame(&Frame::default());
        }
        let out = a.finish(HeaderExtras::default());
        assert_eq!(out.first_frame_at, None);
        assert_eq!(out.last_frame_at, None);
        assert_eq!(out.frame_hz_est, None);
        // The time base is omitted rather than filled with zeros...
        assert!(out.series.iter().all(|s| s.name != "t_offset_s"));
        // ...and the provenance says so.
        assert_eq!(out.config["time_base"], "none");
    }

    #[test]
    fn frame_hz_is_derived_from_intervals_not_sample_count() {
        use chrono::TimeZone;
        let mut a = DetailsAccum::new(TimeBase::DateTime);
        let base = Utc.timestamp_opt(1_757_000_000, 0).unwrap();
        // 16 frames spanning exactly 1 s → 15 intervals → 15 Hz.
        for i in 0..16 {
            let mut f = Frame::default();
            f.custom.date_time = base + chrono::Duration::milliseconds(i * 1000 / 15);
            a.push_frame(&f);
        }
        let out = a.finish(HeaderExtras::default());
        assert_eq!(out.frame_hz_est, Some(15.0));
        assert!(out.first_frame_at.is_some());
        assert!(out.last_frame_at.is_some());
        assert_eq!(out.config["time_base"], "date_time");
    }

    // ── health / config ────────────────────────────────────────────────

    #[test]
    fn health_counts_frames_and_the_anomaly_rollup_counts_distinct_flags() {
        let mut a = accum();
        for t in 0..4 {
            let mut f = frame_at(t as f32, FlightMode::Hover);
            f.osd.is_vibrating = true;
            f.osd.is_compass_error = t < 2;
            a.push_frame(&f);
        }
        let out = a.finish(HeaderExtras::default());
        assert_eq!(out.health["is_vibrating"], 4);
        assert_eq!(out.health["is_compass_error"], 2);
        assert_eq!(out.health["is_motor_blocked"], 0);
        assert_eq!(out.anomaly_flag_count, Some(2));
    }

    #[test]
    fn recorded_limits_are_stored_as_data_with_no_commentary() {
        // ADR-0029 / ADR-0031: a height limit is a recorded configuration
        // value. Nothing here compares it to anything.
        let mut a = accum();
        let mut f = frame_at(0.0, FlightMode::Hover);
        f.home.height_limit = 120.0;
        f.home.go_home_height = 60;
        f.home.max_allowed_height = 500.0;
        f.home.is_beginner_mode = false;
        a.push_frame(&f);
        let out = a.finish(HeaderExtras::default());
        assert_eq!(out.height_limit_m, Some(120.0));
        assert_eq!(out.go_home_height_m, Some(60.0));
        assert_eq!(out.max_allowed_height_m, Some(500.0));
        assert_eq!(out.is_beginner_mode, Some(false));

        let json = serde_json::to_string(&out).unwrap().to_lowercase();
        for banned in ["part 107", "part-107", "exceed", "violation", "400 ft"] {
            assert!(!json.contains(banned), "details payload editorialized: {banned}");
        }
    }

    #[test]
    fn take_off_altitude_is_stored_raw_and_marked_unconfirmed() {
        // §9 C-1. Guessing x0.1 and being wrong puts a fabricated altitude on
        // a screen; the value is carried but flagged so nothing displays it.
        let mut a = accum();
        a.push_frame(&frame_at(0.0, FlightMode::Hover));
        let out = a.finish(HeaderExtras {
            take_off_altitude_raw: Some(1938.0),
            ..HeaderExtras::default()
        });
        assert_eq!(out.take_off_altitude_raw, Some(1938.0));
        assert_eq!(out.take_off_altitude_units.as_deref(), Some("unconfirmed"));
    }

    #[test]
    fn no_header_altitude_means_no_units_claim() {
        let mut a = accum();
        a.push_frame(&frame_at(0.0, FlightMode::Hover));
        let out = a.finish(HeaderExtras::default());
        assert_eq!(out.take_off_altitude_raw, None);
        assert_eq!(out.take_off_altitude_units, None);
    }

    #[test]
    fn zero_frames_yields_a_mostly_null_payload_not_zeros() {
        let a = accum();
        let out = a.finish(HeaderExtras {
            capture_num: Some(4),
            ..HeaderExtras::default()
        });
        assert_eq!(out.frame_count, Some(0));
        assert!(out.series.is_empty());
        assert_eq!(out.max_altitude_msl_m, None);
        assert_eq!(out.battery_energy_wh, None);
        assert_eq!(out.rc_downlink_avg, None);
        // Header values still come through — they are real.
        assert_eq!(out.header_capture_num, Some(4));
    }

    /// Build the canonical sample payload shared with the backend test suite.
    ///
    /// Exercises every branch that can produce a value: two flight modes, a
    /// takeoff and a landing, two shutter events, a video segment, an RC gap
    /// followed by a dropout, a real home point, battery draw, a health flag
    /// and both an event and a garbled event.
    fn sample_payload() -> FlightDetailsOut {
        use chrono::TimeZone;
        // A real wall clock, so the fixture exercises first/last_frame_at and
        // frame_hz_est — and so the backend's RFC3339 → naive-UTC coercion is
        // covered by the same fixture rather than by a separate hand-written
        // one that could drift from what the parser really emits.
        let base = Utc.timestamp_opt(1_757_030_400, 0).unwrap();
        let mut a = DetailsAccum::new(TimeBase::DateTime);
        for i in 0..20u32 {
            let t = i as f32;
            let mode = if (5..12).contains(&i) {
                FlightMode::GPSSport
            } else {
                FlightMode::Hover
            };
            let mut f = frame_at(t, mode);
            f.custom.date_time = base + chrono::Duration::seconds(i as i64);
            f.osd.is_on_ground = i < 2 || i > 17;
            f.osd.altitude = 190.0 + i as f32;
            f.osd.vps_height = 12.0 + i as f32 * 0.1;
            f.osd.z_speed = if i < 10 { 2.5 } else { -1.75 };
            f.osd.pitch = 3.4;
            f.osd.roll = -1.2;
            f.osd.yaw = 128.6;
            f.osd.is_vibrating = i == 7;
            f.home.latitude = 44.0521;
            f.home.longitude = -123.0868;
            f.home.altitude = 132.5;
            f.home.height_limit = 120.0;
            f.home.go_home_height = 60;
            f.home.max_allowed_height = 500.0;
            f.osd.latitude = 44.0521 + i as f64 * 0.0001;
            f.osd.longitude = -123.0868;
            f.gimbal.pitch = -89.9;
            f.battery.voltage = 15.2 - i as f32 * 0.02;
            f.battery.current = 11.5;
            f.battery.cell_num = 4;
            f.battery.cell_voltage_deviation = 0.012;
            f.battery.temperature = 28.4;
            f.battery.full_capacity = 5000;
            f.battery.current_capacity = 4200 - i * 10;
            f.camera.is_photo = i == 4 || i == 9;
            f.camera.is_video = (6..15).contains(&i);
            f.camera.sd_card_is_inserted = true;
            f.rc.downlink_signal = if i < 2 {
                None
            } else if (13..15).contains(&i) {
                Some(0)
            } else {
                Some(78)
            };
            f.rc.uplink_signal = Some(81);
            if i == 13 {
                f.app.warn = "Remote controller disconnected. Adjust antennas".to_string();
            }
            if i == 16 {
                f.app.tip = "\u{3}\u{1}Gimbal pitch axis endpoint reached.".to_string();
            }
            a.push_frame(&f);
        }
        a.finish(HeaderExtras {
            crate_version: "0.5.7".to_string(),
            max_vertical_speed_ms: Some(6.0),
            capture_num: Some(3),
            video_time_s: Some(9.0),
            take_off_altitude_raw: Some(1938.0),
            app_platform: Some("IOS".to_string()),
            serials: serde_json::json!({
                "rc_sn": "RC-TEST-0001",
                "camera_sn": "CAM-TEST-0001",
                "battery_sn": "BAT-TEST-0001",
                "aircraft_sn_header": "1581F8HGX255P00A",
            }),
        })
    }

    /// Emit the cross-language wire fixture.
    ///
    /// The backend suite asserts against the SAME file
    /// (`backend/tests/fixtures/parser_details_payload.json`), so a field
    /// renamed on this side and not on that one turns a test red instead of
    /// silently writing NULLs into every column of every new flight — which is
    /// exactly the failure a JSON boundary between two languages invites, and
    /// exactly the kind that looks like "it works" in the logs.
    ///
    /// Regenerate with:
    ///   DETAILS_FIXTURE_OUT=../backend/tests/fixtures/parser_details_payload.json \
    ///     cargo test emit_wire_fixture
    #[test]
    fn emit_wire_fixture() {
        let payload = sample_payload();
        let json = serde_json::to_string_pretty(&payload).unwrap();
        if let Ok(path) = std::env::var("DETAILS_FIXTURE_OUT") {
            std::fs::write(&path, format!("{json}\n")).expect("write fixture");
            eprintln!("wrote wire fixture to {path}");
        }
        // Whether or not it was written, the payload must be well-formed and
        // populated — an all-null fixture would make the backend test vacuous.
        let v: serde_json::Value = serde_json::from_str(&json).unwrap();
        assert_eq!(v["frame_count"], 20);
        assert_eq!(v["photo_count"], 2);
        assert!(v["series"].as_array().unwrap().len() >= 15);
        assert!(v["events"].as_array().unwrap().len() >= 2);
    }

    #[test]
    fn the_wire_fixture_matches_the_checked_in_copy() {
        // Guards the regeneration step: if this side's field names or values
        // change, the checked-in fixture the backend tests against is stale
        // and must be regenerated deliberately.
        let path = concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../backend/tests/fixtures/parser_details_payload.json"
        );
        let Ok(on_disk) = std::fs::read_to_string(path) else {
            // The backend tree may not be present in every build context.
            return;
        };
        let expected: serde_json::Value = serde_json::from_str(&on_disk).unwrap();
        let actual: serde_json::Value =
            serde_json::to_value(sample_payload()).unwrap();
        assert_eq!(
            actual, expected,
            "the checked-in wire fixture is stale — regenerate it with \
             DETAILS_FIXTURE_OUT=../backend/tests/fixtures/parser_details_payload.json \
             cargo test emit_wire_fixture"
        );
    }

    #[test]
    fn the_payload_round_trips_through_json() {
        let mut a = accum();
        for i in 0..10 {
            let mut f = frame_at(i as f32, FlightMode::Hover);
            f.rc.downlink_signal = if i % 2 == 0 { Some(70) } else { None };
            a.push_frame(&f);
        }
        let out = a.finish(HeaderExtras::default());
        let json = serde_json::to_string(&out).unwrap();
        // Missing samples must survive as JSON null.
        assert!(json.contains("null"));
        let back: FlightDetailsOut = serde_json::from_str(&json).unwrap();
        assert_eq!(back.frame_count, Some(10));
        assert_eq!(back.series.len(), out.series.len());
    }
}
