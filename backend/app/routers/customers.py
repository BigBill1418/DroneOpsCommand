import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import get_current_user
from app.database import get_db
from app.models.customer import Customer
from app.models.tos_acceptance import TosAcceptance
from app.models.user import User
from app.schemas.customer import CustomerCreate, CustomerResponse, CustomerUpdate

logger = logging.getLogger("doc.customers")

router = APIRouter(prefix="/api/customers", tags=["customers"])


def _serialize_customer(
    customer: Customer,
    latest_audit: TosAcceptance | None,
) -> CustomerResponse:
    """Build a CustomerResponse including the latest-acceptance pointer.

    `latest_audit` is the most recent tos_acceptances row for the customer
    or None if the customer has never used the AcroForm flow (legacy
    canvas-signed customers fall here — their per-customer ``tos_signed``
    + ``tos_pdf_path`` columns remain authoritative).
    """
    return CustomerResponse(
        id=customer.id,
        name=customer.name,
        email=customer.email,
        phone=customer.phone,
        address=customer.address,
        city=customer.city,
        state=customer.state,
        zip_code=customer.zip_code,
        company=customer.company,
        notes=customer.notes,
        tos_signed=customer.tos_signed,
        tos_signed_at=customer.tos_signed_at,
        intake_completed_at=customer.intake_completed_at,
        created_at=customer.created_at,
        updated_at=customer.updated_at,
        latest_tos_audit_id=latest_audit.audit_id if latest_audit else None,
        latest_tos_signed_sha=latest_audit.signed_sha256 if latest_audit else None,
        latest_tos_template_version=(
            latest_audit.template_version if latest_audit else None
        ),
    )


async def _latest_audits_by_customer(
    db: AsyncSession,
    customer_ids: list[UUID],
) -> dict[UUID, TosAcceptance]:
    """Return {customer_id: latest_tos_acceptance_row} in ONE query.

    Uses ``DISTINCT ON (customer_id)`` ordered by ``accepted_at DESC`` —
    Postgres-native, single round-trip, no N+1. For the operator
    Customers page (low hundreds of rows max) this beats a LATERAL join
    on readability and is fully covered by the existing ``ix_customer_id``
    index on ``tos_acceptances``.
    """
    if not customer_ids:
        return {}
    stmt = (
        select(TosAcceptance)
        .where(TosAcceptance.customer_id.in_(customer_ids))
        .distinct(TosAcceptance.customer_id)
        .order_by(
            TosAcceptance.customer_id,
            TosAcceptance.accepted_at.desc(),
        )
    )
    rows = (await db.execute(stmt)).scalars().all()
    return {row.customer_id: row for row in rows if row.customer_id is not None}


@router.get("", response_model=list[CustomerResponse])
async def list_customers(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    result = await db.execute(select(Customer).order_by(Customer.name))
    customers = list(result.scalars().all())

    # v2.66.3 — bulk-fetch the latest tos_acceptances row per customer in
    # ONE query (DISTINCT ON), then serialize. No N+1.
    latest = await _latest_audits_by_customer(db, [c.id for c in customers])
    logger.info(
        "[CUSTOMERS-LIST-GET] customers=%d with_acroform_audit=%d",
        len(customers), len(latest),
    )
    return [_serialize_customer(c, latest.get(c.id)) for c in customers]


@router.post("", response_model=CustomerResponse, status_code=status.HTTP_201_CREATED)
async def create_customer(
    data: CustomerCreate,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    customer = Customer(**data.model_dump())
    db.add(customer)
    await db.flush()
    await db.refresh(customer)
    # New customer cannot have an audit yet, so latest_audit is always
    # None — skip the lookup entirely.
    return _serialize_customer(customer, None)


@router.get("/{customer_id}", response_model=CustomerResponse)
async def get_customer(
    customer_id: UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    result = await db.execute(select(Customer).where(Customer.id == customer_id))
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    audit_stmt = (
        select(TosAcceptance)
        .where(TosAcceptance.customer_id == customer_id)
        .order_by(TosAcceptance.accepted_at.desc())
        .limit(1)
    )
    latest = (await db.execute(audit_stmt)).scalar_one_or_none()
    return _serialize_customer(customer, latest)


@router.put("/{customer_id}", response_model=CustomerResponse)
async def update_customer(
    customer_id: UUID,
    data: CustomerUpdate,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    result = await db.execute(select(Customer).where(Customer.id == customer_id))
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(customer, key, value)

    await db.flush()
    await db.refresh(customer)

    audit_stmt = (
        select(TosAcceptance)
        .where(TosAcceptance.customer_id == customer_id)
        .order_by(TosAcceptance.accepted_at.desc())
        .limit(1)
    )
    latest = (await db.execute(audit_stmt)).scalar_one_or_none()
    return _serialize_customer(customer, latest)


@router.delete("/{customer_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_customer(
    customer_id: UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    result = await db.execute(select(Customer).where(Customer.id == customer_id))
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    await db.delete(customer)
