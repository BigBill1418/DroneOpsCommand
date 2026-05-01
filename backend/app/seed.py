"""Seed database with default aircraft profiles and rate templates.

v2.56.0: Admin user creation removed from seed — credentials are now set
via the UI setup wizard on first visit. No env vars for passwords.
Demo mode: auto-creates a demo admin from DEMO_ADMIN_USERNAME/PASSWORD env vars.

v2.63.13: Fleet image heal pass — repairs broken `aircraft/<uuid>/...`
upload paths (orphaned by the 2026-04-20 BOS migration) by falling back
to the bundled default PNG that matches the aircraft model. Adds
DJI Mavic 4 Pro to the seed and refreshes specs to match BarnardHQ
public-site carousel (single source of truth for fleet specs).
"""

import logging
import os

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import hash_password, verify_password
from app.config import settings
from app.models.aircraft import Aircraft
from app.models.invoice import RateTemplate, LineItemCategory
from app.models.user import User

_seed_log = logging.getLogger("doc.seed")

# PostgreSQL advisory lock ID — prevents concurrent seed execution across workers
_SEED_LOCK_ID = 8675309

AIRCRAFT_SEED = [
    {
        "model_name": "DJI Matrice 30T",
        "manufacturer": "DJI",
        "image_filename": "dji_m30t_official.png",
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
        "image_filename": "dji_m4td_official.png",
        "specs": {
            "max_flight_time": "54 min",
            "max_speed": "47 mph (21 m/s)",
            "camera": "48MP Wide (24mm f/1.7) + 48MP Medium Tele (70mm) + 48MP Tele (168mm, 112x hybrid zoom)",
            "thermal": "640×512 Radiometric (UHR 1280×1024), -40°C to 500°C",
            "laser_rangefinder": "1800m range, ±(0.2m + 0.15%D) accuracy",
            "sensors": "Omnidirectional Binocular Vision + 3D Infrared, 0.5–200m detection",
            "weight": "4.1 lbs (1850g with battery)",
            "ip_rating": "IP55",
            "wind_resistance": "27 mph (12 m/s)",
            "transmission": "O4 Enterprise, 15.5 mi / 25 km (FCC)",
            "gnss": "GPS + Galileo + BeiDou + GLONASS + QZSS",
            "rtk": "Centimeter-level positioning (integrated module)",
            "operating_temp": "-20°C to 50°C",
            "max_altitude": "21,325 ft (6500m)",
            "night_vision": "ISO 100–409600 (Night Scene Mode)",
            "dock_compatible": "DJI Dock 3 — autonomous 24/7 operations",
            "highlights": "Quad-sensor payload, dock-ready autonomous ops, IP55 all-weather, RTK precision",
        },
    },
    {
        "model_name": "DJI Mavic 4 Pro",
        "manufacturer": "DJI",
        "image_filename": "dji_mavic4pro_official.png",
        "specs": {
            "max_flight_time": "51 min",
            "max_speed": "56 mph (25 m/s)",
            "camera": "4/3\" Hasselblad Wide (28mm equiv, f/2.0) + 1/1.3\" Medium Tele (70mm, f/2.8) + 1/1.5\" Tele (168mm, f/2.8)",
            "sensors": "Omnidirectional Obstacle Sensing + Forward LiDAR",
            "weight": "2.4 lbs (1063g)",
            "video": "6K/60 HDR, 4K/120fps, 10-bit D-Log / D-Log M / HLG",
            "photo": "100 MP (Hasselblad Natural Colour Solution)",
            "wind_resistance": "27 mph (12 m/s)",
            "transmission": "O4+, 18.6 mi (30 km, FCC)",
            "gimbal": "3-axis with infinity rotation (360° free yaw)",
            "internal_storage": "64 GB",
            "operating_temp": "-10°C to 40°C",
            "controller": "DJI RC 2 (Creator Combo: DJI RC Pro 2)",
            "highlights": "Flagship cinema platform — tri-camera Hasselblad + LiDAR obstacle sensing + 100 MP stills",
        },
    },
    {
        "model_name": "DJI Mavic 3 Pro",
        "manufacturer": "DJI",
        "image_filename": "dji_mavic3pro_official.png",
        "specs": {
            "max_flight_time": "43 min",
            "max_speed": "47 mph",
            "camera": "4/3\" Hasselblad (24mm) + 1/1.3\" Medium Tele (70mm) + 1/2\" Tele (166mm)",
            "sensors": "Omnidirectional Obstacle Sensing, APAS 5.0",
            "weight": "2.1 lbs",
            "video": "5.1K/50fps HDR, 4K/120fps, Apple ProRes, 10-bit D-Log M / HLG",
            "photo": "20 MP RAW",
            "wind_resistance": "27 mph",
            "transmission": "O3+, 9.3 mi range",
        },
    },
    {
        "model_name": "DJI Avata 2",
        "manufacturer": "DJI",
        "image_filename": "dji_avata2_official.png",
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
        "image_filename": "dji_fpv_official.png",
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
        "image_filename": "dji_mini5pro_official.png",
        "specs": {
            "max_flight_time": "52 min",
            "max_speed": "36 mph",
            "camera": "1\" CMOS, 50MP, f/1.8, 4K/120fps",
            "sensors": "Omnidirectional Obstacle Sensing, APAS 6.0",
            "weight": "< 8.8 oz (Sub-250g, no registration in many areas)",
            "video": "4K/120fps HDR, D-Log M, HLG, 10-bit, SlowMo",
            "wind_resistance": "27 mph",
            "transmission": "O4, 12.4 mi range",
        },
    },
]


async def seed_database(db: AsyncSession):
    """Seed the database with initial data.

    v2.56.0 admin policy:
    - Normal mode: NO admin user created — user sets credentials via setup wizard
    - Demo mode: auto-creates demo admin from DEMO_ADMIN_USERNAME/PASSWORD env vars
    - Advisory lock prevents concurrent seed execution across workers
    """

    # ── Acquire advisory lock (blocks other workers) ───────────────
    await db.execute(text(f"SELECT pg_advisory_lock({_SEED_LOCK_ID})"))
    _seed_log.info("Seed lock acquired — beginning seed")

    try:
        # ── Demo mode: auto-create demo admin ─────────────────────────────────────────────
        # ── Demo mode: auto-create demo admin ──────────────────────
        if settings.demo_mode and settings.demo_admin_username and settings.demo_admin_password:
            result = await db.execute(
                select(User).where(User.username == settings.demo_admin_username)
            )
            existing_demo = result.scalar_one_or_none()
            if not existing_demo:
                new_hash = hash_password(settings.demo_admin_password)
                demo_user = User(
                    username=settings.demo_admin_username,
                    hashed_password=new_hash,
                )
                db.add(demo_user)
                await db.flush()
                _seed_log.info("SEED: Demo admin '%s' created", settings.demo_admin_username)
            else:
                _seed_log.info("SEED: Demo admin '%s' already exists", settings.demo_admin_username)
        else:
            # Normal mode — log user count, no admin creation
            result = await db.execute(select(User))
            user_count = len(result.scalars().all())
            _seed_log.info("SEED: %d user(s) in database (credentials managed via UI)", user_count)
    finally:
        # ── Release advisory lock ──────────────────────────────────
        await db.execute(text(f"SELECT pg_advisory_unlock({_SEED_LOCK_ID})"))
        _seed_log.info("Seed lock released")

    # ── Rate templates ─────────────────────────────────────────────
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
        if not existing:
            db.add(RateTemplate(**tmpl_data))

    # ── Aircraft ───────────────────────────────────────────────────
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

    for aircraft_data in AIRCRAFT_SEED:
        result = await db.execute(
            select(Aircraft).where(Aircraft.model_name == aircraft_data["model_name"])
        )
        existing = result.scalars().first()
        if existing:
            existing.specs = aircraft_data["specs"]
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

    # ── Heal pass: repair broken aircraft/<uuid>/...jpg paths ──────
    # Background: the 2026-04-20 BOS migration left several aircraft rows
    # pointing at /data/uploads/aircraft/<uuid>/<file>.jpg files that no
    # longer exist on disk. The /uploads/{filename} fallback only serves
    # bundled defaults when the request path has no slash, so the Fleet
    # tab renders broken-image icons for every affected drone.
    #
    # Heal: for each aircraft whose image_filename references a missing
    # uploads file, fall back to the bundled default PNG that matches its
    # model. Keep the old custom-upload behaviour for files that DO exist.
    model_to_default = {
        a["model_name"]: a["image_filename"]
        for a in AIRCRAFT_SEED
        if "image_filename" in a
    }
    bundled_dir = os.path.join(
        os.path.dirname(__file__), "static", "aircraft"
    )
    healed = 0
    result = await db.execute(select(Aircraft))
    for ac in result.scalars().all():
        fname = ac.image_filename
        if not fname:
            continue
        # Bundled default (no slash) — always served by FastAPI fallback.
        if "/" not in fname:
            continue
        # Reject path traversal before joining.
        if ".." in fname:
            continue
        upload_path = os.path.join(settings.upload_dir, fname)
        if os.path.isfile(upload_path):
            continue  # User upload still on disk — leave it alone.
        default = model_to_default.get(ac.model_name)
        if not default:
            continue  # No bundled default for this model — leave broken.
        bundled_path = os.path.join(bundled_dir, default)
        if not os.path.isfile(bundled_path):
            continue  # Bundled file missing too — leave as-is, log next.
        _seed_log.warning(
            "SEED HEAL: aircraft %s (%s) image %s missing on disk — "
            "falling back to bundled default %s",
            ac.id, ac.model_name, fname, default,
        )
        ac.image_filename = default
        healed += 1
    if healed:
        _seed_log.info("SEED HEAL: repaired %d broken aircraft image path(s)", healed)

    await db.commit()
