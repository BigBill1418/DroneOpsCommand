"""Rate-template seed coverage — 2026-09-01 billable-rates expansion.

Asserts the seed list carries the six PV-inspection-era billable rates
Bill requested (2026-09-01) alongside the original eight, with the
pass-through analytics line represented as a $0 template whose
description carries the at-cost rule (the schema has no formula field).

Hermetic: asserts on the module-level RATE_TEMPLATE_SEED constant, same
pattern as AIRCRAFT_SEED — no DB session needed.
"""

from __future__ import annotations

from decimal import Decimal

from app.models.invoice import LineItemCategory
from app.seed import RATE_TEMPLATE_SEED


def _by_name(name: str) -> dict:
    matches = [t for t in RATE_TEMPLATE_SEED if t["name"] == name]
    assert len(matches) == 1, f"expected exactly one template named {name!r}"
    return matches[0]


def test_seed_names_are_unique():
    names = [t["name"] for t in RATE_TEMPLATE_SEED]
    assert len(names) == len(set(names))


def test_seed_sort_orders_are_unique_and_stable():
    orders = [t["sort_order"] for t in RATE_TEMPLATE_SEED]
    assert len(orders) == len(set(orders))
    # Original eight occupy 0-7; expansion appends after them.
    assert orders == sorted(orders)


def test_original_eight_still_present():
    for name in [
        "Standard Hourly Rate",
        "Travel - Mileage",
        "Travel - Flat Rate",
        "Rapid Deployment",
        "Night Operations Surcharge",
        "Thermal Imaging",
        "Video Editing",
        "Report Preparation",
    ]:
        _by_name(name)


def test_pv_thermal_inspection_field_day():
    t = _by_name("PV Thermal Inspection — Field Day")
    assert t["category"] == LineItemCategory.BILLED_TIME
    assert Decimal(str(t["default_rate"])) == Decimal("2200.00")
    assert t["default_unit"] == "flat"


def test_mobilization_regional_overnight():
    t = _by_name("Mobilization — Regional Overnight")
    assert t["category"] == LineItemCategory.TRAVEL
    assert Decimal(str(t["default_rate"])) == Decimal("850.00")
    assert t["default_unit"] == "flat"


def test_lodging_per_diem():
    t = _by_name("Lodging + Per Diem")
    assert t["category"] == LineItemCategory.TRAVEL
    assert Decimal(str(t["default_rate"])) == Decimal("235.00")
    assert t["default_unit"] == "days"


def test_weather_standby():
    t = _by_name("Weather Standby")
    assert t["category"] == LineItemCategory.BILLED_TIME
    assert Decimal(str(t["default_rate"])) == Decimal("1100.00")
    assert t["default_unit"] == "flat"


def test_data_processing_and_qa():
    t = _by_name("Data Processing & QA")
    assert t["category"] == LineItemCategory.BILLED_TIME
    assert Decimal(str(t["default_rate"])) == Decimal("150.00")
    assert t["default_unit"] == "hours"


def test_third_party_analytics_pass_through_at_cost():
    t = _by_name("Third-Party Analytics (pass-through)")
    assert t["category"] == LineItemCategory.OTHER
    # No formula support in the schema: rate stays 0 and the description
    # carries the at-cost rule (operator decision 2026-09-01 — no markup).
    assert Decimal(str(t["default_rate"])) == Decimal("0")
    assert t["default_unit"] == "flat"
    desc = t["description"].lower()
    assert "cost" in desc
    assert "markup" not in desc or "no markup" in desc
    assert "%" not in t["description"]
