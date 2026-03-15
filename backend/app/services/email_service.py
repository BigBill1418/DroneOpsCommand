import os
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import aiosmtplib
from jinja2 import Environment, FileSystemLoader

from app.config import settings

template_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")
jinja_env = Environment(loader=FileSystemLoader(template_dir))


async def send_report_email(
    to_email: str,
    customer_name: str,
    mission_title: str,
    pdf_path: str,
) -> bool:
    """Send report PDF to customer via email."""

    if not settings.smtp_host:
        raise ValueError("SMTP not configured. Set SMTP_HOST in settings.")

    template = jinja_env.get_template("email_body.html")
    html_body = template.render(
        customer_name=customer_name,
        mission_title=mission_title,
    )

    msg = MIMEMultipart()
    msg["From"] = f"{settings.smtp_from_name} <{settings.smtp_from_email}>"
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
        hostname=settings.smtp_host,
        port=settings.smtp_port,
        username=settings.smtp_user or None,
        password=settings.smtp_password or None,
        use_tls=settings.smtp_use_tls,
    )

    return True
