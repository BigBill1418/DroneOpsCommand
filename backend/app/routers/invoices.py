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
    """Recalculate invoice totals from line items.

    ADR-0009: also re-clamp `deposit_amount` against the new total.
    Without this, an operator who lowers a line item below the existing
    deposit_amount would leave a deposit > total — which the DB CHECK
    constraint `deposit_amount_le_total` would reject on next flush.
    """
    subtotal = sum(float(li.total) for li in invoice.line_items)
    tax_amount = subtotal * float(invoice.tax_rate)
    invoice.subtotal = subtotal
    invoice.tax_amount = tax_amount
    invoice.total = subtotal + tax_amount

    # Re-clamp deposit on every recalc. Only relevant when deposit
    # not yet collected — once paid, the amount is locked.
    if invoice.deposit_required and not invoice.deposit_paid:
        new_total = float(invoice.total)
        current_deposit = float(invoice.deposit_amount or 0)
        if current_deposit > new_total:
            logger.info(
                "[INVOICE-RECALC] Clamping deposit_amount %.2f -> %.2f for invoice=%s (total dropped)",
                current_deposit, new_total, invoice.id,
            )
            invoice.deposit_amount = round(new_total, 2)


def _resolve_deposit_amount(*, deposit_required: bool, deposit_amount: float | None, total: float) -> float:
    """Normalize deposit_amount per ADR-0009 §3.3.

    - deposit_required=False  → forced to 0 regardless of input.
    - deposit_required=True, deposit_amount is None
        → server-fills round(total * 0.50, 2) (TOS §6.2 default).
    - deposit_required=True, deposit_amount provided
        → validated: 0 <= amount <= total. Raises HTTPException(400) on violation.
        - Edge case: total=0 with deposit_required=True is invalid
          (CHECK deposit_required_consistent: deposit_required=True
          implies deposit_amount > 0). Caller should add line items first.
    """
    if not deposit_required:
        return 0.0

    safe_total = max(0.0, float(total))
    if deposit_amount is None:
        return round(safe_total * 0.50, 2)

    amt = float(deposit_amount)
    if amt < 0:
        raise HTTPException(status_code=400, detail="deposit_amount must be >= 0")
    if amt > safe_total:
        raise HTTPException(
            status_code=400,
            detail=f"deposit_amount ({amt:.2f}) cannot exceed invoice total ({safe_total:.2f})",
        )
    return round(amt, 2)


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

    payload = data.model_dump()
    deposit_required = bool(payload.pop("deposit_required", False))
    deposit_amount_input = payload.pop("deposit_amount", None)

    invoice = Invoice(mission_id=mission_id, **payload)
    # Total is 0 at creation (no line items yet); resolve deposit
    # against current (probably 0) total. The operator typically adds
    # line items via /invoice/items, then PATCHes deposit_amount via
    # PUT /invoice — which re-runs validation.
    invoice.deposit_required = deposit_required
    invoice.deposit_amount = _resolve_deposit_amount(
        deposit_required=deposit_required,
        deposit_amount=deposit_amount_input,
        total=float(invoice.total or 0),
    )
    db.add(invoice)
    await db.flush()
    logger.info(
        "[INVOICE-CREATE] mission=%s invoice=%s deposit_required=%s deposit_amount=%.2f",
        mission_id, invoice.id, deposit_required, float(invoice.deposit_amount),
    )

    # Recalculate totals from line items (if any were provided).
    # _recalculate_invoice also re-clamps deposit_amount if total changed.
    result2 = await db.execute(
        select(Invoice)
        .where(Invoice.id == invoice.id)
        .options(selectinload(Invoice.line_items))
    )
    invoice = result2.scalar_one()
    _recalculate_invoice(invoice)
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

    payload = data.model_dump(exclude_unset=True)

    # ADR-0009 — deposit fields are immutable once collected. The
    # webhook handler sets deposit_paid=True; from that point only the
    # rest of the invoice (line items, paid_in_full) is editable.
    deposit_keys_in_payload = {"deposit_required", "deposit_amount"} & payload.keys()
    if deposit_keys_in_payload and invoice.deposit_paid:
        raise HTTPException(
            status_code=400,
            detail="Cannot modify deposit_required / deposit_amount after the deposit has been paid",
        )

    # Pop deposit fields so we can resolve them through the validator
    # rather than just setattr-ing raw user input that bypasses the
    # CHECK constraints.
    new_deposit_required = payload.pop("deposit_required", None)
    new_deposit_amount = payload.pop("deposit_amount", None)

    for key, value in payload.items():
        setattr(invoice, key, value)

    if new_deposit_required is not None or new_deposit_amount is not None:
        # Either field touched — re-resolve both to keep them coherent.
        deposit_required = (
            bool(new_deposit_required)
            if new_deposit_required is not None
            else bool(invoice.deposit_required)
        )
        # If only deposit_required changed (now True) and no amount was
        # provided, recompute the 50% default off current total.
        amount_input = (
            new_deposit_amount
            if "deposit_amount" in (data.model_fields_set or set())
            else (None if deposit_required and not invoice.deposit_required else float(invoice.deposit_amount or 0))
        )
        invoice.deposit_required = deposit_required
        invoice.deposit_amount = _resolve_deposit_amount(
            deposit_required=deposit_required,
            deposit_amount=amount_input,
            total=float(invoice.total or 0),
        )
        logger.info(
            "[INVOICE-UPDATE] mission=%s invoice=%s deposit_required=%s deposit_amount=%.2f",
            mission_id, invoice.id, invoice.deposit_required, float(invoice.deposit_amount),
        )

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
