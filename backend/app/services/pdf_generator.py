import logging
import os
import uuid
from datetime import datetime
from urllib.parse import urlparse

from jinja2 import Environment, FileSystemLoader, select_autoescape
from weasyprint import HTML
from weasyprint.urls import default_url_fetcher

from app.config import settings

logger = logging.getLogger("doc.pdf_generator")

# Set up Jinja2 template environment
template_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")
jinja_env = Environment(loader=FileSystemLoader(template_dir), autoescape=select_autoescape(["html"]))


def _safe_url_fetcher(url, timeout=10, **kwargs):
    """URL fetcher that gracefully handles external resource failures.

    External URLs (Google Fonts, etc.) get a short timeout and return empty
    CSS on failure instead of crashing the entire PDF generation.
    Local file:// URLs are passed through to the default fetcher.
    """
    parsed = urlparse(url)
    if parsed.scheme in ("http", "https"):
        try:
            import urllib.request
            req = urllib.request.Request(url, headers={"User-Agent": "WeasyPrint"})
            resp = urllib.request.urlopen(req, timeout=timeout)
            content_type = resp.headers.get("Content-Type", "text/css")
            data = resp.read()
            return {"string": data, "mime_type": content_type.split(";")[0].strip()}
        except Exception as exc:
            logger.warning("Could not fetch external resource %s: %s — using fallback", url, exc)
            return {"string": b"", "mime_type": "text/css"}
    # Local files — use default fetcher
    return default_url_fetcher(url, **kwargs)


def generate_pdf(
    mission: dict,
    report: dict,
    invoice: dict | None = None,
    aircraft_list: list[dict] | None = None,
    image_paths: list[dict] | None = None,
    payment_links: dict | None = None,
    download_link: dict | None = None,
    branding: dict | None = None,
) -> str:
    """Generate a PDF report and return the file path."""
    from app.routers.system_settings import BRANDING_DEFAULTS

    mission_id = mission.get("id", "unknown")
    logger.info("Starting PDF generation for mission %s", mission_id)

    brand = branding or dict(BRANDING_DEFAULTS)

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
            **brand,
        )
    except Exception as exc:
        logger.error("PDF template render failed for mission %s: %s", mission_id, exc)
        raise

    os.makedirs(settings.reports_dir, exist_ok=True)
    pdf_filename = f"report_{mission_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
    pdf_path = os.path.join(settings.reports_dir, pdf_filename)

    try:
        HTML(string=html_content, base_url=settings.reports_dir, url_fetcher=_safe_url_fetcher).write_pdf(pdf_path)
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
