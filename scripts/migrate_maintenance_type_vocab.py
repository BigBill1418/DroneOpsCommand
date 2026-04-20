"""One-shot migration: snake_case → Title-Case for maintenance_type.

Pre-v2.63.3 the frontend stored record.maintenance_type as snake_case
(e.g. "gimbal_calibration") while the backend seeded schedules with
Title-Case labels ("Gimbal Calibration"). As a result the schedule-match
loop in routers/maintenance.py::create_record never updated
MaintenanceSchedule.last_performed, and alerts could not be cleared.

v2.63.3 unifies the vocabulary on Title-Case. This script rewrites any
existing record.maintenance_type from the legacy snake_case set to the
new canonical Title-Case label. Handles comma-separated values. Safe to
re-run: already-Title-Case values pass through untouched.

Run inside the backend container:
    docker compose exec backend python scripts/migrate_maintenance_type_vocab.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Allow running from repo root: `python scripts/migrate_maintenance_type_vocab.py`
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from sqlalchemy import select  # noqa: E402

from app.database import async_session  # noqa: E402
from app.models.maintenance import MaintenanceRecord  # noqa: E402


LEGACY_TO_CANONICAL: dict[str, str] = {
    "prop_replacement": "Propeller Replacement",
    "motor_inspection": "Motor Inspection",
    "gimbal_calibration": "Gimbal Calibration",
    "sensor_calibration": "Sensor Cleaning",
    "battery_check": "Battery Health Check",
    "airframe_inspection": "Airframe Inspection",
    "antenna_check": "Remote Controller Inspection",
    "firmware_update": "Firmware Review",
    "general_service": "General Service",
    "other": "Other",
}


def remap(value: str) -> str:
    """Rewrite a possibly-comma-separated maintenance_type string."""
    parts = [p.strip() for p in value.split(",") if p.strip()]
    return ",".join(LEGACY_TO_CANONICAL.get(p, p) for p in parts)


async def run() -> None:
    touched = 0
    unchanged = 0
    async with async_session() as session:
        result = await session.execute(select(MaintenanceRecord))
        records = result.scalars().all()
        for rec in records:
            new_value = remap(rec.maintenance_type)
            if new_value != rec.maintenance_type:
                print(f"  {rec.id}  {rec.maintenance_type!r}  ->  {new_value!r}")
                rec.maintenance_type = new_value
                touched += 1
            else:
                unchanged += 1
        await session.commit()

    print(f"\nMigration complete. rewritten={touched}  unchanged={unchanged}  total={touched + unchanged}")


if __name__ == "__main__":
    asyncio.run(run())
