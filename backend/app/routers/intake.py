import logging
import os
import secrets
from datetime import datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import get_current_user
from app.config import settings
from app.database import get_db
from app.models.customer import Customer
from app.models.user import User
from app.schemas.customer import IntakeFormData, IntakePublicResponse, IntakeTokenResponse

logger = logging.getLogger("droneops.intake")

router = APIRouter(prefix="/api/intake", tags=["intake"])


# --- Admin endpoints (require JWT) ---

@router.post("/initiate", response_model=IntakeTokenResponse)
async def initiate_services(
    data: dict,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Create a new customer stub with just an email and generate an intake token."""
    email = data.get("email", "").strip()
    if not email:
        raise HTTPException(status_code=400, detail="Email address is required")

    # Check if customer with this email already exists
    result = await db.execute(select(Customer).where(Customer.email == email))
    customer = result.scalar_one_or_none()

    if customer:
        # Existing customer — generate new token for them
        logger.info("Generating intake token for existing customer %s (%s)", customer.id, email)
    else:
        # Create new customer stub with just the email
        customer = Customer(name=email.split("@")[0], email=email)
        db.add(customer)
        await db.flush()
        logger.info("Created new customer stub %s for %s", customer.id, email)

    # Generate token
    token = secrets.token_urlsafe(32)
    expires_at = datetime.utcnow() + timedelta(days=settings.intake_token_expire_days)
    customer.intake_token = token
    customer.intake_token_expires_at = expires_at
    await db.flush()

    # Build the intake URL
    frontend_url = settings.frontend_url.rstrip("/")
    intake_url = f"{frontend_url}/intake/{token}"

    logger.info("Intake token generated for customer %s, expires %s", customer.id, expires_at)

    return IntakeTokenResponse(
        intake_token=token,
        intake_url=intake_url,
        expires_at=expires_at,
    )


@router.post("/{customer_id}/send-email")
async def send_intake_email_endpoint(
    customer_id: UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Send the intake form link to an existing customer via email."""
    from app.services.email_service import send_intake_email

    result = await db.execute(select(Customer).where(Customer.id == customer_id))
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    if not customer.email:
        raise HTTPException(status_code=400, detail="Customer has no email address")

    # Generate token if not present or expired
    if not customer.intake_token or (
        customer.intake_token_expires_at and customer.intake_token_expires_at < datetime.utcnow()
    ):
        token = secrets.token_urlsafe(32)
        expires_at = datetime.utcnow() + timedelta(days=settings.intake_token_expire_days)
        customer.intake_token = token
        customer.intake_token_expires_at = expires_at
        await db.flush()

    frontend_url = settings.frontend_url.rstrip("/")
    intake_url = f"{frontend_url}/intake/{customer.intake_token}"

    # Determine if this is a new customer or existing requesting TOS
    is_existing = customer.intake_completed_at is not None or (customer.name and customer.name != customer.email.split("@")[0])

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
        logger.error("Failed to send intake email to %s: %s", customer.email, exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Email send failed: {exc}")

    logger.info("Intake email sent to %s for customer %s", customer.email, customer.id)
    return {"message": "Intake email sent", "intake_url": intake_url}


@router.post("/{customer_id}/upload-tos")
async def upload_tos_pdf(
    customer_id: UUID,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Upload a TOS PDF for a specific customer or as the default."""
    result = await db.execute(select(Customer).where(Customer.id == customer_id))
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    # Save the file
    tos_dir = os.path.join(settings.upload_dir, "tos")
    os.makedirs(tos_dir, exist_ok=True)
    tos_path = os.path.join(tos_dir, f"tos_{customer.id}.pdf")

    content = await file.read()
    with open(tos_path, "wb") as f:
        f.write(content)

    customer.tos_pdf_path = tos_path
    await db.flush()

    logger.info("TOS PDF uploaded for customer %s: %s (%d bytes)", customer.id, tos_path, len(content))
    return {"message": "TOS PDF uploaded", "path": tos_path}


@router.post("/upload-default-tos")
async def upload_default_tos(
    file: UploadFile = File(...),
    _user: User = Depends(get_current_user),
):
    """Upload the default TOS PDF used for all new customers."""
    tos_dir = os.path.join(settings.upload_dir, "tos")
    os.makedirs(tos_dir, exist_ok=True)
    tos_path = os.path.join(tos_dir, "default_tos.pdf")

    content = await file.read()
    with open(tos_path, "wb") as f:
        f.write(content)

    logger.info("Default TOS PDF uploaded: %s (%d bytes)", tos_path, len(content))
    return {"message": "Default TOS PDF uploaded", "path": tos_path}


@router.get("/default-tos-status")
async def default_tos_status(
    _user: User = Depends(get_current_user),
):
    """Check if a default TOS PDF has been uploaded."""
    tos_path = os.path.join(settings.upload_dir, "tos", "default_tos.pdf")
    exists = os.path.exists(tos_path)
    return {"uploaded": exists, "path": tos_path if exists else None}


# --- Public endpoints (no auth required) ---

@router.get("/form/{token}", response_model=IntakePublicResponse)
async def get_intake_form(
    token: str,
    db: AsyncSession = Depends(get_db),
):
    """Public endpoint: Get intake form data for a token."""
    result = await db.execute(select(Customer).where(Customer.intake_token == token))
    customer = result.scalar_one_or_none()

    if not customer:
        raise HTTPException(status_code=404, detail="Invalid or expired link")

    if customer.intake_token_expires_at and customer.intake_token_expires_at < datetime.utcnow():
        raise HTTPException(status_code=410, detail="This link has expired. Please contact BarnardHQ for a new one.")

    # Determine TOS PDF URL
    tos_pdf_url = None
    if customer.tos_pdf_path and os.path.exists(customer.tos_pdf_path):
        tos_pdf_url = f"/api/intake/tos-pdf/{token}"
    else:
        default_path = os.path.join(settings.upload_dir, "tos", "default_tos.pdf")
        if os.path.exists(default_path):
            tos_pdf_url = f"/api/intake/tos-pdf/{token}"

    return IntakePublicResponse(
        customer_name=customer.name if customer.name != (customer.email or "").split("@")[0] else None,
        customer_email=customer.email,
        customer_phone=customer.phone,
        customer_address=customer.address,
        customer_company=customer.company,
        tos_pdf_url=tos_pdf_url,
        already_completed=customer.intake_completed_at is not None,
    )


@router.get("/tos-pdf/{token}")
async def serve_tos_pdf(
    token: str,
    db: AsyncSession = Depends(get_db),
):
    """Public endpoint: Serve the TOS PDF for a given token."""
    from fastapi.responses import FileResponse

    result = await db.execute(select(Customer).where(Customer.intake_token == token))
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(status_code=404, detail="Invalid link")

    # Try customer-specific TOS first, then default
    tos_path = None
    if customer.tos_pdf_path and os.path.exists(customer.tos_pdf_path):
        tos_path = customer.tos_pdf_path
    else:
        default_path = os.path.join(settings.upload_dir, "tos", "default_tos.pdf")
        if os.path.exists(default_path):
            tos_path = default_path

    if not tos_path:
        raise HTTPException(status_code=404, detail="Terms of Service document not available")

    return FileResponse(tos_path, media_type="application/pdf", filename="BarnardHQ_Terms_of_Service.pdf")


@router.post("/form/{token}")
async def submit_intake_form(
    token: str,
    data: IntakeFormData,
    db: AsyncSession = Depends(get_db),
):
    """Public endpoint: Submit the intake form."""
    result = await db.execute(select(Customer).where(Customer.intake_token == token))
    customer = result.scalar_one_or_none()

    if not customer:
        raise HTTPException(status_code=404, detail="Invalid or expired link")

    if customer.intake_token_expires_at and customer.intake_token_expires_at < datetime.utcnow():
        raise HTTPException(status_code=410, detail="This link has expired. Please contact BarnardHQ for a new one.")

    if not data.tos_accepted:
        raise HTTPException(status_code=400, detail="You must accept the Terms of Service to proceed")

    if not data.signature_data or len(data.signature_data) < 100:
        raise HTTPException(status_code=400, detail="A valid signature is required")

    # Update customer with all form data
    customer.name = data.name
    customer.email = data.email
    customer.phone = data.phone
    customer.address = data.address
    customer.company = data.company
    customer.tos_signed = True
    customer.tos_signed_at = datetime.utcnow()
    customer.signature_data = data.signature_data
    customer.intake_completed_at = datetime.utcnow()

    await db.flush()

    logger.info("Intake form submitted for customer %s (%s) — TOS signed", customer.id, customer.email)
    return {"message": "Thank you! Your information has been submitted successfully."}
