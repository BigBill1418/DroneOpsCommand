"""TOS-acceptance HTTP routes (ADR-0010).

Public surface:
    GET  /api/tos/template              — serves the active unsigned TOS PDF
    POST /api/tos/accept                — fills + locks + persists + emails
    GET  /api/tos/signed/{audit_id}     — operator-only signed copy fetch
    GET  /api/tos/signed/by-token/{intake_token}
                                          — customer-facing self-serve fetch
                                            (token is the bearer credential)

Three failure modes worth knowing:

* No template uploaded yet → 404 on /template, 503 on /accept (so the
  customer's checkout flow can degrade visibly instead of confusingly).
* SMTP misconfigured → /accept returns 201, the row is committed and
  the PDF written; the email failure is logged but not raised. The
  signed PDF on disk + DB row are the source of truth.
* Tampered intake_token → returned 404; we don't leak whether a row
  exists for a different audit_id under the same token.

Every log line is namespaced ``doc.tos`` so post-incident grep /
Loki query is straightforward.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone

import uuid as _uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, Response
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import get_current_user
from app.database import get_db
from app.models.customer import Customer
from app.models.tos_acceptance import TosAcceptance
from app.models.user import User
from app.schemas.tos_acceptance import (
    TosAcceptanceListItem,
    TosAcceptanceListResponse,
    TosAcceptanceRequest,
    TosAcceptanceResponse,
)
from app.services.email_service import send_signed_tos_to_both_parties
from app.services.tos_acceptance import (
    AcceptanceContext,
    ClientIdentity,
    accept_tos,
)
from app.services.tos_template import get_active_tos_template, signed_pdf_dir

logger = logging.getLogger("doc.tos")

router = APIRouter(prefix="/api/tos", tags=["tos"])
limiter = Limiter(key_func=get_remote_address)

# v2.66.0 — match `Pending Intake YYYY-MM-DD` placeholder name set by
# `intake.initiate_services` on the no-email path. When the customer
# completes TOS, we replace this stub with their real typed name so the
# operator can email them a portal link afterwards.
_PENDING_INTAKE_NAME_RE = re.compile(r"^Pending Intake \d{4}-\d{2}-\d{2}$")


def _client_ip(request: Request) -> str:
    """Honour X-Forwarded-For first hop (CF tunnel + nginx pass it).

    The first IP in the comma-separated chain is the customer's edge
    address as Cloudflare saw it. Falls back to ``request.client``
    (which is the tunnel/proxy IP, only useful for local-host tests).
    """
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",", 1)[0].strip()
    return request.client.host if request.client else "0.0.0.0"


# ── Public routes ────────────────────────────────────────────────────


@router.get("/template")
@limiter.limit("10/minute")
async def download_unsigned_template(request: Request) -> Response:
    """Serve the active unsigned TOS so the customer can read it in-browser.

    No auth: this is the public landing page's PDF iframe source. Cache
    is disabled so a re-uploaded template is picked up immediately
    (the route's hot path is one disk read, no DB call).

    v2.66.0 — rate limit (10/minute per IP). Public + unauthenticated; without
    a limit a single client could spin up a download loop and saturate the
    disk read path. Mirrors `intake.get_intake_form` (30/minute) / the
    intake submit (5/minute) cadence.
    """
    tpl = get_active_tos_template()
    if tpl is None:
        logger.warning("[TOS-TEMPLATE-GET] No template configured — returning 404")
        raise HTTPException(404, "No TOS template configured")
    return Response(
        content=tpl.bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": 'inline; filename="BarnardHQ-Terms-of-Service.pdf"',
            "Cache-Control": "no-store",
        },
    )


@router.post(
    "/accept",
    response_model=TosAcceptanceResponse,
    status_code=status.HTTP_201_CREATED,
)
@limiter.limit("5/minute")
async def accept_terms(
    payload: TosAcceptanceRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> TosAcceptanceResponse:
    """Fill the AcroForm, lock the fields, hash both sides, persist, email."""
    start = time.perf_counter()
    ip = _client_ip(request)
    ua = request.headers.get("user-agent", "")[:1000]

    logger.info(
        "[TOS-ACCEPT-POST] email=%s customer_id=%s intake_token=%s ip=%s",
        payload.email, payload.customer_id,
        (payload.intake_token[:8] + "…") if payload.intake_token else None,
        ip,
    )

    tpl = get_active_tos_template(payload.customer_id)
    if tpl is None:
        logger.error("[TOS-ACCEPT-POST] No TOS template available for customer_id=%s", payload.customer_id)
        raise HTTPException(503, "No TOS template configured")

    client = ClientIdentity(
        full_name=payload.full_name,
        email=payload.email,
        company=payload.company,
        title=payload.title,
    )
    ctx = AcceptanceContext(
        ip=ip,
        user_agent=ua,
        accepted_at=datetime.now(timezone.utc),
    )

    # accept_tos is sync; FastAPI runs sync deps in the threadpool
    # automatically when called from an async route, but this is a
    # plain function call inside an async handler — pypdf operations
    # take ~10–30ms on a 158K template, well below the threshold
    # where we'd want to push it through asyncio.to_thread.
    signed_bytes, record = accept_tos(
        client, ctx, template_bytes=tpl.bytes,
    )

    out_dir = signed_pdf_dir()
    pdf_path = out_dir / f"{record.audit_id}.pdf"
    pdf_path.write_bytes(signed_bytes)
    logger.info(
        "[TOS-ACCEPT-POST] Wrote signed PDF audit_id=%s path=%s size=%d",
        record.audit_id, pdf_path, len(signed_bytes),
    )

    row = TosAcceptance(
        audit_id=record.audit_id,
        customer_id=payload.customer_id,
        intake_token=payload.intake_token,
        client_name=record.field_values["client_name"],
        client_email=record.field_values["client_email"],
        client_company=record.field_values["client_company"],
        client_title=record.field_values["client_title"],
        client_ip=record.field_values["client_ip"],
        user_agent=ctx.user_agent,
        accepted_at=ctx.accepted_at,
        template_version=tpl.version,
        template_sha256=record.template_sha256,
        signed_sha256=record.signed_sha256,
        signed_pdf_path=str(pdf_path),
        signed_pdf_size=len(signed_bytes),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    logger.info(
        "[TOS-ACCEPT-POST] Persisted row id=%s audit_id=%s template_sha=%s signed_sha=%s",
        row.id, row.audit_id,
        row.template_sha256[:12], row.signed_sha256[:12],
    )

    # v2.66.0 (Fix 1) — Sync the customer's typed name + email back onto
    # the customer row when the no-email intake path created a stub
    # (email IS NULL, name == "Pending Intake YYYY-MM-DD"). Without this,
    # the operator can never email the customer a portal link afterwards
    # because customers.email is still null.
    if payload.customer_id is not None:
        cust_result = await db.execute(
            select(Customer).where(Customer.id == payload.customer_id)
        )
        customer = cust_result.scalar_one_or_none()
        if customer is not None:
            email_synced = False
            name_synced = False
            if customer.email is None and payload.email:
                customer.email = payload.email
                email_synced = True
            if customer.name and _PENDING_INTAKE_NAME_RE.match(customer.name):
                customer.name = payload.full_name
                name_synced = True
            if email_synced or name_synced:
                await db.commit()
                logger.info(
                    "[CLIENT-PORTAL] Synced customer name/email from TOS acceptance "
                    "customer_id=%s audit_id=%s email_synced=%s name_synced=%s",
                    payload.customer_id, row.audit_id, email_synced, name_synced,
                )
        else:
            logger.warning(
                "[TOS-ACCEPT-POST] customer_id=%s referenced but not found "
                "(audit row preserved)",
                payload.customer_id,
            )

    # Best-effort email; never roll back the audit row if SMTP fails.
    try:
        await send_signed_tos_to_both_parties(
            client_email=row.client_email,
            client_name=row.client_name,
            audit_id=row.audit_id,
            signed_pdf=signed_bytes,
            db=db,
        )
    except Exception as exc:
        logger.exception(
            "[TOS-ACCEPT-POST] Email failed for audit_id=%s — row preserved: %s",
            row.audit_id, exc,
        )

    elapsed = time.perf_counter() - start
    logger.info(
        "[TOS-ACCEPT-POST] SUCCESS audit_id=%s email=%s (%.3fs)",
        row.audit_id, row.client_email, elapsed,
    )

    return TosAcceptanceResponse(
        id=row.id,
        audit_id=row.audit_id,
        accepted_at=row.accepted_at,
        template_version=row.template_version,
        template_sha256=row.template_sha256,
        signed_sha256=row.signed_sha256,
        download_url=f"/api/tos/signed/by-token/{payload.intake_token}"
        if payload.intake_token
        else f"/api/tos/signed/{row.audit_id}",
    )


@router.get("/signed/by-token/{intake_token}")
async def download_signed_by_token(
    intake_token: str,
    db: AsyncSession = Depends(get_db),
) -> FileResponse:
    """Customer pulls their signed copy using the intake token they hold.

    The token IS the credential here — no operator login needed. We
    keep this path open for as long as the row exists; intake-token
    expiry on ``customers.intake_token_expires_at`` does not gate this
    download because the customer needs durable access to their own
    signed copy.
    """
    if not intake_token or len(intake_token) > 64:
        raise HTTPException(404, "Not found")

    result = await db.execute(
        select(TosAcceptance)
        .where(TosAcceptance.intake_token == intake_token)
        .order_by(TosAcceptance.accepted_at.desc())
        .limit(1)
    )
    row = result.scalar_one_or_none()
    if row is None:
        logger.info("[TOS-SIGNED-TOKEN] No acceptance row for token=%s…", intake_token[:8])
        raise HTTPException(404, "Not found")

    return FileResponse(
        row.signed_pdf_path,
        media_type="application/pdf",
        filename=f"BarnardHQ-ToS-{row.audit_id}.pdf",
    )


# ── Operator-only routes ─────────────────────────────────────────────


@router.get("/acceptances", response_model=TosAcceptanceListResponse)
async def list_acceptances(
    q: str | None = Query(
        default=None,
        max_length=200,
        description="Free-text: matches client_email, audit_id, or client_name (ILIKE).",
    ),
    customer_id: _uuid.UUID | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> TosAcceptanceListResponse:
    """Operator audit-browse over ``tos_acceptances``.

    The customer-portal team needs to answer "did this customer sign?",
    "when?", "give me their PDF" without dropping to ``psql``. This
    endpoint returns the same row shape as the model — minus
    ``signed_pdf_path`` (filesystem-internal, leaks deployment layout)
    and plus a ``download_url`` pointing back at the existing
    operator-gated ``/api/tos/signed/{audit_id}`` route.

    The free-text search runs three OR'd ``ILIKE %q%`` predicates on
    the indexed columns ``client_email`` / ``audit_id`` plus the
    non-indexed ``client_name`` (table is a few-thousand rows max for
    the foreseeable future; a seq scan is fine and avoids a trigram
    index dependency).

    Default ordering is ``accepted_at DESC`` so the freshest rows
    appear first.
    """
    logger.info(
        "[TOS-LIST-GET] operator=%s q=%r customer_id=%s limit=%d offset=%d",
        getattr(user, "username", "?"), q, customer_id, limit, offset,
    )

    # Build the WHERE clause once and reuse for both the count and the
    # page query so they cannot drift out of sync.
    stmt = select(TosAcceptance)
    count_stmt = select(func.count()).select_from(TosAcceptance)

    if customer_id is not None:
        stmt = stmt.where(TosAcceptance.customer_id == customer_id)
        count_stmt = count_stmt.where(TosAcceptance.customer_id == customer_id)

    if q:
        like = f"%{q.strip()}%"
        predicate = or_(
            TosAcceptance.client_email.ilike(like),
            TosAcceptance.audit_id.ilike(like),
            TosAcceptance.client_name.ilike(like),
        )
        stmt = stmt.where(predicate)
        count_stmt = count_stmt.where(predicate)

    total_result = await db.execute(count_stmt)
    total = int(total_result.scalar_one())

    page_stmt = (
        stmt.order_by(TosAcceptance.accepted_at.desc())
        .limit(limit)
        .offset(offset)
    )
    rows_result = await db.execute(page_stmt)
    rows = rows_result.scalars().all()

    items = [
        TosAcceptanceListItem(
            id=row.id,
            audit_id=row.audit_id,
            customer_id=row.customer_id,
            intake_token=row.intake_token,
            client_name=row.client_name,
            client_email=row.client_email,
            client_company=row.client_company,
            client_title=row.client_title,
            client_ip=str(row.client_ip),
            user_agent=row.user_agent,
            accepted_at=row.accepted_at,
            template_version=row.template_version,
            template_sha256=row.template_sha256,
            signed_sha256=row.signed_sha256,
            signed_pdf_size=row.signed_pdf_size,
            created_at=row.created_at,
            download_url=f"/api/tos/signed/{row.audit_id}",
        )
        for row in rows
    ]

    logger.info(
        "[TOS-LIST-GET] returned %d/%d (limit=%d offset=%d)",
        len(items), total, limit, offset,
    )
    return TosAcceptanceListResponse(
        items=items, total=total, limit=limit, offset=offset,
    )


@router.get("/signed/{audit_id}")
async def download_signed_operator(
    audit_id: str,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> FileResponse:
    """Operator pulls a signed copy by audit_id (JWT-authenticated)."""
    result = await db.execute(
        select(TosAcceptance).where(TosAcceptance.audit_id == audit_id)
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "Not found")
    return FileResponse(
        row.signed_pdf_path,
        media_type="application/pdf",
        filename=f"BarnardHQ-ToS-{row.audit_id}.pdf",
    )
