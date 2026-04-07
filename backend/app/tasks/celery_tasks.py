import asyncio
import logging
from datetime import datetime

from celery import Celery

from app.config import settings

logger = logging.getLogger(__name__)

celery_app = Celery("doc", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
)


@celery_app.task(name="send_report_email")
def send_report_email_task(to_email: str, customer_name: str, mission_title: str, pdf_path: str):
    """Background task to send report email."""
    from app.services.email_service import send_report_email

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(
            send_report_email(
                to_email=to_email,
                customer_name=customer_name,
                mission_title=mission_title,
                pdf_path=pdf_path,
            )
        )
    finally:
        loop.close()


@celery_app.task(name="generate_report", bind=True)
def generate_report_task(
    self,
    mission_id: str,
    user_narrative: str,
    mission_title: str,
    mission_type: str,
    location: str,
    flight_summaries: list[dict],
    ground_covered_acres: float | None,
    total_duration: float,
    total_distance: float = 0,
    map_path: str | None = None,
    mission_date: str | None = None,
    company_name: str = "DroneOps",
):
    """Background task to generate LLM report content."""
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session

    from app.models.report import Report
    from app.services.ollama import generate_report as llm_generate_report

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        # Wait for Ollama to be ready before calling LLM
        import httpx as _httpx
        for attempt in range(6):
            try:
                async def _check_ollama():
                    async with _httpx.AsyncClient(timeout=5) as _client:
                        return await _client.get(f"{settings.ollama_base_url}/api/tags")
                _resp = loop.run_until_complete(_check_ollama())
                if _resp.status_code == 200:
                    break
            except Exception:
                pass
            if attempt < 5:
                import time
                logger.info("Waiting for Ollama to be ready (attempt %d/6)...", attempt + 1)
                time.sleep(5)
        else:
            raise RuntimeError(f"Ollama not reachable at {settings.ollama_base_url} after 30s")

        # Call the LLM
        llm_content = loop.run_until_complete(
            llm_generate_report(
                user_narrative=user_narrative,
                mission_title=mission_title,
                mission_type=mission_type,
                location=location,
                flight_summaries=flight_summaries,
                ground_covered_acres=ground_covered_acres,
                total_duration_seconds=total_duration,
                total_distance_meters=total_distance,
                mission_date=mission_date,
                company_name=company_name,
            )
        )

        # Save result to database using sync engine
        engine = create_engine(settings.database_url_sync)
        with Session(engine) as db:
            report = db.execute(
                select(Report).where(Report.mission_id == mission_id)
            ).scalar_one_or_none()

            if report:
                report.llm_generated_content = llm_content
                report.final_content = llm_content
                report.generated_at = datetime.utcnow()
            else:
                report = Report(
                    mission_id=mission_id,
                    user_narrative=user_narrative,
                    llm_generated_content=llm_content,
                    final_content=llm_content,
                    ground_covered_acres=ground_covered_acres if ground_covered_acres and ground_covered_acres > 0 else None,
                    flight_duration_total_seconds=total_duration if total_duration > 0 else None,
                    map_image_path=map_path,
                    generated_at=datetime.utcnow(),
                )
                db.add(report)

            db.commit()

        logger.info("Report generated for mission %s", mission_id)
        return {"status": "complete", "mission_id": mission_id}

    except Exception as exc:
        logger.error("Report generation failed for mission %s: %s", mission_id, exc)
        raise self.retry(exc=exc, max_retries=3, countdown=15)
    finally:
        loop.close()
