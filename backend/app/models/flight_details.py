"""Extended DJI-log data — the ``flight_details`` / ``flight_series`` sidecar pair.

ADR-0043 / plan ``docs/plans/2026-09-04-flight-details-data-ingestion.md``.

**Why a sidecar and not more columns on ``flights``.** ``flights`` is the
subject of three OOM ADRs (0019 list defer, 0020 report geo buffer, 0025
mission detail) and already carries three heavy JSON columns. Widening it
makes every ``select(Flight)`` heavier, protected only by remembering to
``defer()`` — a discipline that has already failed in production. Putting the
extended data in a 1:1 sidecar makes it opt-in *by construction*: the list,
mission, report, export and map paths never join these tables, so they cannot
be hurt by them.

**Why the time series live in their OWN table** (plan §1.5, operator decision
D2 — full resolution at rest). At full resolution one flight's series are
~1.3 MB of raw JSON. A ~300 KB compressed ``series`` column on
``flight_details`` would re-create the ADR-0019 trap one level down: a plain
``select(FlightDetails)`` entity load selects every column and would detoast
the whole blob to read one scalar. One row *per named series* means a chart
request for 1-4 series detoasts ~110 KB instead of ~1.3 MB.

**``json`` for ``FlightSeries.values``, ``JSONB`` for the group columns — the
opposite choice on purpose.** ``jsonb`` re-encodes every number as a
variable-length ``numeric`` with a per-element header; for a 13,870-element
float array that is tens of KB of pure overhead, and binary numerics compress
worse than the rounded ASCII digit runs the parser emits (§2.5). The only
thing ``jsonb`` buys is containment/path operators and GIN indexing —
worthless for an opaque float array that is always fetched whole. The group
columns are the reverse case: small, structured, and plausibly searchable
later, so they keep GIN available. Do not "tidy this up" into one type.

Units follow ADR-0032 throughout: metres, m/s, volts, amps, °C, degrees, mAh,
Wh, seconds, Hz — with the unit in the column name. Every column except
``flight_id`` / ``schema_version`` / ``generated_at`` is nullable: a
Litchi/Airdata flight has no row at all, and a log whose frames failed to
decode produces a mostly-NULL row.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    SmallInteger,
    String,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

#: Bumped when the meaning of a stored column changes, so a backfill can
#: select "everything below version N" without re-parsing what is current.
DETAILS_SCHEMA_VERSION = 1


class FlightDetails(Base):
    """Scalars, rollups and small structured groups for one flight (1:1)."""

    __tablename__ = "flight_details"

    flight_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("flights.id", ondelete="CASCADE"),
        primary_key=True,
    )
    schema_version: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=DETAILS_SCHEMA_VERSION
    )
    parser_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # dji-log-parser crate version used for this decode (operator decision D6).
    crate_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Naive UTC — repo convention, matches every other timestamp column.
    generated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )

    # ── decode provenance ────────────────────────────────────────────
    frame_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    record_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    frame_hz_est: Mapped[float | None] = mapped_column(Float, nullable=True)
    first_frame_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_frame_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # ── altitude (MSL/VPS; AGL stays on flights.max_altitude, untouched) ──
    max_altitude_msl_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    min_altitude_msl_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    home_altitude_msl_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_vps_height_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Units unconfirmed (plan §9 C-1: appears to be 10x metres on one airframe,
    # one sample). Stored RAW and never displayed until a second airframe
    # confirms the scale — guessing and being wrong puts a fabricated altitude
    # on a screen, which ADR-0028's posture forbids.
    take_off_altitude_raw: Mapped[float | None] = mapped_column(Float, nullable=True)
    take_off_altitude_units: Mapped[str | None] = mapped_column(String(16), nullable=True)

    # ── range / rates ────────────────────────────────────────────────
    max_distance_from_home_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_climb_rate_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Positive magnitude, descending.
    max_descent_rate_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    header_max_vertical_speed_ms: Mapped[float | None] = mapped_column(Float, nullable=True)

    # ── phases ───────────────────────────────────────────────────────
    takeoff_count: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    landing_count: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    rth_count: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    sport_mode_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    waypoint_mode_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    manual_mode_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)

    # ── camera ───────────────────────────────────────────────────────
    photo_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    header_capture_num: Mapped[int | None] = mapped_column(Integer, nullable=True)
    video_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    header_video_time_s: Mapped[float | None] = mapped_column(Float, nullable=True)

    # ── RC link (raw crate scale; 0-90 observed, 104 seen once — NOT rescaled) ──
    rc_downlink_min: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    rc_downlink_avg: Mapped[float | None] = mapped_column(Float, nullable=True)
    rc_downlink_max: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    rc_uplink_min: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    rc_uplink_avg: Mapped[float | None] = mapped_column(Float, nullable=True)
    rc_uplink_max: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    rc_zero_downlink_frames: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rc_disconnect_events: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    ofdm_signal_avg_pct: Mapped[float | None] = mapped_column(Float, nullable=True)

    # ── battery, this flight (from frames, full resolution) ──────────
    battery_current_max_a: Mapped[float | None] = mapped_column(Float, nullable=True)
    battery_energy_wh: Mapped[float | None] = mapped_column(Float, nullable=True)
    battery_discharge_mah: Mapped[float | None] = mapped_column(Float, nullable=True)
    battery_cell_count: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    battery_cell_deviation_max_v: Mapped[float | None] = mapped_column(Float, nullable=True)
    battery_temp_min_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    battery_temp_max_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    battery_full_capacity_mah: Mapped[float | None] = mapped_column(Float, nullable=True)
    battery_current_capacity_mah: Mapped[float | None] = mapped_column(Float, nullable=True)

    # ── pack lifetime (Tier 1 SmartBatteryStatic, shim-corrected — P2) ──
    pack_cycle_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pack_designed_capacity_mah: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pack_full_charge_voltage_v: Mapped[float | None] = mapped_column(Float, nullable=True)
    pack_values_shimmed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # false → never displayed, never used as a source for the battery
    # source-of-truth work (operator decision D4).
    pack_values_plausible: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # ── config in force for this flight ──────────────────────────────
    height_limit_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    go_home_height_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_allowed_height_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_beginner_mode: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # ── identity ─────────────────────────────────────────────────────
    aircraft_sn_full: Mapped[str | None] = mapped_column(String(32), nullable=True)
    app_platform: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # ── pilot / VLOS (D1: the raw track lives in flight_series) ───────
    pilot_sample_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pilot_max_distance_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    pilot_avg_distance_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Makes the rows holding PII-adjacent coordinates enumerable for a purge.
    pilot_track_stored: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # ── rollups so the UI can badge without opening a JSONB ───────────
    event_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    warning_event_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    anomaly_flag_count: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)

    # ── repair provenance (D5 / D7) — keeps flights.raw_metadata clean ──
    gps_timestamps_restamped_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # The literal "Unknown(NNN)" a repair pass replaced.
    drone_model_previous: Mapped[str | None] = mapped_column(String(255), nullable=True)
    replaced_from: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # ── JSONB groups (small, structured, potentially searchable) ─────
    phases: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    events: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    config: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    firmware: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    health: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    sd_card: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    serials: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Forward-compat catch-all; normally NULL.
    extra: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    flight = relationship("Flight", back_populates="details")


class FlightSeries(Base):
    """One full-resolution numeric series for one flight.

    The composite primary key is exactly the lookup the read path wants
    (``WHERE flight_id = ? AND source = ? AND name = ANY(?)``), so no
    secondary index ships.

    ``source`` exists because **the series do not share one time base** —
    frames, AppGPS and OFDM are recorded at different cadences (13,870 / 657 /
    5,538 records in the census log). Each ``source`` group carries its own
    ``t_offset_s`` row, and every series within a group is index-aligned to it.
    """

    __tablename__ = "flight_series"

    flight_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("flights.id", ondelete="CASCADE"),
        primary_key=True,
    )
    # 'frame' | 'pilot' | 'ofdm' | 'camera'
    source: Mapped[str] = mapped_column(String(16), primary_key=True)
    # 't_offset_s', 'altitude_msl_m', 'pilot_lat', ...
    name: Mapped[str] = mapped_column(String(48), primary_key=True)
    # 'm', 'm/s', 'deg', 'V', 'A', 'pct', 's', 'wgs84'
    unit: Mapped[str | None] = mapped_column(String(16), nullable=True)
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False)
    # Decimal places emitted (plan §2.5 per-quantity rounding).
    precision_dp: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    # Deliberately generic JSON (Postgres ``json``), NOT JSONB — see module docstring.
    values: Mapped[list] = mapped_column(JSON, nullable=False)

    flight = relationship("Flight", back_populates="series")
