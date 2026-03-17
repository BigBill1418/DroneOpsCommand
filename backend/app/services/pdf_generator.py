import logging
import os
import uuid
from datetime import datetime

from jinja2 import Environment, FileSystemLoader, select_autoescape
from weasyprint import HTML

from app.config import settings

logger = logging.getLogger("droneops.pdf_generator")

# Set up Jinja2 template environment
template_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")
jinja_env = Environment(loader=FileSystemLoader(template_dir), autoescape=select_autoescape(["html"]))


def generate_pdf(
    mission: dict,
    report: dict,
    invoice: dict | None = None,
    aircraft_list: list[dict] | None = None,
    image_paths: list[dict] | None = None,
    payment_links: dict | None = None,
    download_link: dict | None = None,
) -> str:
    """Generate a PDF report and return the file path."""
    mission_id = mission.get("id", "unknown")
    logger.info("Starting PDF generation for mission %s", mission_id)

    try:
        template = jinja_env.get_template("report_pdf.html")
    except Exception as exc:
        logger.error("Failed to load PDF template: %s", exc)
        raise

    try:
        html_content = template.render(
            mission=mission,
            report=report,
            invoice=invoice,
            aircraft_list=aircraft_list or [],
            images=image_paths or [],
            payment_links=payment_links or {},
            download_link=download_link,
            generated_at=datetime.utcnow().strftime("%B %d, %Y"),
            year=datetime.utcnow().year,
        )
    except Exception as exc:
        logger.error("PDF template render failed for mission %s: %s", mission_id, exc)
        raise

    os.makedirs(settings.reports_dir, exist_ok=True)
    pdf_filename = f"report_{mission_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
    pdf_path = os.path.join(settings.reports_dir, pdf_filename)

    try:
        HTML(string=html_content, base_url=settings.reports_dir).write_pdf(pdf_path)
        logger.info("PDF generated: %s", pdf_path)
    except Exception as exc:
        logger.error("WeasyPrint failed for mission %s: %s", mission_id, exc, exc_info=True)
        # Clean up partial file
        if os.path.exists(pdf_path):
            try:
                os.remove(pdf_path)
            except OSError:
                pass
        raise

    return pdf_path
