"""Financials aggregation endpoint — returns all financial metrics in one call."""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import get_current_user
from app.database import get_db
from app.models.invoice import Invoice, LineItemCategory
from app.models.mission import Mission
from app.models.user import User

router = APIRouter(prefix="/api/financials", tags=["financials"])


@router.get("/summary")
async def financials_summary(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Aggregate financial data across all missions."""
    result = await db.execute(
        select(Mission).where(Mission.is_billable == True)
    )
    missions = result.scalars().all()

    total_revenue = 0.0
    total_paid = 0.0
    total_outstanding = 0.0
    invoiced_count = 0
    paid_count = 0
    category_totals: dict[str, float] = {}
    drone_revenue: dict[str, float] = {}
    customer_revenue: dict[str, dict] = {}
    monthly_revenue: dict[str, float] = {}
    mission_type_revenue: dict[str, float] = {}
    mission_list = []

    for mission in missions:
        inv = mission.invoice
        if not inv:
            continue

        invoiced_count += 1
        inv_total = float(inv.total or 0)
        total_revenue += inv_total

        if inv.paid_in_full:
            paid_count += 1
            total_paid += inv_total
        else:
            total_outstanding += inv_total

        # Category breakdown from line items
        for li in inv.line_items:
            cat = li.category.value if li.category else "other"
            category_totals[cat] = category_totals.get(cat, 0) + float(li.total or 0)

        # Revenue by drone (from mission flights)
        for flight in mission.flights:
            if flight.aircraft:
                name = flight.aircraft.model_name
                drone_revenue[name] = drone_revenue.get(name, 0) + (
                    inv_total / max(len(mission.flights), 1)
                )

        # Revenue by customer
        if mission.customer:
            cid = str(mission.customer.id)
            if cid not in customer_revenue:
                customer_revenue[cid] = {
                    "name": mission.customer.name,
                    "company": mission.customer.company or "",
                    "total": 0,
                    "missions": 0,
                }
            customer_revenue[cid]["total"] += inv_total
            customer_revenue[cid]["missions"] += 1

        # Monthly revenue
        if mission.mission_date:
            month_key = mission.mission_date.strftime("%Y-%m")
            monthly_revenue[month_key] = monthly_revenue.get(month_key, 0) + inv_total

        # Revenue by mission type
        mt = mission.mission_type.value
        mission_type_revenue[mt] = mission_type_revenue.get(mt, 0) + inv_total

        # Mission detail row
        mission_list.append({
            "id": str(mission.id),
            "title": mission.title,
            "mission_type": mission.mission_type.value,
            "mission_date": str(mission.mission_date) if mission.mission_date else None,
            "location": mission.location_name,
            "customer_name": mission.customer.name if mission.customer else None,
            "invoice_total": inv_total,
            "paid": inv.paid_in_full,
            "invoice_number": inv.invoice_number,
        })

    billable_missions = len(missions)
    avg_per_mission = total_revenue / invoiced_count if invoiced_count > 0 else 0
    collection_rate = (total_paid / total_revenue * 100) if total_revenue > 0 else 0

    # Sort lists for frontend
    drone_list = sorted(
        [{"name": k, "revenue": round(v, 2)} for k, v in drone_revenue.items()],
        key=lambda x: x["revenue"],
        reverse=True,
    )
    customer_list = sorted(
        customer_revenue.values(),
        key=lambda x: x["total"],
        reverse=True,
    )
    monthly_list = sorted(
        [{"month": k, "revenue": round(v, 2)} for k, v in monthly_revenue.items()],
        key=lambda x: x["month"],
    )
    type_list = sorted(
        [{"type": k, "revenue": round(v, 2)} for k, v in mission_type_revenue.items()],
        key=lambda x: x["revenue"],
        reverse=True,
    )
    mission_list.sort(key=lambda x: x["mission_date"] or "", reverse=True)

    return {
        "total_revenue": round(total_revenue, 2),
        "total_paid": round(total_paid, 2),
        "total_outstanding": round(total_outstanding, 2),
        "billable_missions": billable_missions,
        "invoiced_count": invoiced_count,
        "paid_count": paid_count,
        "avg_per_mission": round(avg_per_mission, 2),
        "collection_rate": round(collection_rate, 1),
        "category_totals": {k: round(v, 2) for k, v in category_totals.items()},
        "drone_revenue": drone_list,
        "customer_revenue": customer_list,
        "monthly_revenue": monthly_list,
        "mission_type_revenue": type_list,
        "missions": mission_list,
    }
