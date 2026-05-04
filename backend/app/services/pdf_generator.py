import logging
import os
import uuid
from datetime import datetime

from jinja2 import Environment, FileSystemLoader, select_autoescape
from weasyprint import HTML, default_url_fetcher

from app.config import settings

logger = logging.getLogger("doc.pdf_generator")

# Set up Jinja2 template environment
template_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")
jinja_env = Environment(loader=FileSystemLoader(template_dir), autoescape=select_autoescape(["html"]))


# Transparent 1x1 PNG used when an image can't be loaded
_FALLBACK_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01"
    b"\r\n\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _safe_url_fetcher(url: str):
    """URL fetcher that returns a transparent pixel for missing/broken resources."""
    try:
        # For file:// URLs, check existence first
        if url.startswith("file://"):
            path = url[7:]  # strip file://
            if not os.path.isfile(path):
                logger.warning("PDF resource not found, using fallback: %s", url)
                return {"string": _FALLBACK_PNG, "mime_type": "image/png"}
        return default_url_fetcher(url)
    except Exception as exc:
        logger.warning("PDF resource fetch failed (%s), using fallback: %s", exc, url)
        return {"string": _FALLBACK_PNG, "mime_type": "image/png"}


def generate_pdf(
    mission: dict,
    report: dict,
    invoice: dict | None = None,
    aircraft_list: list[dict] | None = None,
    image_paths: list[dict] | None = None,
    payment_links: dict | None = None,
    download_link: dict | None = None,
    branding: dict | None = None,
    stripe_pay_url: str | None = None,
) -> str:
    """Generate a PDF report and return the file path."""
    from app.routers.system_settings import BRANDING_DEFAULTS

    mission_id = mission.get("id", "unknown")
    logger.info("Starting PDF generation for mission %s", mission_id)

    brand = branding or dict(BRANDING_DEFAULTS)

    # Resolve company logo to absolute file path for WeasyPrint
    company_logo_path = ""
    logo_rel = brand.get("company_logo", "")
    if logo_rel:
        abs_logo = os.path.join(settings.upload_dir, logo_rel)
        if os.path.isfile(abs_logo):
            company_logo_path = abs_logo
            logger.debug("PDF using company logo: %s", abs_logo)
        else:
            logger.warning("Company logo not found at %s", abs_logo)

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
            stripe_pay_url=stripe_pay_url,
            download_link=download_link,
            generated_at=datetime.utcnow().strftime("%B %d, %Y"),
            year=datetime.utcnow().year,
            company_logo_path=company_logo_path,
            **brand,
        )
    except Exception as exc:
        logger.error("PDF template render failed for mission %s: %s", mission_id, exc)
        raise

    os.makedirs(settings.reports_dir, exist_ok=True)
    pdf_filename = f"report_{mission_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
    pdf_path = os.path.join(settings.reports_dir, pdf_filename)

    try:
        HTML(
            string=html_content,
            base_url=settings.reports_dir,
            url_fetcher=_safe_url_fetcher,
        ).write_pdf(pdf_path)
        if not os.path.exists(pdf_path) or os.path.getsize(pdf_path) == 0:
            raise RuntimeError("PDF file was not created or is empty")
        logger.info("PDF generated: %s (%d bytes)", pdf_path, os.path.getsize(pdf_path))
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
