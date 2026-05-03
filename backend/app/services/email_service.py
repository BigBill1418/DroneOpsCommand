import logging
import os
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import aiosmtplib
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.system_settings import SystemSetting

logger = logging.getLogger("doc.email")

template_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")
jinja_env = Environment(loader=FileSystemLoader(template_dir), autoescape=select_autoescape(["html"]))

SMTP_KEYS = [
    "smtp_host",
    "smtp_port",
    "smtp_user",
    "smtp_password",
    "smtp_from_email",
    "smtp_from_name",
    "smtp_use_tls",
]


async def _get_branding(db: AsyncSession | None) -> dict:
    """Load branding settings from DB, with defaults. Adds company_logo_url for emails."""
    from app.routers.system_settings import BRANDING_DEFAULTS
    if not db:
        return dict(BRANDING_DEFAULTS)
    try:
        from app.routers.system_settings import get_branding
        brand = await get_branding(db)
        # Build absolute logo URL for email templates
        logo = brand.get("company_logo", "")
        if logo:
            brand["company_logo_url"] = f"{settings.frontend_url}/uploads/{logo}"
        else:
            brand["company_logo_url"] = ""
        return brand
    except Exception:
        return dict(BRANDING_DEFAULTS)


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
    logger.info("Preparing email to %s for mission '%s'", to_email, mission_title)

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
        logger.error("SMTP not configured — cannot send email")
        raise ValueError("SMTP not configured. Set SMTP_HOST in settings.")

    # Load branding for template
    branding = await _get_branding(db)

    template = jinja_env.get_template("email_body.html")
    html_body = template.render(
        customer_name=customer_name,
        mission_title=mission_title,
        download_link=download_link,
        **branding,
    )

    msg = MIMEMultipart()
    msg["From"] = f"{smtp['smtp_from_name']} <{smtp['smtp_from_email']}>"
    msg["To"] = to_email
    msg["Subject"] = f"Drone Operations Report: {mission_title}"

    msg.attach(MIMEText(html_body, "html"))

    # Attach PDF
    if pdf_path:
        try:
            with open(pdf_path, "rb") as f:
                pdf_attachment = MIMEApplication(f.read(), _subtype="pdf")
                pdf_filename = os.path.basename(pdf_path)
                pdf_attachment.add_header("Content-Disposition", "attachment", filename=pdf_filename)
                msg.attach(pdf_attachment)
                logger.info("PDF attached: %s", pdf_filename)
        except FileNotFoundError:
            logger.error("PDF file not found for email attachment: %s", pdf_path)
            raise ValueError(f"PDF file not found: {pdf_path}")

    try:
        smtp_port = int(smtp["smtp_port"])
    except (ValueError, TypeError):
        raise ValueError(f"Invalid SMTP port: {smtp['smtp_port']}")

    tls_flag = smtp["smtp_use_tls"] if isinstance(smtp["smtp_use_tls"], bool) else _parse_bool(str(smtp["smtp_use_tls"]), True)
    # Port 465 uses implicit TLS (use_tls); port 587 and others use STARTTLS (start_tls)
    tls_kwargs = {"use_tls": True} if smtp_port == 465 else {"start_tls": tls_flag}

    try:
        await aiosmtplib.send(
            msg,
            hostname=smtp["smtp_host"],
            port=smtp_port,
            username=smtp["smtp_user"] or None,
            password=smtp["smtp_password"] or None,
            **tls_kwargs,
        )
        logger.info("Email sent successfully to %s", to_email)
    except Exception as exc:
        logger.error("SMTP send failed to %s: %s", to_email, exc, exc_info=True)
        raise

    return True


async def send_intake_email(
    to_email: str,
    customer_name: str | None,
    intake_url: str,
    expires_at: object,
    is_existing_customer: bool = False,
    db: AsyncSession | None = None,
) -> bool:
    """Send intake form link to customer via email."""
    import time as _time
    start = _time.perf_counter()

    logger.info("[EMAIL-INTAKE] Preparing intake email to=%s, customer_name=%s, existing=%s, expires=%s",
                to_email, customer_name, is_existing_customer, expires_at)

    if db:
        smtp = await get_smtp_settings(db)
        logger.debug("[EMAIL-INTAKE] Loaded SMTP settings from DB: host=%s port=%s user=%s tls=%s",
                      smtp["smtp_host"], smtp["smtp_port"], smtp["smtp_user"], smtp["smtp_use_tls"])
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
        logger.debug("[EMAIL-INTAKE] Using env SMTP settings: host=%s port=%s", smtp["smtp_host"], smtp["smtp_port"])

    if not smtp["smtp_host"]:
        logger.error("[EMAIL-INTAKE] SMTP not configured — cannot send intake email to %s", to_email)
        raise ValueError("SMTP not configured. Set SMTP_HOST in settings.")

    # Load branding for template
    branding = await _get_branding(db)

    logger.debug("[EMAIL-INTAKE] Rendering intake_email.html template")
    template = jinja_env.get_template("intake_email.html")
    html_body = template.render(
        customer_name=customer_name,
        intake_url=intake_url,
        expires_at=expires_at.strftime("%B %d, %Y") if hasattr(expires_at, "strftime") else str(expires_at),
        is_existing_customer=is_existing_customer,
        **branding,
    )
    logger.debug("[EMAIL-INTAKE] Template rendered, html_length=%d", len(html_body))

    msg = MIMEMultipart()
    msg["From"] = f"{smtp['smtp_from_name']} <{smtp['smtp_from_email']}>"
    msg["To"] = to_email
    cn = branding.get("company_name", "DroneOps")
    subject = f"Complete Your Customer Profile — {cn}" if is_existing_customer else f"Welcome to {cn} — Complete Your Onboarding"
    msg["Subject"] = subject
    msg.attach(MIMEText(html_body, "html"))

    try:
        smtp_port = int(smtp["smtp_port"])
    except (ValueError, TypeError):
        raise ValueError(f"Invalid SMTP port: {smtp['smtp_port']}")

    tls_flag = smtp["smtp_use_tls"] if isinstance(smtp["smtp_use_tls"], bool) else _parse_bool(str(smtp["smtp_use_tls"]), True)
    # Port 465 uses implicit TLS (use_tls); port 587 and others use STARTTLS (start_tls)
    tls_kwargs = {"use_tls": True} if smtp_port == 465 else {"start_tls": tls_flag}

    logger.info("[EMAIL-INTAKE] Sending via SMTP host=%s:%s to=%s subject='%s'", smtp["smtp_host"], smtp_port, to_email, subject)

    try:
        await aiosmtplib.send(
            msg,
            hostname=smtp["smtp_host"],
            port=smtp_port,
            username=smtp["smtp_user"] or None,
            password=smtp["smtp_password"] or None,
            **tls_kwargs,
        )
        elapsed = _time.perf_counter() - start
        logger.info("[EMAIL-INTAKE] SUCCESS — Sent to %s in %.3fs", to_email, elapsed)
    except Exception as exc:
        elapsed = _time.perf_counter() - start
        logger.error("[EMAIL-INTAKE] FAILED — SMTP send to %s failed after %.3fs: %s", to_email, elapsed, exc, exc_info=True)
        raise

    return True


async def send_client_portal_email(
    to_email: str,
    customer_name: str | None,
    mission_title: str,
    portal_url: str,
    expires_at: object,
    db: AsyncSession | None = None,
) -> bool:
    """Send client portal link to customer via email."""
    import time as _time
    start = _time.perf_counter()

    logger.info(
        "[EMAIL-PORTAL] Preparing portal email to=%s, customer=%s, mission='%s'",
        to_email, customer_name, mission_title,
    )

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
        logger.error("[EMAIL-PORTAL] SMTP not configured — cannot send portal email to %s", to_email)
        raise ValueError("SMTP not configured. Set SMTP_HOST in settings.")

    branding = await _get_branding(db)

    template = jinja_env.get_template("client_portal_email.html")
    html_body = template.render(
        customer_name=customer_name,
        mission_title=mission_title,
        portal_url=portal_url,
        expires_at=expires_at.strftime("%B %d, %Y") if hasattr(expires_at, "strftime") else str(expires_at),
        **branding,
    )

    msg = MIMEMultipart()
    msg["From"] = f"{smtp['smtp_from_name']} <{smtp['smtp_from_email']}>"
    msg["To"] = to_email
    cn = branding.get("company_name", "DroneOps")
    msg["Subject"] = f"Your Mission Portal — {cn}"
    msg.attach(MIMEText(html_body, "html"))

    try:
        smtp_port = int(smtp["smtp_port"])
    except (ValueError, TypeError):
        raise ValueError(f"Invalid SMTP port: {smtp['smtp_port']}")

    tls_flag = smtp["smtp_use_tls"] if isinstance(smtp["smtp_use_tls"], bool) else _parse_bool(str(smtp["smtp_use_tls"]), True)
    tls_kwargs = {"use_tls": True} if smtp_port == 465 else {"start_tls": tls_flag}

    logger.info("[EMAIL-PORTAL] Sending via SMTP host=%s:%s to=%s", smtp["smtp_host"], smtp_port, to_email)

    try:
        await aiosmtplib.send(
            msg,
            hostname=smtp["smtp_host"],
            port=smtp_port,
            username=smtp["smtp_user"] or None,
            password=smtp["smtp_password"] or None,
            **tls_kwargs,
        )
        elapsed = _time.perf_counter() - start
        logger.info("[EMAIL-PORTAL] SUCCESS — Sent to %s in %.3fs", to_email, elapsed)
    except Exception as exc:
        elapsed = _time.perf_counter() - start
        logger.error("[EMAIL-PORTAL] FAILED — SMTP send to %s failed after %.3fs: %s", to_email, elapsed, exc, exc_info=True)
        raise

    return True


async def send_signed_tos_to_both_parties(
    *,
    client_email: str,
    client_name: str,
    audit_id: str,
    signed_pdf: bytes,
    db: AsyncSession | None = None,
) -> bool:
    """Email the signed TOS PDF to the customer and BCC the operator.

    Adapted to this repo's ``aiosmtplib`` direct-send pattern. Failure
    is logged + raised so the route layer can swallow it (the row +
    PDF on disk are the source of truth; email is best-effort per
    TOS-Rebuild §2.6).

    The operator BCC defaults to ``smtp_from_email`` — the same
    address every other notification uses. We do NOT introduce a new
    settings key for this; it would just drift from the other email
    flows.
    """
    import time as _time
    start = _time.perf_counter()

    logger.info(
        "[EMAIL-TOS-SIGNED] Preparing signed-TOS email to=%s, audit_id=%s, pdf_size=%d",
        client_email, audit_id, len(signed_pdf or b""),
    )

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
        logger.warning(
            "[EMAIL-TOS-SIGNED] SMTP not configured — skipping signed-TOS email to %s",
            client_email,
        )
        return False

    branding = await _get_branding(db)
    operator_email = smtp.get("smtp_from_email") or ""

    template = jinja_env.get_template("signed_tos_email.html")
    html_body = template.render(
        client_name=client_name,
        audit_id=audit_id,
        **branding,
    )

    msg = MIMEMultipart()
    msg["From"] = f"{smtp['smtp_from_name']} <{smtp['smtp_from_email']}>"
    msg["To"] = client_email
    if operator_email and operator_email.lower() != client_email.lower():
        msg["Bcc"] = operator_email
    cn = branding.get("company_name", "BarnardHQ")
    msg["Subject"] = f"Signed Terms of Service — {audit_id} — {cn}"
    msg.attach(MIMEText(html_body, "html"))

    pdf_attachment = MIMEApplication(signed_pdf, _subtype="pdf")
    pdf_attachment.add_header(
        "Content-Disposition",
        "attachment",
        filename=f"BarnardHQ-ToS-{audit_id}.pdf",
    )
    msg.attach(pdf_attachment)

    try:
        smtp_port = int(smtp["smtp_port"])
    except (ValueError, TypeError):
        raise ValueError(f"Invalid SMTP port: {smtp['smtp_port']}")

    tls_flag = (
        smtp["smtp_use_tls"]
        if isinstance(smtp["smtp_use_tls"], bool)
        else _parse_bool(str(smtp["smtp_use_tls"]), True)
    )
    tls_kwargs = {"use_tls": True} if smtp_port == 465 else {"start_tls": tls_flag}

    # aiosmtplib needs the BCC envelope recipient explicitly — message
    # headers alone don't propagate to the SMTP RCPT TO list.
    recipients = [client_email]
    if operator_email and operator_email.lower() != client_email.lower():
        recipients.append(operator_email)

    logger.info(
        "[EMAIL-TOS-SIGNED] Sending via SMTP host=%s:%s to=%s bcc=%s",
        smtp["smtp_host"], smtp_port, client_email,
        operator_email if len(recipients) > 1 else "(none)",
    )

    try:
        await aiosmtplib.send(
            msg,
            hostname=smtp["smtp_host"],
            port=smtp_port,
            username=smtp["smtp_user"] or None,
            password=smtp["smtp_password"] or None,
            recipients=recipients,
            **tls_kwargs,
        )
        elapsed = _time.perf_counter() - start
        logger.info(
            "[EMAIL-TOS-SIGNED] SUCCESS — Sent to %s (audit_id=%s) in %.3fs",
            client_email, audit_id, elapsed,
        )
        return True
    except Exception as exc:
        elapsed = _time.perf_counter() - start
        logger.error(
            "[EMAIL-TOS-SIGNED] FAILED — SMTP send to %s failed after %.3fs: %s",
            client_email, elapsed, exc, exc_info=True,
        )
        raise


async def send_payment_received_email(
    to_email: str,
    customer_name: str | None,
    mission_title: str,
    invoice_total: float,
    payment_method: str,
    paid_at: object,
    db: AsyncSession | None = None,
) -> bool:
    """Send payment confirmation email to customer after Stripe checkout."""
    import time as _time
    start = _time.perf_counter()

    logger.info(
        "[EMAIL-PAYMENT] Preparing payment receipt to=%s, customer=%s, mission='%s', total=%.2f",
        to_email, customer_name, mission_title, invoice_total,
    )

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
        logger.warning("[EMAIL-PAYMENT] SMTP not configured — skipping payment confirmation email to %s", to_email)
        return False

    branding = await _get_branding(db)

    payment_method_label = {
        "stripe_card": "Credit/Debit Card",
        "stripe_ach": "Bank Transfer (ACH)",
        "manual": "Manual Payment",
    }.get(payment_method, payment_method)

    template = jinja_env.get_template("payment_received_email.html")
    html_body = template.render(
        customer_name=customer_name,
        mission_title=mission_title,
        invoice_total=f"{invoice_total:,.2f}",
        payment_method=payment_method_label,
        paid_at=paid_at.strftime("%B %d, %Y at %I:%M %p") if hasattr(paid_at, "strftime") else str(paid_at),
        **branding,
    )

    msg = MIMEMultipart()
    msg["From"] = f"{smtp['smtp_from_name']} <{smtp['smtp_from_email']}>"
    msg["To"] = to_email
    cn = branding.get("company_name", "DroneOps")
    msg["Subject"] = f"Payment Received — {cn}"
    msg.attach(MIMEText(html_body, "html"))

    try:
        smtp_port = int(smtp["smtp_port"])
    except (ValueError, TypeError):
        raise ValueError(f"Invalid SMTP port: {smtp['smtp_port']}")

    tls_flag = smtp["smtp_use_tls"] if isinstance(smtp["smtp_use_tls"], bool) else _parse_bool(str(smtp["smtp_use_tls"]), True)
    tls_kwargs = {"use_tls": True} if smtp_port == 465 else {"start_tls": tls_flag}

    logger.info("[EMAIL-PAYMENT] Sending via SMTP host=%s:%s to=%s", smtp["smtp_host"], smtp_port, to_email)

    try:
        await aiosmtplib.send(
            msg,
            hostname=smtp["smtp_host"],
            port=smtp_port,
            username=smtp["smtp_user"] or None,
            password=smtp["smtp_password"] or None,
            **tls_kwargs,
        )
        elapsed = _time.perf_counter() - start
        logger.info("[EMAIL-PAYMENT] SUCCESS — Sent to %s in %.3fs", to_email, elapsed)
    except Exception as exc:
        elapsed = _time.perf_counter() - start
        logger.error("[EMAIL-PAYMENT] FAILED — SMTP send to %s failed after %.3fs: %s", to_email, elapsed, exc, exc_info=True)
        raise

    return True
