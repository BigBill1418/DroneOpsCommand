"""Battery health tracking models."""

import uuid
from datetime import datetime, date

from sqlalchemy import String, Text, DateTime, Date, Float, Integer, ForeignKey, JSON
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Battery(Base):
    __tablename__ = "batteries"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    serial: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    model: Mapped[str | None] = mapped_column(String(255), nullable=True)  # e.g. "TB65"
    purchase_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    cycle_count: Mapped[int] = mapped_column(Integer, default=0)
    last_voltage: Mapped[float | None] = mapped_column(Float, nullable=True)
    health_pct: Mapped[float | None] = mapped_column(Float, nullable=True)  # 0-100
    status: Mapped[str] = mapped_column(String(50), default="active")  # active, retired, service
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    aircraft_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("aircraft.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    aircraft = relationship("Aircraft", lazy="selectin")
    logs = relationship("BatteryLog", back_populates="battery", lazy="noload", cascade="all, delete-orphan")


class BatteryLog(Base):
    __tablename__ = "battery_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    battery_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("batteries.id", ondelete="CASCADE"), nullable=False
    )
    flight_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("flights.id", ondelete="SET NULL"), nullable=True
    )
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    start_voltage: Mapped[float | None] = mapped_column(Float, nullable=True)
    end_voltage: Mapped[float | None] = mapped_column(Float, nullable=True)
    min_voltage: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_temp: Mapped[float | None] = mapped_column(Float, nullable=True)  # Celsius
    cycles_at_time: Mapped[int | None] = mapped_column(Integer, nullable=True)
    discharge_mah: Mapped[float | None] = mapped_column(Float, nullable=True)

    battery = relationship("Battery", back_populates="logs")
    flight = relationship("Flight", lazy="selectin")
