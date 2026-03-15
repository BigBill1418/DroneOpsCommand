import asyncio
import os

from celery import Celery

from app.config import settings

celery_app = Celery("droneops", broker=settings.redis_url, backend=settings.redis_url)
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
