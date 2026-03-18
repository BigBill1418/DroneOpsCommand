import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.jwt import get_current_user
from app.database import get_db
from app.models.invoice import Invoice, LineItem
from app.models.mission import Mission
from app.models.user import User
from app.schemas.invoice import (
    InvoiceCreate,
    InvoiceResponse,
    InvoiceUpdate,
    LineItemCreate,
    LineItemResponse,
    LineItemUpdate,
)

logger = logging.getLogger("doc.invoices")

router = APIRouter(prefix="/api/missions", tags=["invoices"])


def _recalculate_invoice(invoice: Invoice):
    """Recalculate invoice totals from line items."""
    subtotal = sum(float(li.total) for li in invoice.line_items)
    tax_amount = subtotal * float(invoice.tax_rate)
    invoice.subtotal = subtotal
    invoice.tax_amount = tax_amount
    invoice.total = subtotal + tax_amount


@router.get("/{mission_id}/invoice", response_model=InvoiceResponse)
async def get_invoice(
    mission_id: UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(Invoice)
        .where(Invoice.mission_id == mission_id)
        .options(selectinload(Invoice.line_items))
    )
    invoice = result.scalar_one_or_none()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return invoice


@router.post("/{mission_id}/invoice", response_model=InvoiceResponse, status_code=status.HTTP_201_CREATED)
async def create_invoice(
    mission_id: UUID,
    data: InvoiceCreate,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    # Verify mission exists
    result = await db.execute(select(Mission).where(Mission.id == mission_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Mission not found")

    # Check if invoice already exists
    existing = await db.execute(select(Invoice).where(Invoice.mission_id == mission_id))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Invoice already exists for this mission")

    invoice = Invoice(mission_id=mission_id, **data.model_dump())
    db.add(invoice)
    await db.flush()
    await db.refresh(invoice)
    return invoice


@router.put("/{mission_id}/invoice", response_model=InvoiceResponse)
async def update_invoice(
    mission_id: UUID,
    data: InvoiceUpdate,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(Invoice)
        .where(Invoice.mission_id == mission_id)
        .options(selectinload(Invoice.line_items))
    )
    invoice = result.scalar_one_or_none()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(invoice, key, value)

    _recalculate_invoice(invoice)
    await db.flush()
    await db.refresh(invoice)
    return invoice


# --- Line Items ---

@router.post("/{mission_id}/invoice/items", response_model=LineItemResponse, status_code=status.HTTP_201_CREATED)
async def add_line_item(
    mission_id: UUID,
    data: LineItemCreate,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(Invoice)
        .where(Invoice.mission_id == mission_id)
        .options(selectinload(Invoice.line_items))
    )
    invoice = result.scalar_one_or_none()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    item = LineItem(
        invoice_id=invoice.id,
        description=data.description,
        category=data.category,
        quantity=data.quantity,
        unit_price=data.unit_price,
        total=data.quantity * data.unit_price,
        sort_order=data.sort_order,
    )
    db.add(item)
    await db.flush()

    # Recalculate totals — re-query to include new item
    result = await db.execute(
        select(Invoice)
        .where(Invoice.id == invoice.id)
        .options(selectinload(Invoice.line_items))
    )
    invoice = result.scalar_one()
    _recalculate_invoice(invoice)
    await db.flush()
    await db.refresh(item)
    return item


@router.put("/{mission_id}/invoice/items/{item_id}", response_model=LineItemResponse)
async def update_line_item(
    mission_id: UUID,
    item_id: UUID,
    data: LineItemUpdate,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    result = await db.execute(select(LineItem).where(LineItem.id == item_id))
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Line item not found")

    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(item, key, value)

    item.total = float(item.quantity) * float(item.unit_price)
    await db.flush()

    # Recalculate invoice totals with eager loaded line_items
    invoice_result = await db.execute(
        select(Invoice)
        .where(Invoice.mission_id == mission_id)
        .options(selectinload(Invoice.line_items))
    )
    invoice = invoice_result.scalar_one_or_none()
    if invoice:
        _recalculate_invoice(invoice)
        await db.flush()

    await db.refresh(item)
    return item


@router.delete("/{mission_id}/invoice/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_line_item(
    mission_id: UUID,
    item_id: UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    result = await db.execute(select(LineItem).where(LineItem.id == item_id))
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Line item not found")

    invoice_id = item.invoice_id
    await db.delete(item)
    await db.flush()

    # Recalculate invoice totals with eager loaded line_items
    invoice_result = await db.execute(
        select(Invoice)
        .where(Invoice.id == invoice_id)
        .options(selectinload(Invoice.line_items))
    )
    invoice = invoice_result.scalar_one_or_none()
    if invoice:
        _recalculate_invoice(invoice)
        await db.flush()
