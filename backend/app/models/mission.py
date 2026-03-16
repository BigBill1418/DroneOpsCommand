import enum
import uuid
from datetime import datetime, date

from sqlalchemy import String, Text, DateTime, Date, Boolean, ForeignKey, JSON, Integer, Enum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class MissionType(str, enum.Enum):
    SAR = "sar"
    VIDEOGRAPHY = "videography"
    LOST_PET = "lost_pet"
    INSPECTION = "inspection"
    MAPPING = "mapping"
    PHOTOGRAPHY = "photography"
    SURVEY = "survey"
    SECURITY_INVESTIGATIONS = "security_investigations"
    OTHER = "other"


class MissionStatus(str, enum.Enum):
    DRAFT = "draft"
    COMPLETED = "completed"
    SENT = "sent"


class Mission(Base):
    __tablename__ = "missions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customers.id"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    mission_type: Mapped[MissionType] = mapped_column(
        Enum(MissionType), default=MissionType.OTHER
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    mission_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    location_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    area_coordinates: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[MissionStatus] = mapped_column(
        Enum(MissionStatus), default=MissionStatus.DRAFT
    )
    is_billable: Mapped[bool] = mapped_column(Boolean, default=False)
    unas_folder_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    download_link_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    download_link_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    customer = relationship("Customer", back_populates="missions", lazy="selectin")
    flights = relationship("MissionFlight", back_populates="mission", lazy="selectin", cascade="all, delete-orphan")
    images = relationship("MissionImage", back_populates="mission", lazy="selectin", cascade="all, delete-orphan")
    report = relationship("Report", back_populates="mission", uselist=False, lazy="noload", cascade="all, delete-orphan")
    invoice = relationship("Invoice", back_populates="mission", uselist=False, lazy="noload", cascade="all, delete-orphan")


class MissionFlight(Base):
    __tablename__ = "mission_flights"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    mission_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("missions.id", ondelete="CASCADE"), nullable=False
    )
    opendronelog_flight_id: Mapped[str] = mapped_column(String(255), nullable=False)
    aircraft_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("aircraft.id"), nullable=True
    )
    flight_data_cache: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    added_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    mission = relationship("Mission", back_populates="flights")
    aircraft = relationship("Aircraft", lazy="selectin")


class MissionImage(Base):
    __tablename__ = "mission_images"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    mission_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("missions.id", ondelete="CASCADE"), nullable=False
    )
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    caption: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    mission = relationship("Mission", back_populates="images")
