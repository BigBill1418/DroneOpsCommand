import uuid
from datetime import datetime

from sqlalchemy import String, Text, DateTime, Boolean
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    city: Mapped[str | None] = mapped_column(String(255), nullable=True)
    state: Mapped[str | None] = mapped_column(String(100), nullable=True)
    zip_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    company: Mapped[str | None] = mapped_column(String(255), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Intake / TOS fields
    intake_token: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    intake_token_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    intake_completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    tos_signed: Mapped[bool] = mapped_column(Boolean, default=False)
    tos_signed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    signature_data: Mapped[str | None] = mapped_column(Text, nullable=True)
    tos_pdf_path: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Client portal auth
    portal_password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    portal_password_set_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    missions = relationship("Mission", back_populates="customer", lazy="noload")
