from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from app.models.invoice import LineItemCategory


class LineItemCreate(BaseModel):
    description: str
    category: LineItemCategory = LineItemCategory.OTHER
    quantity: float = 1
    unit_price: float = 0
    sort_order: int = 0


class LineItemUpdate(BaseModel):
    description: str | None = None
    category: LineItemCategory | None = None
    quantity: float | None = None
    unit_price: float | None = None
    sort_order: int | None = None


class LineItemResponse(BaseModel):
    id: UUID
    description: str
    category: LineItemCategory
    quantity: float
    unit_price: float
    total: float
    sort_order: int

    model_config = {"from_attributes": True}


class InvoiceCreate(BaseModel):
    invoice_number: str | None = None
    tax_rate: float = 0
    notes: str | None = None


class InvoiceUpdate(BaseModel):
    invoice_number: str | None = None
    tax_rate: float | None = None
    notes: str | None = None


class InvoiceResponse(BaseModel):
    id: UUID
    mission_id: UUID
    invoice_number: str | None
    subtotal: float
    tax_rate: float
    tax_amount: float
    total: float
    notes: str | None
    created_at: datetime
    line_items: list[LineItemResponse] = []

    model_config = {"from_attributes": True}


class RateTemplateCreate(BaseModel):
    name: str
    description: str | None = None
    category: LineItemCategory = LineItemCategory.OTHER
    default_quantity: float = 1
    default_unit: str | None = None
    default_rate: float = 0
    sort_order: int = 0


class RateTemplateUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    category: LineItemCategory | None = None
    default_quantity: float | None = None
    default_unit: str | None = None
    default_rate: float | None = None
    is_active: bool | None = None
    sort_order: int | None = None


class RateTemplateResponse(BaseModel):
    id: UUID
    name: str
    description: str | None
    category: LineItemCategory
    default_quantity: float
    default_unit: str | None
    default_rate: float
    is_active: bool
    sort_order: int
    created_at: datetime

    model_config = {"from_attributes": True}
