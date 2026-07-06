import logging
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.jwt import get_current_user
from app.database import get_db
from app.models.invoice import Invoice, LineItem
from app.models.mission import Mission
from app.models.system_settings import SystemSetting
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


# ADR-0011 §2 (v2.66.0) — sequential invoice numbering.
# Format: BARNARDHQ-YYYY-NNNN, 4-digit zero-padded counter, year prefix
# resets every Jan 1. Counter row keys per year so a reset is just a new
# row coming online; old years' counters persist for audit. The counter
# itself is held atomically inside a single UPDATE …  RETURNING (PG
# guarantees the read+increment is one statement so concurrent invoices
# never collide on a number, no SELECT FOR UPDATE needed). The
# `system_settings.value` column is TEXT so the integer is stored as
# its decimal string.
_INVOICE_COUNTER_KEY_PREFIX = "invoice_number_counter_"


async def _next_invoice_number(db: AsyncSession) -> str:
    """Atomic next sequence number per year.

    Returns a string like `BARNARDHQ-2026-0001`. Safe under
    concurrency because the UPDATE RETURNING is one PG statement.
    First-use auto-creates the row at 1.
    """
    year = datetime.utcnow().year
    key = f"{_INVOICE_COUNTER_KEY_PREFIX}{year}"

    # Atomic upsert: if no row exists for this year, insert with value '1'.
    # If it exists, atomically bump and return the new value. The `value`
    # column is TEXT so we cast to bigint, increment, cast back. PG's
    # ON CONFLICT DO UPDATE … RETURNING is the atomic primitive here.
    sql = text(
        """
        INSERT INTO system_settings (key, value)
        VALUES (:k, '1')
        ON CONFLICT (key) DO UPDATE
          SET value = (CAST(system_settings.value AS BIGINT) + 1)::TEXT
        RETURNING value
        """
    )
    result = await db.execute(sql, {"k": key})
    row = result.fetchone()
    next_int = int(row[0])
    formatted = f"BARNARDHQ-{year}-{next_int:04d}"
    logger.info(
        "[INVOICE-NUMBER] Allocated %s (counter=%s)", formatted, key,
    )
    return formatted


def _create_time_deposit_state() -> tuple[bool, float]:
    """(deposit_required, deposit_amount) to persist when an invoice is
    first created.

    A new invoice has no line items, so its total is always 0 at create
    (InvoiceCreate carries no line_items — they are added afterward via
    POST /invoice/items). At total=0 no required deposit can satisfy the
    DB CHECK constraints: `deposit_required_consistent` needs
    deposit_amount > 0, while `deposit_amount_le_total` needs
    deposit_amount <= total (i.e. <= 0). The only constraint-safe state
    is a deferred deposit. The operator's requested deposit is re-applied
    by the first PUT /invoice once line items push total > 0 (see
    frontend MissionInvoiceEdit.handleSave). Persisting the deposit
    eagerly here raised a raw 500 IntegrityError that surfaced to the
    operator as "failing to save invoice".
    """
    return False, 0.0


def _recalculate_invoice(invoice: Invoice):
    """Recalculate invoice totals from line items, then derive the deposit.

    ADR-0009 / operator decision 2026-05-23: "Require 50% deposit" means
    the deposit is ALWAYS exactly 50% of the current total — there is no
    manual override. Because every mutation (add/edit/delete line item,
    any PUT /invoice) ends in this function, deriving the deposit here is
    what makes it track line-item changes automatically and accurately.
    """
    subtotal = sum(float(li.total) for li in invoice.line_items)
    tax_amount = subtotal * float(invoice.tax_rate)
    invoice.subtotal = subtotal
    invoice.tax_amount = tax_amount
    invoice.total = subtotal + tax_amount

    # Derive the deposit from (deposit_required, total) on every recalc.
    # Skip entirely once collected — a paid deposit is locked at the
    # amount actually charged and must not be recomputed.
    if not invoice.deposit_paid:
        new_total = float(invoice.total)
        if not invoice.deposit_required:
            invoice.deposit_amount = 0.0
        elif new_total <= 0:
            # No line items yet, or all removed (e.g. the transient state
            # mid delete-then-recreate save). A required deposit can't
            # satisfy the deposit_* CHECK constraints at total=0
            # (deposit_required_consistent needs amount>0;
            # deposit_amount_le_total needs amount<=0), so defer it. The
            # deposit is restored on the next PUT once total>0 — same
            # contract as _create_time_deposit_state. Without this,
            # deleting the last line item raised a raw 500 and aborted the
            # save, silently dropping line items.
            invoice.deposit_required = False
            invoice.deposit_amount = 0.0
        else:
            # Always 50% of the live total; re-derived so it tracks every
            # line-item change. _resolve_deposit_amount(.., None, total)
            # is the validated 50% computation.
            invoice.deposit_amount = _resolve_deposit_amount(
                deposit_required=True, deposit_amount=None, total=new_total,
            )


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
    # Deposit fields are popped and ignored at create: a new invoice has
    # no line items (total=0) and the DB CHECK constraints make any
    # required deposit impossible at total=0. The deposit is deferred and
    # applied by the first PUT once line items exist. See
    # _create_time_deposit_state for the full rationale.
    payload.pop("deposit_required", None)
    payload.pop("deposit_amount", None)

    # ADR-0011 §2 — allocate sequential invoice number IF the operator
    # didn't supply one explicitly. Pre-existing rows with NULL
    # invoice_number stay null (they were dev/test; ADR-0011 doesn't
    # backfill).
    incoming_number = payload.pop("invoice_number", None)
    if not incoming_number:
        payload["invoice_number"] = await _next_invoice_number(db)
    else:
        payload["invoice_number"] = incoming_number

    invoice = Invoice(mission_id=mission_id, **payload)
    # Total is 0 at creation (no line items yet). A required deposit
    # cannot satisfy the DB CHECK constraints at total=0, so persist a
    # deferred deposit. The operator adds line items via /invoice/items,
    # then PUT /invoice applies the deposit against the real total.
    invoice.deposit_required, invoice.deposit_amount = _create_time_deposit_state()
    db.add(invoice)
    await db.flush()
    logger.info(
        "[INVOICE-CREATE] mission=%s invoice=%s deposit deferred (total=0; "
        "applied on next PUT once line items exist)",
        mission_id, invoice.id,
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

    # ADR-0040: detect the manual paid-in-full transition so the automated
    # download-link delivery can fire exactly once, same as the Stripe path.
    became_paid_in_full = bool(payload.get("paid_in_full")) and not invoice.paid_in_full

    # ADR-0009 — deposit fields are immutable once collected. The
    # webhook handler sets deposit_paid=True; from that point only the
    # rest of the invoice (line items, paid_in_full) is editable.
    deposit_keys_in_payload = {"deposit_required", "deposit_amount"} & payload.keys()
    if deposit_keys_in_payload and invoice.deposit_paid:
        raise HTTPException(
            status_code=400,
            detail="Cannot modify deposit_required / deposit_amount after the deposit has been paid",
        )

    # Deposit amount is no longer operator-settable: "Require 50% deposit"
    # means the deposit is always 50% of the total, derived authoritatively
    # by _recalculate_invoice below. Accept and ignore any amount a client
    # sends so older UIs don't 400.
    new_deposit_required = payload.pop("deposit_required", None)
    payload.pop("deposit_amount", None)

    for key, value in payload.items():
        setattr(invoice, key, value)

    if new_deposit_required is not None:
        invoice.deposit_required = bool(new_deposit_required)
        logger.info(
            "[INVOICE-UPDATE] mission=%s invoice=%s deposit_required=%s",
            mission_id, invoice.id, invoice.deposit_required,
        )

    _recalculate_invoice(invoice)
    await db.flush()

    if became_paid_in_full:
        logger.info(
            "[INVOICE-UPDATE] mission=%s invoice=%s manually marked PAID IN FULL — "
            "triggering download-link delivery (ADR-0040)",
            mission_id, invoice.id,
        )
        from app.services.download_link_delivery import deliver_download_link_if_due
        await deliver_download_link_if_due(db, mission_id, trigger="manual-mark-paid")

    await db.refresh(invoice)
    return invoice


# --- Line Items ---

@router.put("/{mission_id}/invoice/items", response_model=InvoiceResponse)
async def replace_line_items(
    mission_id: UUID,
    data: list[LineItemCreate],
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Atomically replace ALL line items for an invoice in one transaction.

    Supersedes the editor's old delete-each-then-add-each loop, which was
    non-transactional: an interruption mid-loop (e.g. a flaky field
    connection) left the line items and the stored total out of sync — a
    stale total that then drives the wrong balance/charge. Doing the full
    replace + recalc inside one request makes it all-or-nothing (get_db
    commits once on success, rolls back entirely on any error), so the
    stored total can never desync from the line items.
    """
    result = await db.execute(
        select(Invoice)
        .where(Invoice.mission_id == mission_id)
        .options(selectinload(Invoice.line_items))
    )
    invoice = result.scalar_one_or_none()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    for li in list(invoice.line_items):
        await db.delete(li)
    await db.flush()

    for i, item in enumerate(data):
        db.add(LineItem(
            invoice_id=invoice.id,
            description=item.description,
            category=item.category,
            quantity=item.quantity,
            unit_price=item.unit_price,
            total=float(item.quantity) * float(item.unit_price),
            sort_order=i,
        ))
    await db.flush()

    result2 = await db.execute(
        select(Invoice)
        .where(Invoice.id == invoice.id)
        .options(selectinload(Invoice.line_items))
    )
    invoice = result2.scalar_one()
    _recalculate_invoice(invoice)
    await db.flush()
    await db.refresh(invoice)
    logger.info(
        "[INVOICE-ITEMS-REPLACE] mission=%s invoice=%s items=%d total=%.2f",
        mission_id, invoice.id, len(data), float(invoice.total),
    )
    return invoice


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
