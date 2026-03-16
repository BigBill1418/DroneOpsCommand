import os
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import aiosmtplib
from jinja2 import Environment, FileSystemLoader
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.system_settings import SystemSetting

template_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")
jinja_env = Environment(loader=FileSystemLoader(template_dir))

SMTP_KEYS = [
    "smtp_host",
    "smtp_port",
    "smtp_user",
    "smtp_password",
    "smtp_from_email",
    "smtp_from_name",
    "smtp_use_tls",
]


async def get_smtp_settings(db: AsyncSession) -> dict:
    """Load SMTP settings from DB, falling back to env-based config."""
    result = await db.execute(
        select(SystemSetting).where(SystemSetting.key.in_(SMTP_KEYS))
    )
    db_settings = {r.key: r.value for r in result.scalars().all()}

    # DB values take priority; fall back to env config
    return {
        "smtp_host": db_settings.get("smtp_host") or settings.smtp_host,
        "smtp_port": db_settings.get("smtp_port") or str(settings.smtp_port),
        "smtp_user": db_settings.get("smtp_user") or settings.smtp_user,
        "smtp_password": db_settings.get("smtp_password") or settings.smtp_password,
        "smtp_from_email": db_settings.get("smtp_from_email") or settings.smtp_from_email,
        "smtp_from_name": db_settings.get("smtp_from_name") or settings.smtp_from_name,
        "smtp_use_tls": _parse_bool(db_settings.get("smtp_use_tls"), settings.smtp_use_tls),
    }


def _parse_bool(value: str | None, default: bool) -> bool:
    if value is None or value == "":
        return default
    return value.lower() in ("true", "1", "yes")


async def send_report_email(
    to_email: str,
    customer_name: str,
    mission_title: str,
    pdf_path: str,
    db: AsyncSession | None = None,
    download_link: dict | None = None,
) -> bool:
    """Send report PDF to customer via email."""

    # Load SMTP config from DB if session provided, else use env
    if db:
        smtp = await get_smtp_settings(db)
    else:
        smtp = {
            "smtp_host": settings.smtp_host,
            "smtp_port": str(settings.smtp_port),
            "smtp_user": settings.smtp_user,
            "smtp_password": settings.smtp_password,
            "smtp_from_email": settings.smtp_from_email,
            "smtp_from_name": settings.smtp_from_name,
            "smtp_use_tls": settings.smtp_use_tls,
        }

    if not smtp["smtp_host"]:
        raise ValueError("SMTP not configured. Set SMTP_HOST in settings.")

    template = jinja_env.get_template("email_body.html")
    html_body = template.render(
        customer_name=customer_name,
        mission_title=mission_title,
        download_link=download_link,
    )

    msg = MIMEMultipart()
    msg["From"] = f"{smtp['smtp_from_name']} <{smtp['smtp_from_email']}>"
    msg["To"] = to_email
    msg["Subject"] = f"Drone Operations Report: {mission_title}"

    msg.attach(MIMEText(html_body, "html"))

    # Attach PDF
    if pdf_path and os.path.exists(pdf_path):
        with open(pdf_path, "rb") as f:
            pdf_attachment = MIMEApplication(f.read(), _subtype="pdf")
            pdf_filename = os.path.basename(pdf_path)
            pdf_attachment.add_header("Content-Disposition", "attachment", filename=pdf_filename)
            msg.attach(pdf_attachment)

    await aiosmtplib.send(
        msg,
        hostname=smtp["smtp_host"],
        port=int(smtp["smtp_port"]),
        username=smtp["smtp_user"] or None,
        password=smtp["smtp_password"] or None,
        use_tls=smtp["smtp_use_tls"] if isinstance(smtp["smtp_use_tls"], bool) else _parse_bool(str(smtp["smtp_use_tls"]), True),
    )

    return True
