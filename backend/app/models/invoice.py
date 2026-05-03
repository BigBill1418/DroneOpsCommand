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


# ADR-0009 — payment_phase string literals.
# Computed from (deposit_required, deposit_paid, mission.status,
# paid_in_full); never persisted. See docs/adr/0009-deposit-feature.md
# §3.2 for the truth table.
PAYMENT_PHASE_DEPOSIT_DUE = "deposit_due"
PAYMENT_PHASE_AWAITING_COMPLETION = "awaiting_completion"
PAYMENT_PHASE_BALANCE_DUE = "balance_due"
PAYMENT_PHASE_PAID_IN_FULL = "paid_in_full"


def compute_payment_phase(
    *,
    deposit_required: bool,
    deposit_paid: bool,
    mission_completed_or_sent: bool,
    paid_in_full: bool,
) -> str:
    """Pure function — single source of truth for ADR-0009 §3.2 truth table.

    Lives outside the ORM so tests can drive it without spinning up
    a DB session, and so the client_portal router can derive the same
    value off a (mission, invoice) pair without re-implementing the
    logic. The Invoice.payment_phase property is a thin shim that
    pulls mission.status from the joined relation.
    """
    if paid_in_full:
        return PAYMENT_PHASE_PAID_IN_FULL
    if deposit_required and not deposit_paid:
        return PAYMENT_PHASE_DEPOSIT_DUE
    # Either deposit_required is False, or deposit is paid — gate
    # on whether the operator has marked work delivered.
    if mission_completed_or_sent:
        return PAYMENT_PHASE_BALANCE_DUE
    return PAYMENT_PHASE_AWAITING_COMPLETION


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

    # ──────────────────────────────────────────────────────────────────
    # ADR-0009 — Two-phase deposit + balance billing.
    # All seven columns are additive with safe defaults; pre-existing
    # rows behave exactly as before (deposit_required=False keeps the
    # legacy single-payment path).
    # ──────────────────────────────────────────────────────────────────
    deposit_required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    deposit_amount: Mapped[float] = mapped_column(Numeric(10, 2), default=0, nullable=False)
    deposit_paid: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    deposit_paid_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    deposit_payment_intent_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    deposit_checkout_session_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    deposit_payment_method: Mapped[str | None] = mapped_column(String(50), nullable=True)

    mission = relationship("Mission", back_populates="invoice")
    line_items = relationship("LineItem", back_populates="invoice", lazy="selectin", cascade="all, delete-orphan")

    @property
    def balance_amount(self) -> float:
        """Remaining balance owed after deposit (always >= 0)."""
        total = float(self.total or 0)
        deposit = float(self.deposit_amount or 0) if self.deposit_required else 0.0
        return max(0.0, round(total - deposit, 2))

    def payment_phase_for(self, mission_status) -> str:
        """Compute payment_phase given the joined mission's status enum.

        Kept as a method (not a `@property` reading `self.mission`) to
        avoid forcing every caller into a lazy-load round-trip. The
        client portal router has the mission row in scope already.
        """
        from app.models.mission import MissionStatus  # local to avoid cycle
        completed = mission_status in {MissionStatus.COMPLETED, MissionStatus.SENT}
        return compute_payment_phase(
            deposit_required=bool(self.deposit_required),
            deposit_paid=bool(self.deposit_paid),
            mission_completed_or_sent=completed,
            paid_in_full=bool(self.paid_in_full),
        )


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
