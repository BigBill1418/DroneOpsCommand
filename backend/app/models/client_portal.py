import uuid
from datetime import datetime

from sqlalchemy import String, Text, DateTime, ForeignKey, JSON
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class ClientAccessToken(Base):
    __tablename__ = "client_access_tokens"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customers.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    mission_scope: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_accessed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)

    customer = relationship("Customer", lazy="selectin")
