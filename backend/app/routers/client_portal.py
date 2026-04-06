"""Client Portal router — customer-facing mission visibility.

Two audiences:
  - Clients: /api/client/* endpoints authenticated via client JWT
  - Operators: /api/missions/{id}/client-link endpoints via operator JWT

Client endpoints NEVER expose operator internals (financials, flight logs,
fleet info, internal notes). Only mission title, type, date, location, status.
"""

import logging
import time
from datetime import datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from jose import JWTError, jwt
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.client_auth import (
    ClientContext,
    create_client_token,
    get_current_client,
    hash_token,
)
from app.auth.jwt import get_current_user, hash_password_async, verify_password_async
from app.config import settings
from app.database import get_db
from app.models.client_portal import ClientAccessToken
from app.models.customer import Customer
from app.models.mission import Mission
from app.models.user import User
from app.schemas.client_portal import (
    ClientLinkCreate,
    ClientLinkResponse,
    ClientLinkSendRequest,
    ClientLoginRequest,
    ClientLoginResponse,
    ClientMissionDetail,
    ClientMissionSummary,
    ClientTokenValidateResponse,
)

logger = logging.getLogger("doc.client_portal")

router = APIRouter(tags=["client_portal"])
limiter = Limiter(key_func=get_remote_address)


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# ═══════════════════════════════════════════════════════════════════════
# CLIENT-FACING ENDPOINTS — /api/client/*
# ═══════════════════════════════════════════════════════════════════════

@router.post("/api/client/auth/validate", response_model=ClientTokenValidateResponse)
@limiter.limit("30/minute")
async def validate_client_token(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Public: validate a client JWT and return context. Token in Authorization header."""
    start = time.perf_counter()
    client_ip = _client_ip(request)

    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        logger.info("[CLIENT-VALIDATE] No bearer token from ip=%s", client_ip)
        return ClientTokenValidateResponse(valid=False)

    token = auth_header.split(" ", 1)[1]

    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
        token_type = payload.get("type")
        customer_id_str = payload.get("sub")

        if token_type != "client_access" or not customer_id_str:
            logger.warning("[CLIENT-VALIDATE] Invalid token type=%s from ip=%s", token_type, client_ip)
            return ClientTokenValidateResponse(valid=False)

        scope = payload.get("scope", [])
        mission_ids = [s.replace("mission:", "") for s in scope if s.startswith("mission:")]
        exp = payload.get("exp")
        expires_at = datetime.utcfromtimestamp(exp) if exp else None

        result = await db.execute(select(Customer).where(Customer.id == UUID(customer_id_str)))
        customer = result.scalar_one_or_none()
        if not customer:
            logger.warning("[CLIENT-VALIDATE] Customer not found: %s from ip=%s", customer_id_str, client_ip)
            return ClientTokenValidateResponse(valid=False)

        elapsed = time.perf_counter() - start
        logger.info(
            "[CLIENT-VALIDATE] Valid token for customer=%s (%s), missions=%d from ip=%s (%.3fs)",
            customer.id, customer.email, len(mission_ids), client_ip, elapsed,
        )
        return ClientTokenValidateResponse(
            valid=True,
            customer_name=customer.name,
            customer_email=customer.email,
            customer_id=str(customer.id),
            mission_ids=mission_ids,
            expires_at=expires_at,
            has_password=customer.portal_password_hash is not None,
        )

    except JWTError as exc:
        elapsed = time.perf_counter() - start
        logger.warning("[CLIENT-VALIDATE] JWT decode failed from ip=%s: %s (%.3fs)", client_ip, exc, elapsed)
        return ClientTokenValidateResponse(valid=False)


@router.post("/api/client/auth/login", response_model=ClientLoginResponse)
@limiter.limit("5/minute")
async def client_login(
    data: ClientLoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Public: password-based login for repeat clients who set a portal password."""
    start = time.perf_counter()
    client_ip = _client_ip(request)

    logger.info("[CLIENT-LOGIN] Attempt for email=%s from ip=%s", data.email, client_ip)

    result = await db.execute(select(Customer).where(Customer.email == data.email))
    customer = result.scalar_one_or_none()

    if not customer or not customer.portal_password_hash:
        elapsed = time.perf_counter() - start
        logger.warning(
            "[CLIENT-LOGIN] FAILED — no customer or no password for email=%s from ip=%s (%.3fs)",
            data.email, client_ip, elapsed,
        )
        raise HTTPException(status_code=401, detail="Invalid email or password")

    valid = await verify_password_async(data.password, customer.portal_password_hash)
    if not valid:
        elapsed = time.perf_counter() - start
        logger.warning("[CLIENT-LOGIN] FAILED — bad password for email=%s from ip=%s (%.3fs)", data.email, client_ip, elapsed)
        raise HTTPException(status_code=401, detail="Invalid email or password")

    # Find all active (non-revoked, non-expired) tokens for this customer to get mission scope
    tokens_result = await db.execute(
        select(ClientAccessToken).where(
            ClientAccessToken.customer_id == customer.id,
            ClientAccessToken.revoked_at.is_(None),
            ClientAccessToken.expires_at > datetime.utcnow(),
        )
    )
    active_tokens = tokens_result.scalars().all()

    # Aggregate all mission IDs across active tokens
    all_mission_ids: set[str] = set()
    for t in active_tokens:
        for mid in (t.mission_scope or []):
            all_mission_ids.add(str(mid))

    if not all_mission_ids:
        elapsed = time.perf_counter() - start
        logger.warning("[CLIENT-LOGIN] No active missions for customer=%s from ip=%s (%.3fs)", customer.id, client_ip, elapsed)
        raise HTTPException(status_code=403, detail="No active portal access. Contact your operator.")

    mission_ids_list = sorted(all_mission_ids)
    access_token = create_client_token(customer.id, mission_ids_list, settings.client_token_expire_days)

    elapsed = time.perf_counter() - start
    logger.info(
        "[CLIENT-LOGIN] SUCCESS for customer=%s (%s), missions=%d from ip=%s (%.3fs)",
        customer.id, customer.email, len(mission_ids_list), client_ip, elapsed,
    )

    exp = datetime.utcnow() + timedelta(days=settings.client_token_expire_days)
    return ClientLoginResponse(
        access_token=access_token,
        customer_name=customer.name,
        mission_ids=mission_ids_list,
        expires_at=exp,
    )


@router.get("/api/client/missions", response_model=list[ClientMissionSummary])
async def list_client_missions(
    request: Request,
    client: ClientContext = Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
):
    """Client auth: list missions within the token's scope."""
    start = time.perf_counter()
    client_ip = _client_ip(request)

    logger.info("[CLIENT-MISSIONS] List requested by customer=%s from ip=%s", client.customer_id, client_ip)

    if not client.mission_ids:
        logger.info("[CLIENT-MISSIONS] Empty scope for customer=%s", client.customer_id)
        return []

    mission_uuids = [UUID(mid) for mid in client.mission_ids]
    result = await db.execute(
        select(Mission).where(Mission.id.in_(mission_uuids)).order_by(Mission.mission_date.desc().nullslast())
    )
    missions = result.scalars().all()

    summaries = [
        ClientMissionSummary(
            id=str(m.id),
            title=m.title,
            mission_type=m.mission_type.value if hasattr(m.mission_type, "value") else str(m.mission_type),
            mission_date=str(m.mission_date) if m.mission_date else None,
            location_name=m.location_name,
            status=m.status.value if hasattr(m.status, "value") else str(m.status),
        )
        for m in missions
    ]

    elapsed = time.perf_counter() - start
    logger.info("[CLIENT-MISSIONS] Returned %d missions for customer=%s (%.3fs)", len(summaries), client.customer_id, elapsed)
    return summaries


@router.get("/api/client/missions/{mission_id}", response_model=ClientMissionDetail)
async def get_client_mission(
    mission_id: UUID,
    request: Request,
    client: ClientContext = Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
):
    """Client auth: get single mission detail (scoped)."""
    start = time.perf_counter()
    client_ip = _client_ip(request)

    logger.info("[CLIENT-MISSION] Detail requested for mission=%s by customer=%s from ip=%s", mission_id, client.customer_id, client_ip)

    if not client.can_access_mission(str(mission_id)):
        logger.warning("[CLIENT-MISSION] ACCESS DENIED — customer=%s cannot access mission=%s from ip=%s", client.customer_id, mission_id, client_ip)
        raise HTTPException(status_code=403, detail="You do not have access to this mission")

    result = await db.execute(select(Mission).where(Mission.id == mission_id))
    mission = result.scalar_one_or_none()

    if not mission:
        logger.warning("[CLIENT-MISSION] Mission not found: %s", mission_id)
        raise HTTPException(status_code=404, detail="Mission not found")

    image_count = len(mission.images) if mission.images else 0

    elapsed = time.perf_counter() - start
    logger.info("[CLIENT-MISSION] Served mission=%s for customer=%s (%.3fs)", mission_id, client.customer_id, elapsed)

    return ClientMissionDetail(
        id=str(mission.id),
        title=mission.title,
        mission_type=mission.mission_type.value if hasattr(mission.mission_type, "value") else str(mission.mission_type),
        description=mission.description,
        mission_date=str(mission.mission_date) if mission.mission_date else None,
        location_name=mission.location_name,
        status=mission.status.value if hasattr(mission.status, "value") else str(mission.status),
        client_notes=mission.client_notes,
        created_at=mission.created_at,
        image_count=image_count,
    )


# ═══════════════════════════════════════════════════════════════════════
# OPERATOR ENDPOINTS — /api/missions/{id}/client-link
# ═══════════════════════════════════════════════════════════════════════

@router.post("/api/missions/{mission_id}/client-link", response_model=ClientLinkResponse)
async def create_client_link(
    mission_id: UUID,
    data: ClientLinkCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Operator auth: generate a client portal token/URL for a mission."""
    start = time.perf_counter()
    client_ip = _client_ip(request)

    logger.info("[CLIENT-LINK-CREATE] Operator creating link for mission=%s, expires_days=%d from ip=%s", mission_id, data.expires_days, client_ip)

    result = await db.execute(select(Mission).where(Mission.id == mission_id))
    mission = result.scalar_one_or_none()
    if not mission:
        logger.warning("[CLIENT-LINK-CREATE] Mission not found: %s", mission_id)
        raise HTTPException(status_code=404, detail="Mission not found")

    if not mission.customer_id:
        logger.warning("[CLIENT-LINK-CREATE] Mission %s has no customer assigned", mission_id)
        raise HTTPException(status_code=400, detail="Mission must have a customer assigned")

    # Create the JWT
    mission_ids = [str(mission.id)]
    client_jwt = create_client_token(mission.customer_id, mission_ids, data.expires_days)

    # Store token record for tracking/revocation
    token_record = ClientAccessToken(
        customer_id=mission.customer_id,
        token_hash=hash_token(client_jwt),
        mission_scope=[str(mission.id)],
        expires_at=datetime.utcnow() + timedelta(days=data.expires_days),
        ip_address=client_ip,
    )
    db.add(token_record)
    await db.flush()

    frontend_url = settings.frontend_url.rstrip("/")
    portal_url = f"{frontend_url}/client/{client_jwt}"

    elapsed = time.perf_counter() - start
    logger.info(
        "[CLIENT-LINK-CREATE] Token created id=%s for mission=%s, customer=%s (%.3fs)",
        token_record.id, mission_id, mission.customer_id, elapsed,
    )

    return ClientLinkResponse(
        token_id=str(token_record.id),
        portal_url=portal_url,
        expires_at=token_record.expires_at,
        customer_id=str(mission.customer_id),
        mission_ids=mission_ids,
    )


@router.post("/api/missions/{mission_id}/client-link/send")
async def send_client_link(
    mission_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Operator auth: email the client portal link to the customer."""
    start = time.perf_counter()
    client_ip = _client_ip(request)

    logger.info("[CLIENT-LINK-SEND] Sending portal link for mission=%s from ip=%s", mission_id, client_ip)

    result = await db.execute(select(Mission).where(Mission.id == mission_id))
    mission = result.scalar_one_or_none()
    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")
    if not mission.customer_id:
        raise HTTPException(status_code=400, detail="Mission has no customer assigned")

    cust_result = await db.execute(select(Customer).where(Customer.id == mission.customer_id))
    customer = cust_result.scalar_one_or_none()
    if not customer or not customer.email:
        raise HTTPException(status_code=400, detail="Customer has no email address")

    # Generate a fresh token
    mission_ids = [str(mission.id)]
    client_jwt = create_client_token(customer.id, mission_ids, settings.client_token_expire_days)
    expires_at = datetime.utcnow() + timedelta(days=settings.client_token_expire_days)

    token_record = ClientAccessToken(
        customer_id=customer.id,
        token_hash=hash_token(client_jwt),
        mission_scope=[str(mission.id)],
        expires_at=expires_at,
        ip_address=client_ip,
    )
    db.add(token_record)
    await db.flush()

    frontend_url = settings.frontend_url.rstrip("/")
    portal_url = f"{frontend_url}/client/{client_jwt}"

    # Send email
    from app.services.email_service import send_client_portal_email

    try:
        await send_client_portal_email(
            to_email=customer.email,
            customer_name=customer.name,
            mission_title=mission.title,
            portal_url=portal_url,
            expires_at=expires_at,
            db=db,
        )
    except Exception as exc:
        elapsed = time.perf_counter() - start
        logger.error(
            "[CLIENT-LINK-SEND] FAILED to send email to %s for mission=%s: %s (%.3fs)",
            customer.email, mission_id, exc, elapsed, exc_info=True,
        )
        raise HTTPException(status_code=500, detail="Email delivery failed")

    elapsed = time.perf_counter() - start
    logger.info(
        "[CLIENT-LINK-SEND] Email sent to %s for mission=%s, token_id=%s (%.3fs)",
        customer.email, mission_id, token_record.id, elapsed,
    )
    return {"message": "Client portal link sent", "portal_url": portal_url}


@router.delete("/api/missions/{mission_id}/client-link/{token_id}")
async def revoke_client_link(
    mission_id: UUID,
    token_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Operator auth: revoke a client portal token."""
    start = time.perf_counter()
    client_ip = _client_ip(request)

    logger.info("[CLIENT-LINK-REVOKE] Revoking token=%s for mission=%s from ip=%s", token_id, mission_id, client_ip)

    result = await db.execute(
        select(ClientAccessToken).where(
            ClientAccessToken.id == token_id,
            ClientAccessToken.mission_scope.contains([str(mission_id)]),
        )
    )
    token_record = result.scalar_one_or_none()

    if not token_record:
        logger.warning("[CLIENT-LINK-REVOKE] Token not found: %s for mission=%s", token_id, mission_id)
        raise HTTPException(status_code=404, detail="Token not found")

    if token_record.revoked_at:
        logger.info("[CLIENT-LINK-REVOKE] Token %s already revoked at %s", token_id, token_record.revoked_at)
        return {"message": "Token already revoked"}

    token_record.revoked_at = datetime.utcnow()
    await db.flush()

    elapsed = time.perf_counter() - start
    logger.info("[CLIENT-LINK-REVOKE] Token %s revoked (%.3fs)", token_id, elapsed)
    return {"message": "Token revoked"}
