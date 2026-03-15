import uuid
from datetime import datetime

from sqlalchemy import String, DateTime, JSON
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Aircraft(Base):
    __tablename__ = "aircraft"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    model_name: Mapped[str] = mapped_column(String(255), nullable=False)
    manufacturer: Mapped[str] = mapped_column(String(255), default="DJI")
    image_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    specs: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
