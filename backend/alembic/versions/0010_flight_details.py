"""ADR-0043 — flight_details + flight_series sidecar tables

Revision ID: 0010_flight_details
Revises: 0009_mission_dl_email_sent_at
Create Date: 2026-09-05

Creates the two sidecar tables that hold the extended DJI-log data (plan
``docs/plans/2026-09-04-flight-details-data-ingestion.md`` §1.3 / §1.4).
Nothing is written to them by this revision or by any code path shipped
alongside it — the schema lands first and is INERT until the parser pass
populates it.

Both tables are new, so this is pure additive DDL: no backfill, no data
rewrite, no ALTER of an existing table. Replication-safe (plain DDL over
WAL), survives container recreation and standby promotion, and touches no
port / ``pg_hba`` / connection string.

**Indexes: the primary keys only.** ``flight_details`` is keyed on
``flight_id``; ``flight_series`` on ``(flight_id, source, name)`` — which is
exactly the lookup the read path issues, so no secondary index is needed. The
JSONB group columns keep a later GIN index a one-liner if a query ever wants
one; none does today.

``flight_series.values`` is Postgres ``json`` (not ``jsonb``) on purpose —
see the model module docstring. ``values`` is a reserved word and is quoted.

IDEMPOTENT (ADR-0042, the 2026-08-22 fresh-install incident): ``0001`` builds
fresh databases with ``create_all`` from the LIVE models, which already
include both tables — so on a fresh DB this revision must no-op instead of
raising DuplicateTable. Guarded exactly as ``0009`` guards its column.

Revision id is 19 chars (<= 32, the constraint 0007/0009 call out).
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010_flight_details"
down_revision: Union[str, None] = "0009_mission_dl_email_sent_at"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    existing = set(sa.inspect(conn).get_table_names())

    if "flight_details" not in existing:
        op.create_table(
            "flight_details",
            sa.Column("flight_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("schema_version", sa.SmallInteger(), nullable=False),
            sa.Column("parser_version", sa.String(length=32), nullable=True),
            sa.Column("crate_version", sa.String(length=32), nullable=True),
            sa.Column("generated_at", sa.DateTime(), nullable=False),
            # decode provenance
            sa.Column("frame_count", sa.Integer(), nullable=True),
            sa.Column("record_count", sa.Integer(), nullable=True),
            sa.Column("frame_hz_est", sa.Float(), nullable=True),
            sa.Column("first_frame_at", sa.DateTime(), nullable=True),
            sa.Column("last_frame_at", sa.DateTime(), nullable=True),
            # altitude
            sa.Column("max_altitude_msl_m", sa.Float(), nullable=True),
            sa.Column("min_altitude_msl_m", sa.Float(), nullable=True),
            sa.Column("home_altitude_msl_m", sa.Float(), nullable=True),
            sa.Column("max_vps_height_m", sa.Float(), nullable=True),
            sa.Column("take_off_altitude_raw", sa.Float(), nullable=True),
            sa.Column("take_off_altitude_units", sa.String(length=16), nullable=True),
            # range / rates
            sa.Column("max_distance_from_home_m", sa.Float(), nullable=True),
            sa.Column("max_climb_rate_ms", sa.Float(), nullable=True),
            sa.Column("max_descent_rate_ms", sa.Float(), nullable=True),
            sa.Column("header_max_vertical_speed_ms", sa.Float(), nullable=True),
            # phases
            sa.Column("takeoff_count", sa.SmallInteger(), nullable=True),
            sa.Column("landing_count", sa.SmallInteger(), nullable=True),
            sa.Column("rth_count", sa.SmallInteger(), nullable=True),
            sa.Column("sport_mode_seconds", sa.Float(), nullable=True),
            sa.Column("waypoint_mode_seconds", sa.Float(), nullable=True),
            sa.Column("manual_mode_seconds", sa.Float(), nullable=True),
            # camera
            sa.Column("photo_count", sa.Integer(), nullable=True),
            sa.Column("header_capture_num", sa.Integer(), nullable=True),
            sa.Column("video_seconds", sa.Float(), nullable=True),
            sa.Column("header_video_time_s", sa.Float(), nullable=True),
            # RC link
            sa.Column("rc_downlink_min", sa.SmallInteger(), nullable=True),
            sa.Column("rc_downlink_avg", sa.Float(), nullable=True),
            sa.Column("rc_downlink_max", sa.SmallInteger(), nullable=True),
            sa.Column("rc_uplink_min", sa.SmallInteger(), nullable=True),
            sa.Column("rc_uplink_avg", sa.Float(), nullable=True),
            sa.Column("rc_uplink_max", sa.SmallInteger(), nullable=True),
            sa.Column("rc_zero_downlink_frames", sa.Integer(), nullable=True),
            sa.Column("rc_disconnect_events", sa.SmallInteger(), nullable=True),
            sa.Column("ofdm_signal_avg_pct", sa.Float(), nullable=True),
            # battery, this flight
            sa.Column("battery_current_max_a", sa.Float(), nullable=True),
            sa.Column("battery_energy_wh", sa.Float(), nullable=True),
            sa.Column("battery_discharge_mah", sa.Float(), nullable=True),
            sa.Column("battery_cell_count", sa.SmallInteger(), nullable=True),
            sa.Column("battery_cell_deviation_max_v", sa.Float(), nullable=True),
            sa.Column("battery_temp_min_c", sa.Float(), nullable=True),
            sa.Column("battery_temp_max_c", sa.Float(), nullable=True),
            sa.Column("battery_full_capacity_mah", sa.Float(), nullable=True),
            sa.Column("battery_current_capacity_mah", sa.Float(), nullable=True),
            # pack lifetime
            sa.Column("pack_cycle_count", sa.Integer(), nullable=True),
            sa.Column("pack_designed_capacity_mah", sa.Integer(), nullable=True),
            sa.Column("pack_full_charge_voltage_v", sa.Float(), nullable=True),
            sa.Column("pack_values_shimmed", sa.Boolean(), nullable=True),
            sa.Column("pack_values_plausible", sa.Boolean(), nullable=True),
            # config
            sa.Column("height_limit_m", sa.Float(), nullable=True),
            sa.Column("go_home_height_m", sa.Float(), nullable=True),
            sa.Column("max_allowed_height_m", sa.Float(), nullable=True),
            sa.Column("is_beginner_mode", sa.Boolean(), nullable=True),
            # identity
            sa.Column("aircraft_sn_full", sa.String(length=32), nullable=True),
            sa.Column("app_platform", sa.String(length=32), nullable=True),
            # pilot / VLOS
            sa.Column("pilot_sample_count", sa.Integer(), nullable=True),
            sa.Column("pilot_max_distance_m", sa.Float(), nullable=True),
            sa.Column("pilot_avg_distance_m", sa.Float(), nullable=True),
            sa.Column("pilot_track_stored", sa.Boolean(), nullable=True),
            # rollups
            sa.Column("event_count", sa.Integer(), nullable=True),
            sa.Column("warning_event_count", sa.Integer(), nullable=True),
            sa.Column("anomaly_flag_count", sa.SmallInteger(), nullable=True),
            # repair provenance
            sa.Column("gps_timestamps_restamped_at", sa.DateTime(), nullable=True),
            sa.Column("drone_model_previous", sa.String(length=255), nullable=True),
            sa.Column("replaced_from", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            # JSONB groups
            sa.Column("phases", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("events", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("firmware", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("health", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("sd_card", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("serials", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("extra", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.ForeignKeyConstraint(["flight_id"], ["flights.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("flight_id"),
        )

    if "flight_series" not in existing:
        op.create_table(
            "flight_series",
            sa.Column("flight_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("source", sa.String(length=16), nullable=False),
            sa.Column("name", sa.String(length=48), nullable=False),
            sa.Column("unit", sa.String(length=16), nullable=True),
            sa.Column("sample_count", sa.Integer(), nullable=False),
            sa.Column("precision_dp", sa.SmallInteger(), nullable=True),
            # Generic JSON → Postgres ``json``, deliberately NOT ``jsonb``.
            sa.Column("values", sa.JSON(), nullable=False),
            sa.ForeignKeyConstraint(["flight_id"], ["flights.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("flight_id", "source", "name"),
        )


def downgrade() -> None:
    op.drop_table("flight_series")
    op.drop_table("flight_details")
