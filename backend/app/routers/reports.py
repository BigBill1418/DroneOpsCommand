from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.jwt import get_current_user
from app.database import get_db
from app.models.invoice import Invoice
from app.models.mission import Mission
from app.models.report import Report
from app.models.user import User
from app.schemas.report import ReportGenerateRequest, ReportResponse, ReportUpdateRequest
from app.services.map_renderer import calculate_area_acres, extract_gps_tracks, render_static_map
from app.services.ollama import generate_report as llm_generate_report
from app.services.pdf_generator import generate_pdf

router = APIRouter(prefix="/api/missions", tags=["reports"])


def _flights_to_dicts(mission: Mission) -> list[dict]:
    flights = []
    for f in mission.flights:
        flight_dict = {
            "opendronelog_flight_id": f.opendronelog_flight_id,
            "flight_data_cache": f.flight_data_cache,
        }
        if f.aircraft:
            flight_dict["aircraft"] = {
                "model_name": f.aircraft.model_name,
                "manufacturer": f.aircraft.manufacturer,
            }
        flights.append(flight_dict)
    return flights


def _build_flight_summaries(mission: Mission) -> list[dict]:
    summaries = []
    for f in mission.flights:
        cache = f.flight_data_cache or {}
        summary = {
            "aircraft": f.aircraft.model_name if f.aircraft else "Unknown",
            "max_altitude": cache.get("max_altitude", cache.get("maxAltitude", cache.get("max_height", "Unknown"))),
        }
        if cache.get("notes"):
            summary["notes"] = cache["notes"]
        summaries.append(summary)
    return summaries


@router.get("/{mission_id}/report", response_model=ReportResponse)
async def get_report(
    mission_id: UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    result = await db.execute(select(Report).where(Report.mission_id == mission_id))
    report = result.scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    return report


@router.post("/{mission_id}/report/generate")
async def generate_report(
    mission_id: UUID,
    data: ReportGenerateRequest,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Kick off LLM report generation as a background task."""
    from app.tasks.celery_tasks import generate_report_task

    result = await db.execute(select(Mission).where(Mission.id == mission_id))
    mission = result.scalar_one_or_none()
    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")

    # Pre-compute everything we can before handing off to the worker
    flights = _flights_to_dicts(mission)
    tracks = extract_gps_tracks(flights)
    acres = calculate_area_acres(tracks)
    flight_summaries = _build_flight_summaries(mission)
    map_path = render_static_map(flights) if tracks else None

    total_duration = 0
    total_distance = 0
    for f in mission.flights:
        cache = f.flight_data_cache or {}
        dur = cache.get("duration_secs", cache.get("durationSecs",
              cache.get("duration_seconds", cache.get("flight_time_seconds", 0))))
        if isinstance(dur, (int, float)):
            total_duration += dur
        dist = cache.get("total_distance", cache.get("totalDistance",
               cache.get("distance", cache.get("distance_meters", 0))))
        if isinstance(dist, (int, float)):
            total_distance += dist

    # Ensure a report record exists so the frontend can poll GET /report
    existing = await db.execute(select(Report).where(Report.mission_id == mission_id))
    report = existing.scalar_one_or_none()
    if report:
        report.user_narrative = data.user_narrative
        report.ground_covered_acres = acres if acres > 0 else None
        report.flight_duration_total_seconds = total_duration if total_duration > 0 else None
        report.flight_distance_total_meters = total_distance if total_distance > 0 else None
        report.map_image_path = map_path
    else:
        report = Report(
            mission_id=mission_id,
            user_narrative=data.user_narrative,
            ground_covered_acres=acres if acres > 0 else None,
            flight_duration_total_seconds=total_duration if total_duration > 0 else None,
            flight_distance_total_meters=total_distance if total_distance > 0 else None,
            map_image_path=map_path,
        )
        db.add(report)
    await db.flush()

    # Dispatch to Celery worker
    task = generate_report_task.delay(
        mission_id=str(mission_id),
        user_narrative=data.user_narrative,
        mission_title=mission.title,
        mission_type=mission.mission_type.value,
        mission_date=str(mission.mission_date) if mission.mission_date else None,
        location=mission.location_name or "Not specified",
        flight_summaries=flight_summaries,
        ground_covered_acres=acres if acres > 0 else None,
        total_duration=total_duration,
        total_distance=total_distance,
        map_path=map_path,
    )

    return {"task_id": task.id, "status": "generating"}


@router.get("/{mission_id}/report/status/{task_id}")
async def get_generation_status(
    mission_id: UUID,
    task_id: str,
    _user: User = Depends(get_current_user),
):
    """Poll the status of a background report generation task."""
    from app.tasks.celery_tasks import celery_app

    result = celery_app.AsyncResult(task_id)

    if result.state == "PENDING":
        return {"status": "generating"}
    elif result.state == "SUCCESS":
        return {"status": "complete"}
    elif result.state == "FAILURE":
        return {"status": "failed", "detail": str(result.result)}
    else:
        return {"status": "generating"}


@router.put("/{mission_id}/report", response_model=ReportResponse)
async def update_report(
    mission_id: UUID,
    data: ReportUpdateRequest,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    result = await db.execute(select(Report).where(Report.mission_id == mission_id))
    report = result.scalar_one_or_none()

    if report:
        for key, value in data.model_dump(exclude_unset=True).items():
            setattr(report, key, value)
    else:
        # Create a new report record (draft save before generation)
        report = Report(mission_id=mission_id, **data.model_dump(exclude_unset=True))
        db.add(report)

    await db.flush()
    await db.refresh(report)
    return report


@router.post("/{mission_id}/report/pdf")
async def generate_report_pdf(
    mission_id: UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Generate PDF from the report."""
    result = await db.execute(
        select(Mission)
        .where(Mission.id == mission_id)
        .options(
            selectinload(Mission.customer),
            selectinload(Mission.invoice).selectinload(Invoice.line_items),
        )
    )
    mission = result.scalar_one_or_none()
    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")

    report_result = await db.execute(select(Report).where(Report.mission_id == mission_id))
    report = report_result.scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail="Report not generated yet")

    # Build data for PDF template
    mission_dict = {
        "id": str(mission.id),
        "title": mission.title,
        "mission_type": mission.mission_type.value,
        "description": mission.description,
        "mission_date": str(mission.mission_date) if mission.mission_date else "",
        "location_name": mission.location_name or "",
        "customer_name": mission.customer.name if mission.customer else "",
        "customer_email": mission.customer.email if mission.customer else "",
        "customer_company": mission.customer.company if mission.customer else "",
    }

    report_dict = {
        "final_content": report.final_content or "",
        "ground_covered_acres": report.ground_covered_acres,
        "flight_duration_total_seconds": report.flight_duration_total_seconds,
        "flight_distance_total_meters": report.flight_distance_total_meters,
        "map_image_path": report.map_image_path,
    }

    # Aircraft used
    aircraft_list = []
    seen_aircraft = set()
    for f in mission.flights:
        if f.aircraft and f.aircraft.id not in seen_aircraft:
            seen_aircraft.add(f.aircraft.id)
            aircraft_list.append({
                "model_name": f.aircraft.model_name,
                "manufacturer": f.aircraft.manufacturer,
                "image_filename": f.aircraft.image_filename,
                "specs": f.aircraft.specs,
            })

    # Invoice
    invoice_dict = None
    if mission.invoice:
        invoice_dict = {
            "invoice_number": mission.invoice.invoice_number,
            "subtotal": float(mission.invoice.subtotal),
            "tax_rate": float(mission.invoice.tax_rate),
            "tax_amount": float(mission.invoice.tax_amount),
            "total": float(mission.invoice.total),
            "paid_in_full": mission.invoice.paid_in_full,
            "notes": mission.invoice.notes,
            "line_items": [
                {
                    "description": li.description,
                    "category": li.category.value,
                    "quantity": float(li.quantity),
                    "unit_price": float(li.unit_price),
                    "total": float(li.total),
                }
                for li in mission.invoice.line_items
            ],
        }

    # Load payment links for invoice
    payment_links = {}
    if mission.is_billable and invoice_dict and not invoice_dict.get("paid_in_full"):
        from app.models.system_settings import SystemSetting
        pl_result = await db.execute(
            select(SystemSetting).where(SystemSetting.key.in_(["paypal_link", "venmo_link"]))
        )
        payment_links = {r.key: r.value for r in pl_result.scalars().all()}

    # Images
    image_list = [{"file_path": img.file_path, "caption": img.caption} for img in mission.images]

    pdf_path = generate_pdf(
        mission=mission_dict,
        report=report_dict,
        invoice=invoice_dict if mission.is_billable else None,
        aircraft_list=aircraft_list,
        image_paths=image_list,
        payment_links=payment_links,
    )

    report.pdf_path = pdf_path
    await db.flush()

    return FileResponse(pdf_path, media_type="application/pdf", filename=f"report_{mission.title}.pdf")


@router.post("/{mission_id}/report/send")
async def send_report(
    mission_id: UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Send report PDF to customer via email."""
    from app.services.email_service import send_report_email

    result = await db.execute(select(Mission).where(Mission.id == mission_id))
    mission = result.scalar_one_or_none()
    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")
    if not mission.customer or not mission.customer.email:
        raise HTTPException(status_code=400, detail="Customer email not set")

    report_result = await db.execute(select(Report).where(Report.mission_id == mission_id))
    report = report_result.scalar_one_or_none()
    if not report or not report.pdf_path:
        raise HTTPException(status_code=400, detail="PDF not generated yet")

    await send_report_email(
        to_email=mission.customer.email,
        customer_name=mission.customer.name,
        mission_title=mission.title,
        pdf_path=report.pdf_path,
        db=db,
    )

    report.sent_at = datetime.utcnow()
    mission.status = "sent"
    await db.flush()

    return {"message": "Report sent successfully"}
