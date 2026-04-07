"""API endpoints for managing system settings (SMTP, etc.) from the admin portal."""

import asyncio
import io
import logging
import os
import uuid as uuid_mod

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from PIL import Image as PILImage
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import get_current_user
from app.config import settings as app_settings
from app.database import get_db
from app.models.system_settings import SystemSetting
from app.models.user import User

logger = logging.getLogger("doc.settings")

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

WEATHER_KEYS = [
    "weather_lat",
    "weather_lon",
    "weather_label",
    "weather_airport_icao",
]

BRANDING_KEYS = [
    "company_name",
    "company_tagline",
    "company_website",
    "company_social_url",
    "company_contact_email",
    "company_logo",
    "brand_primary_color",
    "brand_accent_color",
]

# Defaults used when no branding is configured
BRANDING_DEFAULTS = {
    "company_name": "DroneOps",
    "company_tagline": "Professional Aerial Operations",
    "company_website": "",
    "company_social_url": "",
    "company_contact_email": "",
    "company_logo": "",
    "brand_primary_color": "#050608",
    "brand_accent_color": "#00d4ff",
}


OPENSKY_KEYS = [
    "opensky_client_id",
    "opensky_client_secret",
]


class OpenSkySettings(BaseModel):
    opensky_client_id: str = ""
    opensky_client_secret: str = ""


class OpenDroneLogSettings(BaseModel):
    opendronelog_url: str = ""


class DjiSettings(BaseModel):
    dji_api_key: str = ""


class PaymentSettings(BaseModel):
    paypal_link: str = ""
    venmo_link: str = ""


class WeatherLocationSettings(BaseModel):
    weather_lat: str = ""
    weather_lon: str = ""
    weather_label: str = ""
    weather_airport_icao: str = ""


async def get_branding(db: AsyncSession) -> dict:
    """Load branding settings from DB with defaults. Used by templates and services."""
    result = await db.execute(
        select(SystemSetting).where(SystemSetting.key.in_(BRANDING_KEYS))
    )
    rows = {r.key: r.value for r in result.scalars().all()}
    return {key: rows.get(key, "") or BRANDING_DEFAULTS.get(key, "") for key in BRANDING_KEYS}


class BrandingSettings(BaseModel):
    company_name: str = ""
    company_tagline: str = ""
    company_website: str = ""
    company_social_url: str = ""
    company_contact_email: str = ""
    brand_primary_color: str = "#050608"
    brand_accent_color: str = "#00d4ff"


class SmtpSettings(BaseModel):
    smtp_host: str = ""
    smtp_port: str = "587"
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from_email: str = ""
    smtp_from_name: str = ""
    smtp_use_tls: str = "true"


@router.get("/branding")
async def get_branding_settings(
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get branding settings (authenticated)."""
    result = await db.execute(
        select(SystemSetting).where(SystemSetting.key.in_(BRANDING_KEYS))
    )
    rows = {r.key: r.value for r in result.scalars().all()}
    return {key: rows.get(key, BRANDING_DEFAULTS.get(key, "")) for key in BRANDING_KEYS}


@router.put("/branding")
async def update_branding_settings(
    payload: BrandingSettings,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update branding settings."""
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


MAX_LOGO_SIZE = 5_000_000  # 5 MB
ALLOWED_LOGO_TYPES = {"image/jpeg", "image/png", "image/webp", "image/svg+xml"}


@router.post("/branding/logo")
async def upload_company_logo(
    file: UploadFile = File(...),
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Upload a company logo image for PDF reports and emails."""
    content = await file.read()
    if len(content) > MAX_LOGO_SIZE:
        raise HTTPException(status_code=413, detail="Logo too large (5MB max)")
    if file.content_type and file.content_type not in ALLOWED_LOGO_TYPES:
        raise HTTPException(status_code=400, detail="Only JPEG, PNG, WebP, and SVG images are allowed")

    # Delete old logo if one exists
    result = await db.execute(
        select(SystemSetting).where(SystemSetting.key == "company_logo")
    )
    existing = result.scalar_one_or_none()
    if existing and existing.value:
        old_path = os.path.join(app_settings.upload_dir, existing.value)
        try:
            os.remove(old_path)
        except OSError:
            pass

    # Resize non-SVG logos to max 400px width for PDFs
    ext = os.path.splitext(file.filename)[1].lower() if file.filename else ".png"
    if file.content_type != "image/svg+xml":
        try:
            img = PILImage.open(io.BytesIO(content))
            if img.width > 400:
                ratio = 400 / img.width
                img = img.resize((400, int(img.height * ratio)), PILImage.LANCZOS)
            buf = io.BytesIO()
            if img.mode in ("RGBA", "P") and ext not in (".png", ".webp"):
                img = img.convert("RGB")
            fmt = "PNG" if ext == ".png" else "WEBP" if ext == ".webp" else "JPEG"
            img.save(buf, format=fmt, quality=90, optimize=True)
            content = buf.getvalue()
        except Exception as exc:
            logger.warning("Logo resize failed, using original: %s", exc)

    # Save to uploads/branding/
    logo_dir = os.path.join(app_settings.upload_dir, "branding")
    os.makedirs(logo_dir, exist_ok=True)
    filename = f"logo_{uuid_mod.uuid4()}{ext}"
    file_path = os.path.join(logo_dir, filename)
    with open(file_path, "wb") as f:
        f.write(content)

    # Store relative path in DB
    relative_path = os.path.join("branding", filename)
    if existing:
        existing.value = relative_path
    else:
        db.add(SystemSetting(key="company_logo", value=relative_path))
    await db.commit()

    logger.info("Company logo uploaded: %s", relative_path)
    return {"company_logo": relative_path}


@router.delete("/branding/logo")
async def delete_company_logo(
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete the company logo."""
    result = await db.execute(
        select(SystemSetting).where(SystemSetting.key == "company_logo")
    )
    existing = result.scalar_one_or_none()
    if existing and existing.value:
        old_path = os.path.join(app_settings.upload_dir, existing.value)
        try:
            os.remove(old_path)
        except OSError:
            pass
        existing.value = ""
        await db.commit()
        logger.info("Company logo deleted")
    return {"status": "ok"}


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
        msg = MIMEText("This is a test email from DroneOpsCommand.")
        msg["From"] = f"{smtp['smtp_from_name']} <{smtp['smtp_from_email']}>"
        msg["To"] = smtp["smtp_from_email"]
        msg["Subject"] = "DroneOpsCommand SMTP Test"

        from app.services.email_service import _parse_bool
        smtp_port = int(smtp["smtp_port"])
        tls_flag = smtp["smtp_use_tls"] if isinstance(smtp["smtp_use_tls"], bool) else _parse_bool(str(smtp["smtp_use_tls"]), True)
        tls_kwargs = {"use_tls": True} if smtp_port == 465 else {"start_tls": tls_flag}

        await aiosmtplib.send(
            msg,
            hostname=smtp["smtp_host"],
            port=smtp_port,
            username=smtp["smtp_user"] or None,
            password=smtp["smtp_password"] or None,
            **tls_kwargs,
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


@router.get("/dji")
async def get_dji_settings(
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get DJI API key (masked)."""
    result = await db.execute(
        select(SystemSetting).where(SystemSetting.key == "dji_api_key")
    )
    row = result.scalar_one_or_none()
    value = row.value if row else ""
    # Mask the key for frontend display
    if value:
        value = value[:4] + "••••••••" + value[-4:] if len(value) > 8 else "••••••••"
    return {"dji_api_key": value}


@router.put("/dji")
async def update_dji_settings(
    payload: DjiSettings,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update DJI API key."""
    value = payload.dji_api_key
    # Don't overwrite with masked value
    if "••••" in value:
        return {"status": "ok"}

    result = await db.execute(
        select(SystemSetting).where(SystemSetting.key == "dji_api_key")
    )
    existing = result.scalar_one_or_none()
    if existing:
        existing.value = value
    else:
        db.add(SystemSetting(key="dji_api_key", value=value))
    await db.commit()
    return {"status": "ok"}


@router.post("/dji/test")
async def test_dji_api_key(
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Test DJI API key end-to-end through the flight-parser service.

    Sends the DB-stored key to flight-parser's /validate-dji-key endpoint,
    which tests it directly against DJI's servers. This validates the full
    chain: Settings UI → DB → backend → flight-parser → DJI API.
    """
    result = await db.execute(
        select(SystemSetting).where(SystemSetting.key == "dji_api_key")
    )
    row = result.scalar_one_or_none()
    api_key = row.value if row else ""

    if not api_key:
        return {"status": "error", "message": "No DJI API key configured — enter your key above and save it first"}

    key_len = len(api_key.strip())
    if key_len < 8:
        return {"status": "error", "message": f"API key too short ({key_len} chars) — check your key"}

    # Validate through flight-parser (end-to-end test)
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                "http://flight-parser:8100/validate-dji-key",
                headers={"X-DJI-Api-Key": api_key.strip()},
            )
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "status": data.get("status", "unknown"),
                    "message": data.get("message", "Validation complete"),
                    "key_source": data.get("key_source"),
                    "dji_api_reachable": data.get("dji_api_reachable"),
                    "parser_online": True,
                }
            else:
                return {"status": "error", "message": f"Flight parser returned {resp.status_code}", "parser_online": True}
    except httpx.ConnectError:
        return {"status": "error", "message": "Flight parser service is not running — rebuild with ./update.sh dev", "parser_online": False}
    except httpx.TimeoutException:
        return {"status": "warning", "message": "Flight parser timed out validating key — DJI servers may be slow", "parser_online": True}
    except Exception as e:
        logger.warning("DJI key test failed: %s", e)
        return {"status": "error", "message": f"Validation error: {str(e)}", "parser_online": False}


@router.get("/opensky")
async def get_opensky_settings(
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get OpenSky Network credentials. Client secret is masked."""
    result = await db.execute(
        select(SystemSetting).where(SystemSetting.key.in_(OPENSKY_KEYS))
    )
    rows = {r.key: r.value for r in result.scalars().all()}

    data = {}
    for key in OPENSKY_KEYS:
        data[key] = rows.get(key, "")
    # Mask the client secret for frontend display
    if data.get("opensky_client_secret"):
        val = data["opensky_client_secret"]
        data["opensky_client_secret"] = val[:4] + "••••••••" + val[-4:] if len(val) > 8 else "••••••••"
    return data


@router.put("/opensky")
async def update_opensky_settings(
    payload: OpenSkySettings,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update OpenSky Network credentials."""
    updates = payload.model_dump()

    for key, value in updates.items():
        # Skip masked client secret — don't overwrite with mask
        if key == "opensky_client_secret" and "••••" in value:
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
    logger.info("OpenSky Network settings updated by user %s", _user.username)
    return {"status": "ok"}


@router.post("/opensky/test")
async def test_opensky_credentials(
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Test OpenSky Network credentials by requesting an OAuth2 token."""
    result = await db.execute(
        select(SystemSetting).where(SystemSetting.key.in_(OPENSKY_KEYS))
    )
    rows = {r.key: r.value for r in result.scalars().all()}
    client_id = rows.get("opensky_client_id", "").strip()
    client_secret = rows.get("opensky_client_secret", "").strip()

    if not client_id or not client_secret:
        return {"status": "error", "message": "OpenSky client ID and secret must both be configured"}

    token_url = (
        "https://auth.opensky-network.org/auth/realms/opensky-network"
        "/protocol/openid-connect/token"
    )

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                token_url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("access_token"):
                    logger.info("OpenSky credential test succeeded for user %s", _user.username)
                    return {"status": "ok", "message": "Credentials are valid — OAuth2 token obtained successfully"}
                else:
                    logger.warning("OpenSky returned 200 but no access_token")
                    return {"status": "error", "message": "Unexpected response — no access token returned"}
            else:
                detail = ""
                try:
                    err = resp.json()
                    detail = err.get("error_description", err.get("error", ""))
                except Exception:
                    detail = resp.text[:200]
                logger.warning("OpenSky credential test failed: %s %s", resp.status_code, detail)
                return {"status": "error", "message": f"Authentication failed ({resp.status_code}): {detail}"}
    except httpx.ConnectError:
        logger.warning("OpenSky credential test — connection refused")
        return {"status": "error", "message": "Could not connect to OpenSky auth server"}
    except httpx.TimeoutException:
        logger.warning("OpenSky credential test — timeout")
        return {"status": "error", "message": "OpenSky auth server timed out"}
    except Exception as e:
        logger.warning("OpenSky credential test error: %s", e)
        return {"status": "error", "message": f"Test failed: {str(e)}"}


LLM_KEYS = [
    "llm_provider",
    "anthropic_api_key",
]


class LlmSettings(BaseModel):
    llm_provider: str = "ollama"
    anthropic_api_key: str = ""


@router.get("/llm")
async def get_llm_settings(
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get LLM provider settings. API key is masked."""
    result = await db.execute(
        select(SystemSetting).where(SystemSetting.key.in_(LLM_KEYS))
    )
    rows = {r.key: r.value for r in result.scalars().all()}

    data = {}
    for key in LLM_KEYS:
        data[key] = rows.get(key, "")
    # Default provider to config value if not set in DB
    if not data["llm_provider"]:
        data["llm_provider"] = app_settings.llm_provider
    # Mask API key for frontend display
    if data.get("anthropic_api_key"):
        val = data["anthropic_api_key"]
        data["anthropic_api_key"] = val[:7] + "••••••••" + val[-4:] if len(val) > 11 else "••••••••"
    return data


@router.put("/llm")
async def update_llm_settings(
    payload: LlmSettings,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update LLM provider settings."""
    updates = payload.model_dump()

    for key, value in updates.items():
        # Skip masked API key — don't overwrite with mask
        if key == "anthropic_api_key" and "••••" in value:
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
    logger.info("LLM settings updated by user %s (provider=%s)", _user.username, updates.get("llm_provider"))
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


@router.get("/weather")
async def get_weather_settings(
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get weather monitoring location settings."""
    result = await db.execute(
        select(SystemSetting).where(SystemSetting.key.in_(WEATHER_KEYS))
    )
    rows = {r.key: r.value for r in result.scalars().all()}
    return {key: rows.get(key, "") for key in WEATHER_KEYS}


@router.put("/weather")
async def update_weather_settings(
    payload: WeatherLocationSettings,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update weather monitoring location."""
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


@router.post("/weather/lookup")
async def lookup_weather_location(
    payload: dict,
    _user: User = Depends(get_current_user),
):
    """Look up coordinates and nearest airport from a zip code or place name."""
    import httpx

    query = payload.get("query", "").strip()
    if not query:
        return {"error": "No query provided"}

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://nominatim.openstreetmap.org/search",
                params={"q": query, "format": "json", "limit": 1, "countrycodes": "us"},
                headers={"User-Agent": "DroneOpsCommand/1.0"},
            )
            resp.raise_for_status()
            results = resp.json()
            if not results:
                return {"error": "Location not found"}

            lat = float(results[0]["lat"])
            lon = float(results[0]["lon"])
            display_name = results[0].get("display_name", query)
            parts = display_name.split(",")
            label = ", ".join(p.strip() for p in parts[:2]) if len(parts) >= 2 else display_name

            # Find nearest airport for METAR/TFR/NOTAM data
            airport_icao = ""
            try:
                airport_resp = await client.get(
                    "https://aviationweather.gov/api/data/stationinfo",
                    params={"bbox": f"{lat-0.5},{lon-0.5},{lat+0.5},{lon+0.5}", "format": "json"},
                )
                if airport_resp.status_code == 200:
                    stations = airport_resp.json()
                    if isinstance(stations, list) and stations:
                        best = min(stations, key=lambda s: (s.get("lat", 0) - lat)**2 + (s.get("lon", 0) - lon)**2)
                        airport_icao = best.get("icaoId", "")
            except Exception:
                pass

            return {
                "lat": f"{lat:.4f}",
                "lon": f"{lon:.4f}",
                "label": label,
                "airport_icao": airport_icao,
            }
    except Exception as exc:
        return {"error": str(exc)}
