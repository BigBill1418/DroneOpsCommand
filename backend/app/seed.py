"""Seed database with default aircraft profiles and admin user."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import hash_password
from app.config import settings
from app.models.aircraft import Aircraft
from app.models.invoice import RateTemplate, LineItemCategory
from app.models.user import User

AIRCRAFT_SEED = [
    {
        "model_name": "DJI Matrice 30T",
        "manufacturer": "DJI",
        "image_filename": "dji_m30t.svg",
        "specs": {
            "max_flight_time": "41 min",
            "max_speed": "51 mph",
            "camera": "48MP Wide + 12MP Zoom (16x optical)",
            "thermal": "640×512 Radiometric Thermal",
            "sensors": "FPV, Infrared, Visual, Laser Rangefinder",
            "weight": "8.3 lbs (with batteries)",
            "ip_rating": "IP55",
            "wind_resistance": "33 mph",
            "transmission": "O3 Enterprise, 9.3 mi range",
        },
    },
    {
        "model_name": "DJI Matrice 4TD",
        "manufacturer": "DJI",
        "image_filename": "dji_m4td.svg",
        "specs": {
            "max_flight_time": "38 min",
            "max_speed": "50 mph",
            "camera": "Wide + Zoom + Thermal + Laser Rangefinder",
            "thermal": "High-res Radiometric Thermal",
            "sensors": "Omnidirectional Obstacle Sensing",
            "weight": "5.1 lbs",
            "ip_rating": "IP55",
            "wind_resistance": "33 mph",
            "transmission": "O4 Enterprise, 12.4 mi range",
        },
    },
    {
        "model_name": "DJI Mavic 3 Pro",
        "manufacturer": "DJI",
        "image_filename": "dji_mavic3pro.svg",
        "specs": {
            "max_flight_time": "43 min",
            "max_speed": "47 mph",
            "camera": "4/3 CMOS Hasselblad + 1/1.3\" Medium Tele + 1/2\" Tele",
            "sensors": "Omnidirectional Obstacle Sensing, APAS 5.0",
            "weight": "2.1 lbs",
            "video": "5.1K/50fps, 4K/120fps, Apple ProRes",
            "wind_resistance": "27 mph",
            "transmission": "O3+, 9.3 mi range",
        },
    },
    {
        "model_name": "DJI Avata 2",
        "manufacturer": "DJI",
        "image_filename": "dji_avata2.svg",
        "specs": {
            "max_flight_time": "23 min",
            "max_speed": "60 mph",
            "camera": "1/1.3\" CMOS, 12MP, 4K/60fps",
            "sensors": "Downward Vision, Infrared ToF",
            "weight": "13.3 oz",
            "fov": "155° Super-Wide FOV",
            "stabilization": "RockSteady + HorizonSteady",
            "transmission": "O4, 8.1 mi range",
        },
    },
    {
        "model_name": "DJI FPV",
        "manufacturer": "DJI",
        "image_filename": "dji_fpv.svg",
        "specs": {
            "max_flight_time": "20 min",
            "max_speed": "87 mph",
            "camera": "1/2.3\" CMOS, 12MP, 4K/60fps",
            "sensors": "Forward + Downward Obstacle Sensing",
            "weight": "1.75 lbs",
            "fov": "150° FOV",
            "modes": "Normal, Sport, Manual (Acro)",
            "transmission": "O3, 6.2 mi range",
        },
    },
    {
        "model_name": "DJI Mini 5 Pro",
        "manufacturer": "DJI",
        "image_filename": "dji_mini5pro.svg",
        "specs": {
            "max_flight_time": "38 min",
            "max_speed": "36 mph",
            "camera": "1/1.3\" CMOS, 50MP, 4K/120fps",
            "sensors": "Omnidirectional Obstacle Sensing, APAS 6.0",
            "weight": "8.8 oz (Sub-250g, no registration in many areas)",
            "video": "4K HDR, D-Log M, HLG, 10-bit, SlowMo",
            "wind_resistance": "24 mph",
            "transmission": "O4+, 12.4 mi range",
        },
    },
]


async def seed_database(db: AsyncSession):
    """Seed the database with initial data."""

    # Seed admin user — always re-hash password to match current config
    result = await db.execute(select(User).where(User.username == settings.admin_username))
    existing_admin = result.scalar_one_or_none()
    if existing_admin:
        existing_admin.hashed_password = hash_password(settings.admin_password)
    else:
        admin = User(
            username=settings.admin_username,
            hashed_password=hash_password(settings.admin_password),
        )
        db.add(admin)

    # Seed rate templates
    rate_templates = [
        {
            "name": "Standard Hourly Rate",
            "description": "Standard operator hourly rate for drone operations",
            "category": LineItemCategory.BILLED_TIME,
            "default_quantity": 1,
            "default_unit": "hours",
            "default_rate": 150.00,
            "sort_order": 0,
        },
        {
            "name": "Travel - Mileage",
            "description": "Per-mile travel charge to and from mission site",
            "category": LineItemCategory.TRAVEL,
            "default_quantity": 1,
            "default_unit": "miles",
            "default_rate": 0.67,
            "sort_order": 1,
        },
        {
            "name": "Travel - Flat Rate",
            "description": "Flat rate travel fee for local missions",
            "category": LineItemCategory.TRAVEL,
            "default_quantity": 1,
            "default_unit": "flat",
            "default_rate": 50.00,
            "sort_order": 2,
        },
        {
            "name": "Rapid Deployment",
            "description": "Extra fee for same-day or emergency deployment",
            "category": LineItemCategory.RAPID_DEPLOYMENT,
            "default_quantity": 1,
            "default_unit": "flat",
            "default_rate": 250.00,
            "sort_order": 3,
        },
        {
            "name": "Night Operations Surcharge",
            "description": "Additional charge for operations conducted at night or low-light",
            "category": LineItemCategory.SPECIAL,
            "default_quantity": 1,
            "default_unit": "flat",
            "default_rate": 100.00,
            "sort_order": 4,
        },
        {
            "name": "Thermal Imaging",
            "description": "Thermal camera operations surcharge",
            "category": LineItemCategory.EQUIPMENT,
            "default_quantity": 1,
            "default_unit": "hours",
            "default_rate": 75.00,
            "sort_order": 5,
        },
        {
            "name": "Video Editing",
            "description": "Post-mission video editing and production",
            "category": LineItemCategory.BILLED_TIME,
            "default_quantity": 1,
            "default_unit": "hours",
            "default_rate": 85.00,
            "sort_order": 6,
        },
        {
            "name": "Report Preparation",
            "description": "Detailed report writing and documentation",
            "category": LineItemCategory.BILLED_TIME,
            "default_quantity": 1,
            "default_unit": "flat",
            "default_rate": 75.00,
            "sort_order": 7,
        },
    ]

    for tmpl_data in rate_templates:
        result = await db.execute(
            select(RateTemplate).where(RateTemplate.name == tmpl_data["name"])
        )
        existing = result.scalar_one_or_none()
        # Skip if it exists (active or soft-deleted — don't resurrect deleted templates)
        if not existing:
            db.add(RateTemplate(**tmpl_data))

    # Migrate: rename Mini 4 Pro -> Mini 5 Pro with updated specs
    result = await db.execute(
        select(Aircraft).where(Aircraft.model_name == "DJI Mini 4 Pro")
    )
    old_mini = result.scalar_one_or_none()
    if old_mini:
        mini5_data = next(a for a in AIRCRAFT_SEED if a["model_name"] == "DJI Mini 5 Pro")
        old_mini.model_name = mini5_data["model_name"]
        old_mini.image_filename = mini5_data["image_filename"]
        old_mini.specs = mini5_data["specs"]

    # Seed aircraft (upsert specs to keep them current)
    for aircraft_data in AIRCRAFT_SEED:
        result = await db.execute(
            select(Aircraft).where(Aircraft.model_name == aircraft_data["model_name"])
        )
        existing = result.scalar_one_or_none()
        if existing:
            existing.specs = aircraft_data["specs"]
            # Only set default image if user hasn't uploaded a custom one
            # Custom uploads go to "aircraft/{id}/..." while defaults are "dji_*.svg"
            if "image_filename" in aircraft_data:
                has_custom_upload = (
                    existing.image_filename
                    and existing.image_filename.startswith("aircraft/")
                )
                if not has_custom_upload:
                    existing.image_filename = aircraft_data["image_filename"]
        else:
            aircraft = Aircraft(**aircraft_data)
            db.add(aircraft)

    await db.commit()
