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
            "max_speed": "51 mph (82 km/h)",
            "camera": "48MP Wide + 12MP Zoom (16x optical)",
            "thermal": "640×512 Radiometric Thermal",
            "sensors": "FPV, Infrared, Visual, Laser Rangefinder",
            "weight": "3.77 kg (with batteries)",
            "ip_rating": "IP55",
            "wind_resistance": "33 mph (15 m/s)",
            "transmission": "O3 Enterprise, 15 km range",
        },
    },
    {
        "model_name": "DJI Matrice 4TD",
        "manufacturer": "DJI",
        "image_filename": "dji_m4td.svg",
        "specs": {
            "max_flight_time": "38 min",
            "max_speed": "50 mph (80 km/h)",
            "camera": "Wide + Zoom + Thermal + Laser Rangefinder",
            "thermal": "High-res Radiometric Thermal",
            "sensors": "Omnidirectional Obstacle Sensing",
            "weight": "2.3 kg",
            "ip_rating": "IP55",
            "wind_resistance": "33 mph (15 m/s)",
            "transmission": "O4 Enterprise, 20 km range",
        },
    },
    {
        "model_name": "DJI Mavic 3 Pro",
        "manufacturer": "DJI",
        "image_filename": "dji_mavic3pro.svg",
        "specs": {
            "max_flight_time": "43 min",
            "max_speed": "47 mph (75.6 km/h)",
            "camera": "4/3 CMOS Hasselblad + 1/1.3\" Medium Tele + 1/2\" Tele",
            "sensors": "Omnidirectional Obstacle Sensing, APAS 5.0",
            "weight": "958 g",
            "video": "5.1K/50fps, 4K/120fps, Apple ProRes",
            "wind_resistance": "27 mph (12 m/s)",
            "transmission": "O3+, 15 km range",
        },
    },
    {
        "model_name": "DJI Avata 2",
        "manufacturer": "DJI",
        "image_filename": "dji_avata2.svg",
        "specs": {
            "max_flight_time": "23 min",
            "max_speed": "60 mph (97 km/h)",
            "camera": "1/1.3\" CMOS, 12MP, 4K/60fps",
            "sensors": "Downward Vision, Infrared ToF",
            "weight": "377 g",
            "fov": "155° Super-Wide FOV",
            "stabilization": "RockSteady + HorizonSteady",
            "transmission": "O4, 13 km range",
        },
    },
    {
        "model_name": "DJI FPV",
        "manufacturer": "DJI",
        "image_filename": "dji_fpv.svg",
        "specs": {
            "max_flight_time": "20 min",
            "max_speed": "87 mph (140 km/h)",
            "camera": "1/2.3\" CMOS, 12MP, 4K/60fps",
            "sensors": "Forward + Downward Obstacle Sensing",
            "weight": "795 g",
            "fov": "150° FOV",
            "modes": "Normal, Sport, Manual (Acro)",
            "transmission": "O3, 10 km range",
        },
    },
    {
        "model_name": "DJI Mini 5 Pro",
        "manufacturer": "DJI",
        "image_filename": "dji_mini5pro.svg",
        "specs": {
            "max_flight_time": "38 min",
            "max_speed": "36 mph (57.6 km/h)",
            "camera": "1/1.3\" CMOS, 50MP, 4K/120fps",
            "sensors": "Omnidirectional Obstacle Sensing, APAS 6.0",
            "weight": "249 g (Sub-250g, no registration in many areas)",
            "video": "4K HDR, D-Log M, HLG, 10-bit, SlowMo",
            "wind_resistance": "24 mph (10.7 m/s)",
            "transmission": "O4+, 20 km range",
        },
    },
]


async def seed_database(db: AsyncSession):
    """Seed the database with initial data."""

    # Seed admin user
    result = await db.execute(select(User).where(User.username == settings.admin_username))
    if not result.scalar_one_or_none():
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
        if not result.scalar_one_or_none():
            db.add(RateTemplate(**tmpl_data))

    # Seed aircraft
    for aircraft_data in AIRCRAFT_SEED:
        result = await db.execute(
            select(Aircraft).where(Aircraft.model_name == aircraft_data["model_name"])
        )
        if not result.scalar_one_or_none():
            aircraft = Aircraft(**aircraft_data)
            db.add(aircraft)

    await db.commit()
