"""Client Portal router — customer-facing mission visibility.

Two audiences:
  - Clients: /api/client/* endpoints authenticated via client JWT
  - Operators: /api/missions/{id}/client-link endpoints via operator JWT

Client endpoints NEVER expose operator internals (financials, flight logs,
fleet info, internal notes). Only mission title, type, date, location, status.

ADR-0011 (v2.66.0) — pay/deposit + pay/balance reuse a recent unpaid
Stripe Checkout session if one already exists for this invoice. A
customer double-clicking Pay no longer mints two sessions.
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
from sqlalchemy.orm import selectinload

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
from app.models.invoice import (
    Invoice,
    PAYMENT_PHASE_DEPOSIT_DUE,
    PAYMENT_PHASE_BALANCE_DUE,
    PAYMENT_PHASE_PAID_IN_FULL,
)
from app.models.mission import Mission, MissionStatus
from app.models.user import User
from app.schemas.client_portal import (
    ClientInvoiceLineItem,
    ClientInvoiceResponse,
    ClientLinkCreate,
    ClientLinkResponse,
    ClientLinkSendRequest,
    ClientLoginRequest,
    ClientLoginResponse,
    ClientMissionDetail,
    ClientMissionSummary,
    ClientPaymentResponse,
    ClientTokenValidateResponse,
)

logger = logging.getLogger("doc.client_portal")

router = APIRouter(tags=["client_portal"])
limiter = Limiter(key_func=get_remote_address)


# Mission states at which the customer is allowed to see the invoice
# (`get_client_invoice`) and pay it (`create_client_payment`). The
# operator can still create + edit invoices on missions in earlier
# states — this gate only governs the customer-facing surface.
INVOICE_VISIBLE_STATUSES: frozenset[MissionStatus] = frozenset({
    MissionStatus.COMPLETED,
    MissionStatus.SENT,
})


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
# (Definitions appear AFTER the customer-pay endpoints below; the
# duplicate first-set previously here was unreachable — FastAPI keeps
# the LAST registered handler when two share a path. v2.66.0 cleanup.)
# ═══════════════════════════════════════════════════════════════════════


@router.get("/api/client/missions/{mission_id}/invoice", response_model=ClientInvoiceResponse | None)
async def get_client_invoice(
    mission_id: UUID,
    request: Request,
    client: ClientContext = Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
):
    """Client auth: get invoice for a mission (filtered — no operator notes).

    ADR-0008 + ADR-0009 visibility rule:
        invoice is shown when EITHER
          (1) deposit_required AND NOT deposit_paid (deposit phase),
          OR
          (2) mission.status in {COMPLETED, SENT}    (post-delivery).
        hidden only when:
          deposit_required = False AND mission not yet COMPLETED|SENT.

    The invoice payload always carries `payment_phase` so the customer
    UI can render the 4-step phase strip without re-deriving the truth
    table client-side.
    """
    start = time.perf_counter()
    client_ip = _client_ip(request)

    if not client.can_access_mission(str(mission_id)):
        logger.warning("[CLIENT-INVOICE] ACCESS DENIED — customer=%s cannot access mission=%s", client.customer_id, mission_id)
        raise HTTPException(status_code=403, detail="You do not have access to this mission")

    mission_row = await db.execute(select(Mission.status).where(Mission.id == mission_id))
    mission_status = mission_row.scalar_one_or_none()

    result = await db.execute(
        select(Invoice)
        .options(selectinload(Invoice.line_items))
        .where(Invoice.mission_id == mission_id)
    )
    invoice = result.scalar_one_or_none()

    if not invoice:
        elapsed = time.perf_counter() - start
        logger.info("[CLIENT-INVOICE] No invoice for mission=%s (%.3fs)", mission_id, elapsed)
        return None

    deposit_phase_active = bool(invoice.deposit_required) and not bool(invoice.deposit_paid)
    mission_delivered = (
        mission_status is not None and mission_status in INVOICE_VISIBLE_STATUSES
    )

    if not (deposit_phase_active or mission_delivered):
        elapsed = time.perf_counter() - start
        logger.info(
            "[CLIENT-INVOICE] HIDDEN — mission=%s status=%s deposit_required=%s deposit_paid=%s (%.3fs)",
            mission_id,
            mission_status.value if mission_status else "unknown",
            invoice.deposit_required,
            invoice.deposit_paid,
            elapsed,
        )
        return None

    line_items = [
        ClientInvoiceLineItem(
            description=item.description,
            quantity=float(item.quantity),
            unit_price=float(item.unit_price),
            total=float(item.total),
        )
        for item in (invoice.line_items or [])
    ]

    payment_phase = invoice.payment_phase_for(mission_status)

    elapsed = time.perf_counter() - start
    logger.info(
        "[CLIENT-INVOICE] Served invoice=%s for mission=%s, phase=%s, paid=%s, deposit_paid=%s (%.3fs)",
        invoice.id, mission_id, payment_phase, invoice.paid_in_full, invoice.deposit_paid, elapsed,
    )

    return ClientInvoiceResponse(
        id=str(invoice.id),
        total=float(invoice.total),
        paid_in_full=invoice.paid_in_full,
        paid_at=invoice.paid_at,
        payment_method=invoice.payment_method,
        line_items=line_items,
        deposit_required=bool(invoice.deposit_required),
        deposit_amount=float(invoice.deposit_amount or 0),
        deposit_paid=bool(invoice.deposit_paid),
        deposit_paid_at=invoice.deposit_paid_at,
        deposit_payment_method=invoice.deposit_payment_method,
        balance_amount=invoice.balance_amount,
        payment_phase=payment_phase,
    )


async def _load_pay_context(
    *,
    mission_id: UUID,
    client: ClientContext,
    db: AsyncSession,
) -> tuple[Mission, Invoice, Customer]:
    """Common preflight for every /pay/* endpoint.

    Returns (mission, invoice, customer) on success; raises
    HTTPException with the appropriate status otherwise. Centralizing
    this means each pay endpoint contains only the phase-specific gate.
    """
    if not client.can_access_mission(str(mission_id)):
        logger.warning("[CLIENT-PAY] ACCESS DENIED — customer=%s cannot access mission=%s", client.customer_id, mission_id)
        raise HTTPException(status_code=403, detail="You do not have access to this mission")

    mission_result = await db.execute(select(Mission).where(Mission.id == mission_id))
    mission = mission_result.scalar_one_or_none()
    if mission is None:
        raise HTTPException(status_code=404, detail="Mission not found")

    inv_result = await db.execute(
        select(Invoice)
        .options(selectinload(Invoice.line_items))
        .where(Invoice.mission_id == mission_id)
    )
    invoice = inv_result.scalar_one_or_none()
    if not invoice:
        raise HTTPException(status_code=404, detail="No invoice found for this mission")

    if float(invoice.total) <= 0:
        raise HTTPException(status_code=400, detail="Invoice total must be greater than zero")

    cust_result = await db.execute(select(Customer).where(Customer.id == client.customer_id))
    customer = cust_result.scalar_one_or_none()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    return mission, invoice, customer


def _client_redirect_urls(mission_id: UUID) -> tuple[str, str]:
    frontend_url = settings.frontend_url.rstrip("/")
    return (
        f"{frontend_url}/client/missions/{mission_id}?payment=success",
        f"{frontend_url}/client/missions/{mission_id}?payment=cancelled",
    )


# ADR-0011 (v2.66.0) — Pay/deposit + pay/balance idempotency window.
# A double-click within this window returns the existing Stripe session
# URL instead of minting a new one. Stripe Checkout sessions are valid
# for 24h by default; we use 30 min so that a customer who comes back
# after a coffee gets a fresh session (preventing them paying with stale
# pricing if the operator updated line items in between).
_CHECKOUT_REUSE_WINDOW = timedelta(minutes=30)


async def _reuse_existing_checkout_session(
    *,
    session_id: str | None,
    db: AsyncSession,
    log_prefix: str,
) -> str | None:
    """If `session_id` points at a recent, unpaid Stripe Checkout session,
    return its URL so the caller can return that to the client without
    creating a duplicate session. Returns None if no reuse possible.

    Stripe is the authority on payment_status + created timestamp — we
    don't trust local DB freshness signals because the webhook might
    not have arrived yet. Network failure → None (caller mints new).
    """
    if not session_id:
        return None
    try:
        from app.services.stripe_service import get_stripe_settings
        import stripe as _stripe
        cfg = await get_stripe_settings(db)
        secret = cfg.get("stripe_secret_key")
        if not secret:
            return None
        _stripe.api_key = secret
        existing = _stripe.checkout.Session.retrieve(session_id)
    except Exception as exc:
        logger.warning(
            "%s could not retrieve existing session=%s for reuse check: %s",
            log_prefix, session_id, exc,
        )
        return None

    payment_status = getattr(existing, "payment_status", None)
    created_unix = getattr(existing, "created", None)
    url = getattr(existing, "url", None)

    if payment_status == "paid":
        logger.info(
            "%s existing session=%s already paid — not reusing (caller will see paid_in_full)",
            log_prefix, session_id,
        )
        return None
    if not created_unix or not url:
        return None
    age = datetime.utcnow() - datetime.utcfromtimestamp(created_unix)
    if age > _CHECKOUT_REUSE_WINDOW:
        logger.info(
            "%s existing session=%s age=%s exceeds reuse window=%s — minting fresh",
            log_prefix, session_id, age, _CHECKOUT_REUSE_WINDOW,
        )
        return None
    logger.info(
        "%s reusing existing checkout session=%s age=%s status=%s",
        log_prefix, session_id, age, payment_status,
    )
    return url


@router.post("/api/client/missions/{mission_id}/invoice/pay/deposit", response_model=ClientPaymentResponse)
async def create_client_deposit_payment(
    mission_id: UUID,
    request: Request,
    client: ClientContext = Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
):
    """Client auth: pay the deposit (ADR-0009 §3.4). Open any time
    the deposit is required and not yet collected — does not require
    the mission to be completed."""
    start = time.perf_counter()
    client_ip = _client_ip(request)
    logger.info(
        "[CLIENT-PAY-DEPOSIT] Requested mission=%s customer=%s ip=%s",
        mission_id, client.customer_id, client_ip,
    )

    mission, invoice, customer = await _load_pay_context(
        mission_id=mission_id, client=client, db=db,
    )

    if not invoice.deposit_required:
        raise HTTPException(status_code=400, detail="This invoice does not require a deposit")
    if invoice.deposit_paid:
        raise HTTPException(status_code=400, detail="Deposit has already been paid")
    if float(invoice.deposit_amount or 0) <= 0:
        raise HTTPException(status_code=400, detail="Deposit amount must be greater than zero")

    # ADR-0011 (v2.66.0) — return existing recent unpaid session if the
    # customer double-clicks Pay. Avoids minting two Stripe sessions
    # against the same invoice (and the customer accidentally paying both).
    reused = await _reuse_existing_checkout_session(
        session_id=invoice.deposit_checkout_session_id,
        db=db,
        log_prefix="[CLIENT-PAY-DEPOSIT]",
    )
    if reused is not None:
        elapsed = time.perf_counter() - start
        logger.info(
            "[CLIENT-PAY-DEPOSIT] Reused existing session mission=%s (%.3fs)",
            mission_id, elapsed,
        )
        return ClientPaymentResponse(checkout_url=reused)

    success_url, cancel_url = _client_redirect_urls(mission_id)

    from app.services.stripe_service import create_checkout_session
    try:
        checkout_url = await create_checkout_session(
            invoice=invoice,
            customer=customer,
            success_url=success_url,
            cancel_url=cancel_url,
            db=db,
            payment_phase="deposit",
            amount_override=float(invoice.deposit_amount),
            description_override=f"Deposit — {mission.title}"[:200],
        )
    except ValueError as exc:
        logger.error("[CLIENT-PAY-DEPOSIT] Stripe not configured: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))
    except Exception as exc:
        logger.error("[CLIENT-PAY-DEPOSIT] Stripe error mission=%s: %s", mission_id, exc)
        raise HTTPException(status_code=500, detail="Failed to create payment session")

    await db.commit()
    elapsed = time.perf_counter() - start
    logger.info(
        "[CLIENT-PAY-DEPOSIT] Checkout created mission=%s amount=%.2f (%.3fs)",
        mission_id, float(invoice.deposit_amount), elapsed,
    )
    return ClientPaymentResponse(checkout_url=checkout_url)


@router.post("/api/client/missions/{mission_id}/invoice/pay/balance", response_model=ClientPaymentResponse)
async def create_client_balance_payment(
    mission_id: UUID,
    request: Request,
    client: ClientContext = Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
):
    """Client auth: pay the balance (ADR-0009 §3.4).

    Gated: mission must be COMPLETED|SENT, deposit must be paid (or
    not required), and invoice must not already be paid in full.
    """
    start = time.perf_counter()
    client_ip = _client_ip(request)
    logger.info(
        "[CLIENT-PAY-BALANCE] Requested mission=%s customer=%s ip=%s",
        mission_id, client.customer_id, client_ip,
    )

    mission, invoice, customer = await _load_pay_context(
        mission_id=mission_id, client=client, db=db,
    )

    if invoice.paid_in_full:
        raise HTTPException(status_code=400, detail="Invoice is already paid")
    if mission.status not in INVOICE_VISIBLE_STATUSES:
        logger.warning(
            "[CLIENT-PAY-BALANCE] BLOCKED — mission=%s status=%s not in %s",
            mission_id, mission.status.value, sorted(s.value for s in INVOICE_VISIBLE_STATUSES),
        )
        raise HTTPException(
            status_code=400,
            detail="The balance can only be paid after your operator marks the mission complete.",
        )
    if invoice.deposit_required and not invoice.deposit_paid:
        raise HTTPException(
            status_code=400,
            detail="The deposit must be paid before the balance can be charged.",
        )

    balance = invoice.balance_amount
    if balance <= 0:
        # Defensive — if a deposit_amount somehow equals total, treat
        # the invoice as paid in full and refuse to spin up Stripe.
        raise HTTPException(status_code=400, detail="Nothing left to pay on this invoice")

    # ADR-0011 (v2.66.0) — same idempotency window as deposit branch.
    reused = await _reuse_existing_checkout_session(
        session_id=invoice.stripe_checkout_session_id,
        db=db,
        log_prefix="[CLIENT-PAY-BALANCE]",
    )
    if reused is not None:
        elapsed = time.perf_counter() - start
        logger.info(
            "[CLIENT-PAY-BALANCE] Reused existing session mission=%s (%.3fs)",
            mission_id, elapsed,
        )
        return ClientPaymentResponse(checkout_url=reused)

    success_url, cancel_url = _client_redirect_urls(mission_id)

    from app.services.stripe_service import create_checkout_session
    try:
        checkout_url = await create_checkout_session(
            invoice=invoice,
            customer=customer,
            success_url=success_url,
            cancel_url=cancel_url,
            db=db,
            payment_phase="balance",
            amount_override=balance,
            description_override=f"Balance — {mission.title}"[:200],
        )
    except ValueError as exc:
        logger.error("[CLIENT-PAY-BALANCE] Stripe not configured: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))
    except Exception as exc:
        logger.error("[CLIENT-PAY-BALANCE] Stripe error mission=%s: %s", mission_id, exc)
        raise HTTPException(status_code=500, detail="Failed to create payment session")

    await db.commit()
    elapsed = time.perf_counter() - start
    logger.info(
        "[CLIENT-PAY-BALANCE] Checkout created mission=%s amount=%.2f (%.3fs)",
        mission_id, balance, elapsed,
    )
    return ClientPaymentResponse(checkout_url=checkout_url)


@router.post("/api/client/missions/{mission_id}/invoice/pay", response_model=ClientPaymentResponse)
async def create_client_payment(
    mission_id: UUID,
    request: Request,
    client: ClientContext = Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
):
    """Client auth: legacy alias retained for back-compat (ADR-0009 §3.4).

    Infers the appropriate payment phase from current invoice state and
    delegates to the deposit or balance handler. Pre-deposit-feature
    bookmarks and the customer-facing UI from before the cutover keep
    working through this endpoint.
    """
    start = time.perf_counter()
    client_ip = _client_ip(request)
    logger.info(
        "[CLIENT-PAY] Legacy alias hit for mission=%s customer=%s ip=%s",
        mission_id, client.customer_id, client_ip,
    )

    mission, invoice, _customer = await _load_pay_context(
        mission_id=mission_id, client=client, db=db,
    )

    if invoice.paid_in_full:
        raise HTTPException(status_code=400, detail="Invoice is already paid")

    # Phase inference matches compute_payment_phase but with the alias
    # restriction: we reject "awaiting_completion" because there's
    # nothing to charge yet.
    phase = invoice.payment_phase_for(mission.status)
    elapsed = time.perf_counter() - start
    logger.info(
        "[CLIENT-PAY] Inferred phase=%s mission=%s (%.3fs)",
        phase, mission_id, elapsed,
    )

    if phase == PAYMENT_PHASE_DEPOSIT_DUE:
        return await create_client_deposit_payment(
            mission_id=mission_id, request=request, client=client, db=db,
        )
    if phase == PAYMENT_PHASE_BALANCE_DUE:
        return await create_client_balance_payment(
            mission_id=mission_id, request=request, client=client, db=db,
        )
    if phase == PAYMENT_PHASE_PAID_IN_FULL:
        raise HTTPException(status_code=400, detail="Invoice is already paid")
    # awaiting_completion
    raise HTTPException(
        status_code=400,
        detail="This invoice is not yet available for payment. Your operator will mark the mission complete once the work is finished.",
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
