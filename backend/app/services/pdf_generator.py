import os
import uuid
from datetime import datetime

from jinja2 import Environment, FileSystemLoader
from weasyprint import HTML

from app.config import settings

# Set up Jinja2 template environment
template_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")
jinja_env = Environment(loader=FileSystemLoader(template_dir))


def generate_pdf(
    mission: dict,
    report: dict,
    invoice: dict | None = None,
    aircraft_list: list[dict] | None = None,
    image_paths: list[dict] | None = None,
) -> str:
    """Generate a PDF report and return the file path."""

    template = jinja_env.get_template("report_pdf.html")

    html_content = template.render(
        mission=mission,
        report=report,
        invoice=invoice,
        aircraft_list=aircraft_list or [],
        images=image_paths or [],
        generated_at=datetime.utcnow().strftime("%B %d, %Y"),
        year=datetime.utcnow().year,
    )

    os.makedirs(settings.reports_dir, exist_ok=True)
    pdf_filename = f"report_{mission.get('id', uuid.uuid4().hex[:8])}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
    pdf_path = os.path.join(settings.reports_dir, pdf_filename)

    HTML(string=html_content, base_url=settings.reports_dir).write_pdf(pdf_path)

    return pdf_path
