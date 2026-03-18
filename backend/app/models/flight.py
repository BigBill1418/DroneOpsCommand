"""Flight log models — native flight management replacing OpenDroneLog dependency."""

import enum
import uuid
from datetime import datetime, date

from sqlalchemy import String, Text, DateTime, Date, Float, Integer, Boolean, ForeignKey, JSON, Enum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class FlightSource(str, enum.Enum):
    DJI_TXT = "dji_txt"
    LITCHI_CSV = "litchi_csv"
    AIRDATA_CSV = "airdata_csv"
    MANUAL = "manual"
    OPENDRONELOG_IMPORT = "opendronelog_import"


class Flight(Base):
    __tablename__ = "flights"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    drone_model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    drone_name: Mapped[str | None] = mapped_column(String(255), nullable=True)  # custom nickname from ODL
    drone_serial: Mapped[str | None] = mapped_column(String(255), nullable=True)
    battery_serial: Mapped[str | None] = mapped_column(String(255), nullable=True)
    start_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    duration_secs: Mapped[float] = mapped_column(Float, default=0)
    total_distance: Mapped[float] = mapped_column(Float, default=0)  # meters
    max_altitude: Mapped[float] = mapped_column(Float, default=0)  # meters
    max_speed: Mapped[float] = mapped_column(Float, default=0)  # m/s
    home_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    home_lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    point_count: Mapped[int] = mapped_column(Integer, default=0)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # list of strings
    source: Mapped[str] = mapped_column(String(50), default="manual")
    source_file_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)  # SHA-256 for dedup
    original_filename: Mapped[str | None] = mapped_column(String(500), nullable=True)
    gps_track: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # [{lat, lng, alt}, ...]
    telemetry: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # full telemetry time-series
    raw_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # original parsed blob
    aircraft_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("aircraft.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    aircraft = relationship("Aircraft", lazy="selectin")
    battery_logs = relationship("BatteryLog", back_populates="flight", lazy="noload")
