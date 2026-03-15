import uuid
from datetime import datetime

from sqlalchemy import String, Text, DateTime, Float, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    mission_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("missions.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    user_narrative: Mapped[str | None] = mapped_column(Text, nullable=True)
    llm_generated_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    final_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    ground_covered_acres: Mapped[float | None] = mapped_column(Float, nullable=True)
    flight_duration_total_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    map_image_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    pdf_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    mission = relationship("Mission", back_populates="report")
