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
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import get_current_user
from app.database import get_db
from app.models.tos_acceptance import TosAcceptance
from app.models.user import User
from app.schemas.tos_acceptance import TosAcceptanceRequest, TosAcceptanceResponse
from app.services.email_service import send_signed_tos_to_both_parties
from app.services.tos_acceptance import (
    AcceptanceContext,
    ClientIdentity,
    accept_tos,
)
from app.services.tos_template import get_active_tos_template, signed_pdf_dir

logger = logging.getLogger("doc.tos")

router = APIRouter(prefix="/api/tos", tags=["tos"])


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
async def download_unsigned_template() -> Response:
    """Serve the active unsigned TOS so the customer can read it in-browser.

    No auth: this is the public landing page's PDF iframe source. Cache
    is disabled so a re-uploaded template is picked up immediately
    (the route's hot path is one disk read, no DB call).
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
