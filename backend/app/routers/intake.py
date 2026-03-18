import base64
import io
import logging
import os
import secrets
import time
from datetime import datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Request
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import get_current_user
from app.config import settings
from app.database import get_db
from app.models.customer import Customer
from app.models.user import User
from app.schemas.customer import IntakeFormData, IntakePublicResponse, IntakeTokenResponse

logger = logging.getLogger("doc.intake")

router = APIRouter(prefix="/api/intake", tags=["intake"])
limiter = Limiter(key_func=get_remote_address)


def _client_ip(request: Request) -> str:
    """Extract client IP from request, respecting X-Forwarded-For for reverse proxies."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# --- Admin endpoints (require JWT) ---

@router.post("/initiate", response_model=IntakeTokenResponse)
async def initiate_services(
    data: dict,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Create a new customer stub with just an email and generate an intake token."""
    start = time.perf_counter()
    client_ip = _client_ip(request)
    email = data.get("email", "").strip()

    logger.info("[INTAKE-INIT] Admin initiated services for email=%s from ip=%s", email, client_ip)

    if not email:
        logger.warning("[INTAKE-INIT] Rejected — empty email from ip=%s", client_ip)
        raise HTTPException(status_code=400, detail="Email address is required")

    # Check if customer with this email already exists
    result = await db.execute(select(Customer).where(Customer.email == email))
    customer = result.scalar_one_or_none()

    if customer:
        logger.info("[INTAKE-INIT] Found existing customer id=%s for email=%s", customer.id, email)
        if customer.intake_completed_at:
            logger.info("[INTAKE-INIT] Customer %s already completed intake on %s — generating new token anyway", customer.id, customer.intake_completed_at)
    else:
        customer = Customer(name=email.split("@")[0], email=email)
        db.add(customer)
        await db.flush()
        logger.info("[INTAKE-INIT] Created new customer stub id=%s for email=%s", customer.id, email)

    # Reuse existing valid token if present and not expired
    if (
        customer.intake_token
        and customer.intake_token_expires_at
        and customer.intake_token_expires_at > datetime.utcnow()
        and not customer.intake_completed_at
    ):
        token = customer.intake_token
        expires_at = customer.intake_token_expires_at
        logger.info("[INTAKE-INIT] Reusing existing valid token for customer %s, expires=%s", customer.id, expires_at)
    else:
        token = secrets.token_urlsafe(32)
        expires_at = datetime.utcnow() + timedelta(days=settings.intake_token_expire_days)
        customer.intake_token = token
        customer.intake_token_expires_at = expires_at
        if customer.intake_completed_at:
            customer.intake_completed_at = None  # reset for re-enrollment
        await db.flush()
        logger.info("[INTAKE-INIT] New token generated for customer %s, expires=%s", customer.id, expires_at)

    # Build the intake URL
    frontend_url = settings.frontend_url.rstrip("/")
    intake_url = f"{frontend_url}/intake/{token}"

    elapsed = time.perf_counter() - start
    logger.info("[INTAKE-INIT] Token ready for customer %s, url=%s (%.3fs)", customer.id, intake_url, elapsed)

    return IntakeTokenResponse(
        intake_token=token,
        intake_url=intake_url,
        expires_at=expires_at,
    )


@router.post("/{customer_id}/send-email")
async def send_intake_email_endpoint(
    customer_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Send the intake form link to an existing customer via email."""
    start = time.perf_counter()
    client_ip = _client_ip(request)
    from app.services.email_service import send_intake_email

    logger.info("[INTAKE-EMAIL] Admin requesting intake email for customer_id=%s from ip=%s", customer_id, client_ip)

    result = await db.execute(select(Customer).where(Customer.id == customer_id))
    customer = result.scalar_one_or_none()
    if not customer:
        logger.warning("[INTAKE-EMAIL] Customer not found: %s", customer_id)
        raise HTTPException(status_code=404, detail="Customer not found")
    if not customer.email:
        logger.warning("[INTAKE-EMAIL] Customer %s has no email address", customer_id)
        raise HTTPException(status_code=400, detail="Customer has no email address")

    # Generate token if not present or expired
    token_regenerated = False
    if not customer.intake_token or (
        customer.intake_token_expires_at and customer.intake_token_expires_at < datetime.utcnow()
    ):
        token = secrets.token_urlsafe(32)
        expires_at = datetime.utcnow() + timedelta(days=settings.intake_token_expire_days)
        customer.intake_token = token
        customer.intake_token_expires_at = expires_at
        await db.flush()
        token_regenerated = True
        logger.info("[INTAKE-EMAIL] Generated new token for customer %s (previous expired or missing), expires=%s", customer.id, expires_at)
    else:
        logger.info("[INTAKE-EMAIL] Using existing token for customer %s, expires=%s", customer.id, customer.intake_token_expires_at)

    frontend_url = settings.frontend_url.rstrip("/")
    intake_url = f"{frontend_url}/intake/{customer.intake_token}"

    # Determine if this is a new customer or existing requesting TOS
    is_existing = customer.intake_completed_at is not None or (customer.name and customer.name != customer.email.split("@")[0])
    logger.info("[INTAKE-EMAIL] Sending to %s (existing_customer=%s, token_regenerated=%s)", customer.email, is_existing, token_regenerated)

    try:
        await send_intake_email(
            to_email=customer.email,
            customer_name=customer.name if is_existing else None,
            intake_url=intake_url,
            expires_at=customer.intake_token_expires_at,
            is_existing_customer=is_existing,
            db=db,
        )
    except Exception as exc:
        elapsed = time.perf_counter() - start
        logger.error("[INTAKE-EMAIL] FAILED to send to %s for customer %s: %s (%.3fs)", customer.email, customer.id, exc, elapsed, exc_info=True)
        raise HTTPException(status_code=500, detail="Email delivery failed")

    elapsed = time.perf_counter() - start
    logger.info("[INTAKE-EMAIL] Successfully sent to %s for customer %s (%.3fs)", customer.email, customer.id, elapsed)
    return {"message": "Intake email sent", "intake_url": intake_url}


@router.post("/{customer_id}/upload-tos")
async def upload_tos_pdf(
    customer_id: UUID,
    request: Request,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Upload a TOS PDF for a specific customer or as the default."""
    client_ip = _client_ip(request)
    logger.info("[INTAKE-TOS-UPLOAD] Customer-specific TOS upload for customer_id=%s, filename=%s from ip=%s", customer_id, file.filename, client_ip)

    result = await db.execute(select(Customer).where(Customer.id == customer_id))
    customer = result.scalar_one_or_none()
    if not customer:
        logger.warning("[INTAKE-TOS-UPLOAD] Customer not found: %s", customer_id)
        raise HTTPException(status_code=404, detail="Customer not found")

    # Validate file
    content = await file.read()
    if len(content) > 10_000_000:
        raise HTTPException(status_code=413, detail="File too large (10MB max)")
    if not content[:5].startswith(b"%PDF"):
        raise HTTPException(status_code=400, detail="Invalid PDF file")

    # Save the file
    tos_dir = os.path.join(settings.upload_dir, "tos")
    os.makedirs(tos_dir, exist_ok=True)
    tos_path = os.path.join(tos_dir, f"tos_{customer.id}.pdf")

    with open(tos_path, "wb") as f:
        f.write(content)

    customer.tos_pdf_path = tos_path
    await db.flush()

    logger.info("[INTAKE-TOS-UPLOAD] Saved customer TOS for %s: %s (%d bytes, original=%s)", customer.id, tos_path, len(content), file.filename)
    return {"message": "TOS PDF uploaded", "path": tos_path}


@router.post("/upload-default-tos")
async def upload_default_tos(
    request: Request,
    file: UploadFile = File(...),
    _user: User = Depends(get_current_user),
):
    """Upload the default TOS PDF used for all new customers."""
    client_ip = _client_ip(request)
    logger.info("[INTAKE-TOS-DEFAULT] Default TOS upload, filename=%s from ip=%s", file.filename, client_ip)

    content = await file.read()
    if len(content) > 10_000_000:
        raise HTTPException(status_code=413, detail="File too large (10MB max)")
    if not content[:5].startswith(b"%PDF"):
        raise HTTPException(status_code=400, detail="Invalid PDF file")

    tos_dir = os.path.join(settings.upload_dir, "tos")
    os.makedirs(tos_dir, exist_ok=True)
    tos_path = os.path.join(tos_dir, "default_tos.pdf")

    with open(tos_path, "wb") as f:
        f.write(content)

    logger.info("[INTAKE-TOS-DEFAULT] Saved default TOS: %s (%d bytes, original=%s)", tos_path, len(content), file.filename)
    return {"message": "Default TOS PDF uploaded", "path": tos_path}


@router.get("/default-tos-status")
async def default_tos_status(
    _user: User = Depends(get_current_user),
):
    """Check if a default TOS PDF has been uploaded."""
    tos_path = os.path.join(settings.upload_dir, "tos", "default_tos.pdf")
    exists = os.path.exists(tos_path)
    logger.debug("[INTAKE-TOS-STATUS] Default TOS check: exists=%s path=%s", exists, tos_path)
    return {"uploaded": exists, "path": tos_path if exists else None}


# --- Public endpoints (no auth required) ---

@router.get("/form/{token}", response_model=IntakePublicResponse)
@limiter.limit("30/minute")
async def get_intake_form(
    token: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Public endpoint: Get intake form data for a token."""
    start = time.perf_counter()
    client_ip = _client_ip(request)
    token_preview = token[:8] + "..." if len(token) > 8 else token

    logger.info("[INTAKE-FORM-GET] Form requested for token=%s from ip=%s user_agent=%s", token_preview, client_ip, request.headers.get("user-agent", "unknown")[:100])

    result = await db.execute(select(Customer).where(Customer.intake_token == token))
    customer = result.scalar_one_or_none()

    if not customer:
        logger.warning("[INTAKE-FORM-GET] INVALID TOKEN from ip=%s token=%s — possible brute force or stale link", client_ip, token_preview)
        raise HTTPException(status_code=404, detail="Invalid or expired link")

    if customer.intake_token_expires_at and customer.intake_token_expires_at < datetime.utcnow():
        hours_expired = (datetime.utcnow() - customer.intake_token_expires_at).total_seconds() / 3600
        logger.warning("[INTAKE-FORM-GET] EXPIRED TOKEN for customer %s from ip=%s — expired %.1f hours ago", customer.id, client_ip, hours_expired)
        raise HTTPException(status_code=410, detail="This link has expired. Please contact us for a new one.")

    if customer.intake_completed_at:
        logger.info("[INTAKE-FORM-GET] Already completed form accessed for customer %s from ip=%s (completed %s)", customer.id, client_ip, customer.intake_completed_at)

    # Determine TOS PDF URL
    tos_pdf_url = None
    if customer.tos_pdf_path and os.path.exists(customer.tos_pdf_path):
        tos_pdf_url = f"/api/intake/tos-pdf/{token}"
        logger.debug("[INTAKE-FORM-GET] Using customer-specific TOS for %s", customer.id)
    else:
        default_path = os.path.join(settings.upload_dir, "tos", "default_tos.pdf")
        if os.path.exists(default_path):
            tos_pdf_url = f"/api/intake/tos-pdf/{token}"
            logger.debug("[INTAKE-FORM-GET] Using default TOS for customer %s", customer.id)
        else:
            logger.warning("[INTAKE-FORM-GET] No TOS PDF available for customer %s — neither customer-specific nor default found", customer.id)

    elapsed = time.perf_counter() - start
    logger.info("[INTAKE-FORM-GET] Served form for customer %s (%s), tos_available=%s, already_completed=%s (%.3fs)", customer.id, customer.email, tos_pdf_url is not None, customer.intake_completed_at is not None, elapsed)

    return IntakePublicResponse(
        customer_name=customer.name if customer.name != (customer.email or "").split("@")[0] else None,
        customer_email=customer.email,
        customer_phone=customer.phone,
        customer_address=customer.address,
        customer_city=customer.city,
        customer_state=customer.state,
        customer_zip_code=customer.zip_code,
        customer_company=customer.company,
        tos_pdf_url=tos_pdf_url,
        already_completed=customer.intake_completed_at is not None,
    )


@router.get("/tos-pdf/{token}")
async def serve_tos_pdf(
    token: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Public endpoint: Serve the TOS PDF for a given token."""
    from fastapi.responses import FileResponse

    client_ip = _client_ip(request)
    token_preview = token[:8] + "..." if len(token) > 8 else token

    logger.info("[INTAKE-TOS-SERVE] TOS PDF requested for token=%s from ip=%s", token_preview, client_ip)

    result = await db.execute(select(Customer).where(Customer.intake_token == token))
    customer = result.scalar_one_or_none()
    if not customer:
        logger.warning("[INTAKE-TOS-SERVE] INVALID TOKEN for TOS PDF from ip=%s token=%s", client_ip, token_preview)
        raise HTTPException(status_code=404, detail="Invalid link")

    # Try customer-specific TOS first, then default
    tos_path = None
    if customer.tos_pdf_path and os.path.exists(customer.tos_pdf_path):
        tos_path = customer.tos_pdf_path
        logger.info("[INTAKE-TOS-SERVE] Serving customer-specific TOS for %s: %s", customer.id, tos_path)
    else:
        default_path = os.path.join(settings.upload_dir, "tos", "default_tos.pdf")
        if os.path.exists(default_path):
            tos_path = default_path
            logger.info("[INTAKE-TOS-SERVE] Serving default TOS for customer %s", customer.id)

    if not tos_path:
        logger.error("[INTAKE-TOS-SERVE] No TOS PDF found for customer %s — file missing from disk", customer.id)
        raise HTTPException(status_code=404, detail="Terms of Service document not available")

    return FileResponse(tos_path, media_type="application/pdf", filename="Terms_of_Service.pdf")


@router.post("/form/{token}")
@limiter.limit("5/minute")
async def submit_intake_form(
    token: str,
    data: IntakeFormData,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Public endpoint: Submit the intake form."""
    start = time.perf_counter()
    client_ip = _client_ip(request)
    token_preview = token[:8] + "..." if len(token) > 8 else token

    logger.info("[INTAKE-SUBMIT] Form submission from ip=%s for token=%s, name=%s, email=%s", client_ip, token_preview, data.name, data.email)

    result = await db.execute(select(Customer).where(Customer.intake_token == token))
    customer = result.scalar_one_or_none()

    if not customer:
        logger.warning("[INTAKE-SUBMIT] INVALID TOKEN submission from ip=%s token=%s — possible attack", client_ip, token_preview)
        raise HTTPException(status_code=404, detail="Invalid or expired link")

    if customer.intake_token_expires_at and customer.intake_token_expires_at < datetime.utcnow():
        hours_expired = (datetime.utcnow() - customer.intake_token_expires_at).total_seconds() / 3600
        logger.warning("[INTAKE-SUBMIT] EXPIRED TOKEN submission from ip=%s for customer %s — expired %.1f hours ago", client_ip, customer.id, hours_expired)
        raise HTTPException(status_code=410, detail="This link has expired. Please contact us for a new one.")

    if customer.intake_completed_at:
        logger.warning("[INTAKE-SUBMIT] DUPLICATE submission attempt from ip=%s for customer %s — already completed on %s", client_ip, customer.id, customer.intake_completed_at)

    if not data.tos_accepted:
        logger.warning("[INTAKE-SUBMIT] TOS not accepted from ip=%s for customer %s", client_ip, customer.id)
        raise HTTPException(status_code=400, detail="You must accept the Terms of Service to proceed")

    if not data.signature_data or len(data.signature_data) < 100:
        logger.warning("[INTAKE-SUBMIT] Invalid signature from ip=%s for customer %s (length=%d)", client_ip, customer.id, len(data.signature_data) if data.signature_data else 0)
        raise HTTPException(status_code=400, detail="A valid signature is required")

    if len(data.signature_data) > 500_000:
        logger.warning("[INTAKE-SUBMIT] Signature too large from ip=%s for customer %s (length=%d)", client_ip, customer.id, len(data.signature_data))
        raise HTTPException(status_code=400, detail="Signature data exceeds maximum allowed size")

    # Log data changes for audit trail
    changes = []
    if customer.name != data.name:
        changes.append(f"name: '{customer.name}' -> '{data.name}'")
    if customer.email != data.email:
        changes.append(f"email: '{customer.email}' -> '{data.email}'")
    if customer.phone != data.phone:
        changes.append(f"phone updated")
    if customer.address != data.address:
        changes.append(f"address updated")
    if customer.city != data.city:
        changes.append(f"city: '{customer.city}' -> '{data.city}'")
    if customer.state != data.state:
        changes.append(f"state: '{customer.state}' -> '{data.state}'")
    if customer.zip_code != data.zip_code:
        changes.append(f"zip_code: '{customer.zip_code}' -> '{data.zip_code}'")
    if customer.company != data.company:
        changes.append(f"company: '{customer.company}' -> '{data.company}'")

    # Update customer with all form data
    customer.name = data.name
    customer.email = data.email
    customer.phone = data.phone
    customer.address = data.address
    customer.city = data.city
    customer.state = data.state
    customer.zip_code = data.zip_code
    customer.company = data.company
    customer.tos_signed = True
    customer.tos_signed_at = datetime.utcnow()
    customer.signature_data = data.signature_data
    customer.intake_completed_at = datetime.utcnow()

    await db.flush()

    elapsed = time.perf_counter() - start
    logger.info("[INTAKE-SUBMIT] SUCCESS — Customer %s (%s) completed intake from ip=%s — TOS signed, signature_length=%d, changes=[%s] (%.3fs)",
                customer.id, customer.email, client_ip, len(data.signature_data), ", ".join(changes) if changes else "none", elapsed)
    return {"message": "Thank you! Your information has been submitted successfully."}


@router.get("/{customer_id}/signed-tos")
async def get_signed_tos(
    customer_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Admin endpoint: Serve the TOS PDF with the customer's signature composited onto it."""
    from fastapi.responses import StreamingResponse
    from PIL import Image
    from pypdf import PdfReader, PdfWriter
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    client_ip = _client_ip(request)
    logger.info("[SIGNED-TOS] Requested for customer_id=%s from ip=%s", customer_id, client_ip)

    result = await db.execute(select(Customer).where(Customer.id == customer_id))
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    if not customer.tos_signed or not customer.signature_data:
        raise HTTPException(status_code=404, detail="Customer has not signed the TOS")

    # Find the TOS PDF
    tos_path = None
    if customer.tos_pdf_path and os.path.exists(customer.tos_pdf_path):
        tos_path = customer.tos_pdf_path
    else:
        default_path = os.path.join(settings.upload_dir, "tos", "default_tos.pdf")
        if os.path.exists(default_path):
            tos_path = default_path

    if not tos_path:
        raise HTTPException(status_code=404, detail="TOS PDF not found on disk")

    # Decode the signature from base64 data URL
    sig_data = customer.signature_data
    if "," in sig_data:
        sig_data = sig_data.split(",", 1)[1]
    sig_bytes = base64.b64decode(sig_data)
    sig_image = Image.open(io.BytesIO(sig_bytes)).convert("RGBA")

    # Crop transparent padding from signature
    bbox = sig_image.getbbox()
    if bbox:
        sig_image = sig_image.crop(bbox)

    # Read the original TOS PDF
    reader = PdfReader(tos_path)
    writer = PdfWriter()

    # Copy all pages, adding signature overlay to the last page
    last_idx = len(reader.pages) - 1
    for i, page in enumerate(reader.pages):
        if i == last_idx:
            # Get page dimensions
            media_box = page.mediabox
            page_width = float(media_box.width)
            page_height = float(media_box.height)

            # Create the signature overlay with reportlab
            overlay_buf = io.BytesIO()
            c = canvas.Canvas(overlay_buf, pagesize=(page_width, page_height))

            # Scale signature to fit nicely (max 200pt wide, proportional height)
            sig_w, sig_h = sig_image.size
            max_sig_width = 200
            scale = min(max_sig_width / sig_w, 60 / sig_h)
            draw_w = sig_w * scale
            draw_h = sig_h * scale

            # Position: bottom-right area, above the bottom margin
            sig_x = page_width - draw_w - 72  # 1 inch from right
            sig_y = 72  # 1 inch from bottom

            # Draw a signature line
            c.setStrokeColorRGB(0.5, 0.5, 0.5)
            c.setLineWidth(0.5)
            line_x = sig_x - 10
            line_w = draw_w + 20
            c.line(line_x, sig_y - 2, line_x + line_w, sig_y - 2)

            # Draw the signature image
            sig_buf = io.BytesIO()
            sig_image.save(sig_buf, format="PNG")
            sig_buf.seek(0)
            c.drawImage(ImageReader(sig_buf), sig_x, sig_y, width=draw_w, height=draw_h, mask='auto')

            # Add signed date text below the line
            c.setFont("Helvetica", 8)
            c.setFillColorRGB(0.4, 0.4, 0.4)
            signed_at = customer.tos_signed_at.strftime("%B %d, %Y at %I:%M %p") if customer.tos_signed_at else "Date unknown"
            c.drawString(line_x, sig_y - 14, f"Digitally signed by {customer.name or customer.email} — {signed_at}")

            c.save()
            overlay_buf.seek(0)

            # Merge the overlay onto the last page
            overlay_reader = PdfReader(overlay_buf)
            page.merge_page(overlay_reader.pages[0])

        writer.add_page(page)

    # Write the final PDF to a buffer
    output_buf = io.BytesIO()
    writer.write(output_buf)
    output_buf.seek(0)

    filename = f"TOS_Signed_{(customer.name or 'customer').replace(' ', '_')}.pdf"
    logger.info("[SIGNED-TOS] Serving signed TOS for customer %s (%s)", customer.id, customer.email)

    return StreamingResponse(
        output_buf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )
