from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import get_current_user
from app.database import get_db
from app.models.invoice import RateTemplate
from app.models.user import User
from app.schemas.invoice import RateTemplateCreate, RateTemplateResponse, RateTemplateUpdate

router = APIRouter(prefix="/api/rate-templates", tags=["rate-templates"])


@router.get("", response_model=list[RateTemplateResponse])
async def list_rate_templates(
    active_only: bool = False,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    query = select(RateTemplate).order_by(RateTemplate.sort_order, RateTemplate.name)
    if active_only:
        query = query.where(RateTemplate.is_active == True)
    result = await db.execute(query)
    return result.scalars().all()


@router.post("", response_model=RateTemplateResponse, status_code=status.HTTP_201_CREATED)
async def create_rate_template(
    data: RateTemplateCreate,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    template = RateTemplate(**data.model_dump())
    db.add(template)
    await db.flush()
    await db.refresh(template)
    return template


@router.get("/{template_id}", response_model=RateTemplateResponse)
async def get_rate_template(
    template_id: UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    result = await db.execute(select(RateTemplate).where(RateTemplate.id == template_id))
    template = result.scalar_one_or_none()
    if not template:
        raise HTTPException(status_code=404, detail="Rate template not found")
    return template


@router.put("/{template_id}", response_model=RateTemplateResponse)
async def update_rate_template(
    template_id: UUID,
    data: RateTemplateUpdate,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    result = await db.execute(select(RateTemplate).where(RateTemplate.id == template_id))
    template = result.scalar_one_or_none()
    if not template:
        raise HTTPException(status_code=404, detail="Rate template not found")

    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(template, key, value)

    await db.flush()
    await db.refresh(template)
    return template


@router.delete("/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rate_template(
    template_id: UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    result = await db.execute(select(RateTemplate).where(RateTemplate.id == template_id))
    template = result.scalar_one_or_none()
    if not template:
        raise HTTPException(status_code=404, detail="Rate template not found")
    await db.delete(template)
