"""API endpoints for managing system settings (SMTP, etc.) from the admin portal."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import get_current_user
from app.database import get_db
from app.models.system_settings import SystemSetting
from app.models.user import User

router = APIRouter(prefix="/api/settings", tags=["settings"])

SMTP_KEYS = [
    "smtp_host",
    "smtp_port",
    "smtp_user",
    "smtp_password",
    "smtp_from_email",
    "smtp_from_name",
    "smtp_use_tls",
]


PAYMENT_KEYS = [
    "paypal_link",
    "venmo_link",
]


class OpenDroneLogSettings(BaseModel):
    opendronelog_url: str = ""


class PaymentSettings(BaseModel):
    paypal_link: str = ""
    venmo_link: str = ""


class SmtpSettings(BaseModel):
    smtp_host: str = ""
    smtp_port: str = "587"
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from_email: str = ""
    smtp_from_name: str = ""
    smtp_use_tls: str = "true"


@router.get("/smtp")
async def get_smtp_settings(
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get current SMTP settings. Password is masked."""
    result = await db.execute(
        select(SystemSetting).where(SystemSetting.key.in_(SMTP_KEYS))
    )
    rows = {r.key: r.value for r in result.scalars().all()}

    data = {}
    for key in SMTP_KEYS:
        data[key] = rows.get(key, "")
    # Mask password for frontend display
    if data.get("smtp_password"):
        data["smtp_password"] = "••••••••"
    return data


@router.put("/smtp")
async def update_smtp_settings(
    payload: SmtpSettings,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update SMTP settings in the database."""
    updates = payload.model_dump()

    for key, value in updates.items():
        # Skip masked password — don't overwrite with mask
        if key == "smtp_password" and value == "••••••••":
            continue

        result = await db.execute(
            select(SystemSetting).where(SystemSetting.key == key)
        )
        existing = result.scalar_one_or_none()
        if existing:
            existing.value = value
        else:
            db.add(SystemSetting(key=key, value=value))

    await db.commit()
    return {"status": "ok"}


@router.post("/smtp/test")
async def test_smtp(
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Send a test email using current SMTP settings."""
    from app.services.email_service import get_smtp_settings as load_smtp

    smtp = await load_smtp(db)
    if not smtp["smtp_host"]:
        return {"status": "error", "message": "SMTP host is not configured"}

    import aiosmtplib
    from email.mime.text import MIMEText

    try:
        msg = MIMEText("This is a test email from DroneOpsReport.")
        msg["From"] = f"{smtp['smtp_from_name']} <{smtp['smtp_from_email']}>"
        msg["To"] = smtp["smtp_from_email"]
        msg["Subject"] = "DroneOpsReport SMTP Test"

        await aiosmtplib.send(
            msg,
            hostname=smtp["smtp_host"],
            port=int(smtp["smtp_port"]),
            username=smtp["smtp_user"] or None,
            password=smtp["smtp_password"] or None,
            use_tls=smtp["smtp_use_tls"],
        )
        return {"status": "ok", "message": f"Test email sent to {smtp['smtp_from_email']}"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.get("/opendronelog")
async def get_opendronelog_settings(
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get OpenDroneLog URL."""
    result = await db.execute(
        select(SystemSetting).where(SystemSetting.key == "opendronelog_url")
    )
    row = result.scalar_one_or_none()
    from app.config import settings as app_settings
    return {"opendronelog_url": (row.value if row else "") or app_settings.opendronelog_url}


@router.put("/opendronelog")
async def update_opendronelog_settings(
    payload: OpenDroneLogSettings,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update OpenDroneLog URL."""
    result = await db.execute(
        select(SystemSetting).where(SystemSetting.key == "opendronelog_url")
    )
    existing = result.scalar_one_or_none()
    if existing:
        existing.value = payload.opendronelog_url
    else:
        db.add(SystemSetting(key="opendronelog_url", value=payload.opendronelog_url))
    await db.commit()
    return {"status": "ok"}


@router.get("/payment")
async def get_payment_settings(
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get PayPal and Venmo links."""
    result = await db.execute(
        select(SystemSetting).where(SystemSetting.key.in_(PAYMENT_KEYS))
    )
    rows = {r.key: r.value for r in result.scalars().all()}
    return {key: rows.get(key, "") for key in PAYMENT_KEYS}


@router.put("/payment")
async def update_payment_settings(
    payload: PaymentSettings,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update PayPal and Venmo links."""
    for key, value in payload.model_dump().items():
        result = await db.execute(
            select(SystemSetting).where(SystemSetting.key == key)
        )
        existing = result.scalar_one_or_none()
        if existing:
            existing.value = value
        else:
            db.add(SystemSetting(key=key, value=value))

    await db.commit()
    return {"status": "ok"}
