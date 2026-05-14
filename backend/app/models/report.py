import uuid
from datetime import datetime

from sqlalchemy import String, Text, DateTime, Float, Boolean, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB, UUID
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
    flight_distance_total_meters: Mapped[float | None] = mapped_column(Float, nullable=True)
    map_image_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    pdf_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    include_download_link: Mapped[bool] = mapped_column(Boolean, default=False)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # ADR-0015 runtime audience-leak gate (soft block).
    # Populated by `generate_report_task` after every LLM generation.
    # `has_audience_leak` is the fast-filter boolean; `audience_leak_details`
    # is a JSON list of `{rule, snippet, start, end}` records mirroring
    # `app.services.report_audience.AudienceLeak`. Empty list = clean.
    # Defaults are False / [] so legacy rows (pre-runtime-gate) and any
    # path that bypasses generation (manual edit-only) read cleanly.
    has_audience_leak: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    audience_leak_details: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")

    mission = relationship("Mission", back_populates="report")
