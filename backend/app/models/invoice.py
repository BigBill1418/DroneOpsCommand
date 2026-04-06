import enum
import uuid
from datetime import datetime

from sqlalchemy import String, Text, DateTime, Float, Integer, ForeignKey, Enum, Numeric, Boolean
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class LineItemCategory(str, enum.Enum):
    TRAVEL = "travel"
    BILLED_TIME = "billed_time"
    RAPID_DEPLOYMENT = "rapid_deployment"
    EQUIPMENT = "equipment"
    SPECIAL = "special"
    OTHER = "other"


class Invoice(Base):
    __tablename__ = "invoices"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    mission_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("missions.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    invoice_number: Mapped[str | None] = mapped_column(String(50), nullable=True)
    subtotal: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    tax_rate: Mapped[float] = mapped_column(Numeric(5, 4), default=0)
    tax_amount: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    total: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    paid_in_full: Mapped[bool] = mapped_column(Boolean, default=False)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    payment_method: Mapped[str | None] = mapped_column(String(50), nullable=True)
    stripe_checkout_session_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    stripe_payment_intent_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    mission = relationship("Mission", back_populates="invoice")
    line_items = relationship("LineItem", back_populates="invoice", lazy="selectin", cascade="all, delete-orphan")


class LineItem(Base):
    __tablename__ = "line_items"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False
    )
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    category: Mapped[LineItemCategory] = mapped_column(
        Enum(LineItemCategory), default=LineItemCategory.OTHER
    )
    quantity: Mapped[float] = mapped_column(Numeric(10, 2), default=1)
    unit_price: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    total: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    invoice = relationship("Invoice", back_populates="line_items")


class RateTemplate(Base):
    __tablename__ = "rate_templates"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    category: Mapped[LineItemCategory] = mapped_column(
        Enum(LineItemCategory), default=LineItemCategory.OTHER
    )
    default_quantity: Mapped[float] = mapped_column(Numeric(10, 2), default=1)
    default_unit: Mapped[str | None] = mapped_column(String(20), nullable=True)  # hours, miles, flat, each
    default_rate: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    is_active: Mapped[bool] = mapped_column(default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
