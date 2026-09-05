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
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)  # display alias from ODL
    model: Mapped[str | None] = mapped_column(String(255), nullable=True)  # e.g. "TB65"
    purchase_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    cycle_count: Mapped[int] = mapped_column(Integer, default=0)
    # ── Battery source-of-truth columns (operator decision D4, ADR-0043) ──
    # Landed by migration 0011 alongside the flight-details schema so the
    # phase that actually switches the semantics needs no migration of its
    # own. INERT until then: nothing reads or writes these yet.
    #
    # ``cycle_count`` today is a counter incremented once per imported flight
    # — it counts *flights we have logs for*, not the pack's lifetime cycles.
    # When D4 lands, the pack's own reported count becomes the displayed
    # value and the legacy increment continues here, so the two stay
    # comparable instead of one silently overwriting the other's history.
    cycle_count_observed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 'observed' (legacy per-import increment) | 'pack' (the pack's own value)
    metrics_source: Mapped[str | None] = mapped_column(String(16), nullable=True)
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
    # ``cycles_at_time`` keeps its existing meaning (the OBSERVED counter).
    # The pack's own reported count gets its own column below — silently
    # redefining this one mid-history would corrupt the series it already
    # holds.
    cycles_at_time: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pack_cycle_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    discharge_mah: Mapped[float | None] = mapped_column(Float, nullable=True)

    battery = relationship("Battery", back_populates="logs")
    flight = relationship("Flight", lazy="selectin")
