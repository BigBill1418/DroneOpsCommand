from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class ReportGenerateRequest(BaseModel):
    user_narrative: str
    include_download_link: bool = False


class ReportUpdateRequest(BaseModel):
    final_content: str | None = None
    user_narrative: str | None = None
    include_download_link: bool | None = None


class AudienceLeakDetail(BaseModel):
    """Single audience-leak record surfaced to the editorial-gate UI.

    Mirrors the `AudienceLeak` dataclass in
    `app.services.report_audience` but as a plain Pydantic model so it
    serializes through the FastAPI layer.
    """

    rule: str
    snippet: str
    start: int
    end: int


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
    include_download_link: bool = False
    generated_at: datetime | None
    sent_at: datetime | None
    # ADR-0015 soft-block runtime gate — fast filter + structured findings.
    has_audience_leak: bool = False
    audience_leak_details: list[AudienceLeakDetail] = []

    model_config = {"from_attributes": True}
