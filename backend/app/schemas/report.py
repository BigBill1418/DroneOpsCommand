from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class ReportGenerateRequest(BaseModel):
    user_narrative: str


class ReportUpdateRequest(BaseModel):
    final_content: str | None = None
    user_narrative: str | None = None


class ReportResponse(BaseModel):
    id: UUID
    mission_id: UUID
    user_narrative: str | None
    llm_generated_content: str | None
    final_content: str | None
    ground_covered_acres: float | None
    flight_duration_total_seconds: float | None
    flight_distance_total_meters: float | None
    map_image_path: str | None
    pdf_path: str | None
    generated_at: datetime | None
    sent_at: datetime | None

    model_config = {"from_attributes": True}
