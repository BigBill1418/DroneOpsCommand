"""Demo data seeder — populates the database with realistic sample data.

Called during app startup when DEMO_MODE=true. Idempotent: checks for the
sentinel customer "Demo Solar Co." before inserting anything. Safe to call
on every startup.
"""

import logging
import uuid
from datetime import datetime, timedelta, date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.aircraft import Aircraft
from app.models.customer import Customer
from app.models.flight import Flight
from app.models.maintenance import MaintenanceSchedule
from app.models.mission import Mission, MissionFlight, MissionStatus, MissionType
from app.models.pilot import Pilot

logger = logging.getLogger("doc.demo")

# ── Stable date anchors ───────────────────────────────────────────────────────
_NOW = datetime.utcnow()
_TODAY = _NOW.date()


def _days_ago(n: int) -> date:
    return _TODAY - timedelta(days=n)


def _dt_days_ago(n: int, hour: int = 9, minute: int = 0) -> datetime:
    base = datetime(_TODAY.year, _TODAY.month, _TODAY.day, hour, minute, 0)
    return base - timedelta(days=n)


# ─────────────────────────────────────────────────────────────────────────────
# Static UUIDs (stable across re-seeds)
# ─────────────────────────────────────────────────────────────────────────────

_AC1 = uuid.UUID("a1000000-de00-0000-0000-000000000001")  # M30T
_AC2 = uuid.UUID("a1000000-de00-0000-0000-000000000002")  # M4TD
_AC3 = uuid.UUID("a1000000-de00-0000-0000-000000000003")  # Mavic 3E

_P1 = "b1000000-de00-0000-0000-000000000001"  # Mike Barnard
_P2 = "b1000000-de00-0000-0000-000000000002"  # Sarah Chen

_CU1 = uuid.UUID("c1000000-de00-0000-0000-000000000001")  # Demo Solar Co.
_CU2 = uuid.UUID("c1000000-de00-0000-0000-000000000002")  # Acme Construction
_CU3 = uuid.UUID("c1000000-de00-0000-0000-000000000003")  # City of Springfield
_CU4 = uuid.UUID("c1000000-de00-0000-0000-000000000004")  # Barnard Aerial Services

_M1 = uuid.UUID("d1000000-de00-0000-0000-000000000001")
_M2 = uuid.UUID("d1000000-de00-0000-0000-000000000002")
_M3 = uuid.UUID("d1000000-de00-0000-0000-000000000003")
_M4 = uuid.UUID("d1000000-de00-0000-0000-000000000004")
_M5 = uuid.UUID("d1000000-de00-0000-0000-000000000005")
_M6 = uuid.UUID("d1000000-de00-0000-0000-000000000006")

_F1  = uuid.UUID("f1000000-de00-0000-0000-000000000001")
_F2  = uuid.UUID("f1000000-de00-0000-0000-000000000002")
_F3  = uuid.UUID("f1000000-de00-0000-0000-000000000003")
_F4  = uuid.UUID("f1000000-de00-0000-0000-000000000004")
_F5  = uuid.UUID("f1000000-de00-0000-0000-000000000005")
_F6  = uuid.UUID("f1000000-de00-0000-0000-000000000006")
_F7  = uuid.UUID("f1000000-de00-0000-0000-000000000007")
_F8  = uuid.UUID("f1000000-de00-0000-0000-000000000008")
_F9  = uuid.UUID("f1000000-de00-0000-0000-000000000009")
_F10 = uuid.UUID("f1000000-de00-0000-0000-000000000010")
_F11 = uuid.UUID("f1000000-de00-0000-0000-000000000011")
_F12 = uuid.UUID("f1000000-de00-0000-0000-000000000012")


async def seed_demo_data(db: AsyncSession) -> None:
    """Seed the database with realistic demo data if not already present.

    Idempotent — checks for the sentinel customer "Demo Solar Co." before
    inserting anything. Safe to call on every startup when DEMO_MODE=true.
    """
    logger.info("demo_seed: checking whether demo data is already present...")

    result = await db.execute(
        select(Customer).where(Customer.name == "Demo Solar Co.")
    )
    if result.scalar_one_or_none() is not None:
        logger.info("demo_seed: demo data already exists — skipping.")
        return

    logger.info("demo_seed: no existing demo data found — seeding now.")

    try:
        # ── Aircraft ─────────────────────────────────────────────────────────
        logger.info("demo_seed: inserting aircraft...")

        aircraft_list = [
            Aircraft(
                id=_AC1,
                model_name="DJI Matrice 30T",
                manufacturer="DJI",
                serial_number="1ZXDK4C0030001",
                image_filename="dji_m30t_official.png",
                specs={
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
            ),
            Aircraft(
                id=_AC2,
                model_name="DJI Matrice 4TD",
                manufacturer="DJI",
                serial_number="1ZXDK5F0040002",
                image_filename="dji_m4td_official.png",
                specs={
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
                },
            ),
            Aircraft(
                id=_AC3,
                model_name="DJI Mavic 3 Enterprise",
                manufacturer="DJI",
                serial_number="1ZXME3G0020003",
                image_filename="dji_mavic3e_official.png",
                specs={
                    "max_flight_time": "45 min",
                    "max_speed": "47 mph",
                    "camera": "4/3 CMOS 20MP + 70mm Tele 12MP",
                    "sensors": "Omnidirectional Obstacle Sensing, APAS 5.0",
                    "weight": "2.0 lbs (915g)",
                    "ip_rating": "IP43",
                    "wind_resistance": "27 mph",
                    "transmission": "O3 Enterprise, 9.3 mi range",
                    "rtk": "Optional RTK module (centimeter-level positioning)",
                    "storage": "8GB internal + microSD",
                },
            ),
        ]

        # Resolve aircraft — reuse existing (from regular seed) or create new
        ac_id_map = {}  # maps our static UUID -> actual DB UUID
        for ac in aircraft_list:
            existing = await db.execute(
                select(Aircraft).where(Aircraft.model_name == ac.model_name)
            )
            found = existing.scalars().first()
            if found is not None:
                ac_id_map[ac.id] = found.id
                # Backfill serial if missing
                if not found.serial_number and ac.serial_number:
                    found.serial_number = ac.serial_number
            else:
                db.add(ac)
                ac_id_map[ac.id] = ac.id

        await db.flush()
        logger.info("demo_seed: %d aircraft flushed.", len(aircraft_list))

        # ── Pilots ───────────────────────────────────────────────────────────
        logger.info("demo_seed: inserting pilots...")

        pilot_mike = Pilot(
            id=_P1,
            name="Mike Barnard",
            email="mike.barnard@example.com",
            phone="(555) 210-4801",
            faa_certificate_number="4801234",
            faa_certificate_expiry=datetime(2027, 6, 15, 0, 0, 0),
            is_active=True,
            notes="Lead pilot. Specializes in thermal inspections and mapping. Part 107 + night-ops waiver.",
        )
        pilot_sarah = Pilot(
            id=_P2,
            name="Sarah Chen",
            email="sarah.chen@example.com",
            phone="(555) 319-7702",
            faa_certificate_number="5502891",
            faa_certificate_expiry=datetime(2026, 11, 30, 0, 0, 0),
            is_active=True,
            notes="Certified Part 107 pilot. Focus on construction progress documentation and aerial photography.",
        )

        for pilot in [pilot_mike, pilot_sarah]:
            existing = await db.execute(select(Pilot).where(Pilot.id == pilot.id))
            if existing.scalar_one_or_none() is None:
                db.add(pilot)

        await db.flush()
        logger.info("demo_seed: pilots flushed.")

        # ── Customers ────────────────────────────────────────────────────────
        logger.info("demo_seed: inserting customers...")

        db.add(Customer(
            id=_CU1,
            name="Demo Solar Co.",
            email="ops@demosolar.example.com",
            phone="(555) 100-2001",
            company="Demo Solar Co.",
            address="4820 Sunbelt Drive",
            city="Phoenix",
            state="AZ",
            zip_code="85001",
            notes="Large utility-scale solar operator. Requires thermal inspection reports quarterly.",
        ))
        db.add(Customer(
            id=_CU2,
            name="Acme Construction",
            email="project.manager@acmeconstruction.example.com",
            phone="(555) 200-3301",
            company="Acme Construction LLC",
            address="1100 Industrial Parkway",
            city="Dallas",
            state="TX",
            zip_code="75201",
            notes="Ongoing construction progress documentation. Weekly flights requested during active phases.",
        ))
        db.add(Customer(
            id=_CU3,
            name="City of Springfield",
            email="publicworks@springfield.example.gov",
            phone="(555) 300-4401",
            company="City of Springfield — Public Works",
            address="742 Evergreen Terrace",
            city="Springfield",
            state="IL",
            zip_code="62701",
            notes="Municipal client. Bridge and infrastructure inspection contract.",
        ))
        db.add(Customer(
            id=_CU4,
            name="Barnard Aerial Services",
            email="hello@barnardaerial.example.com",
            phone="(555) 400-5501",
            company="Barnard Aerial Services",
            address="88 Runway Court",
            city="Tucson",
            state="AZ",
            zip_code="85701",
            notes="Internal demo client for testing invoicing and reporting workflows.",
        ))

        await db.flush()
        logger.info("demo_seed: customers flushed.")

        # ── Missions ─────────────────────────────────────────────────────────
        logger.info("demo_seed: inserting missions...")

        db.add(Mission(
            id=_M1,
            title="Solar Farm Q1 Thermal Inspection",
            customer_id=_CU1,
            mission_type=MissionType.INSPECTION,
            description="Full thermal sweep of 450-acre solar installation to identify underperforming panels and hotspots. Three flight runs covering north, south, and inverter arrays.",
            mission_date=_days_ago(62),
            location_name="Solana Solar Farm — Gila Bend, AZ",
            status=MissionStatus.COMPLETED,
            is_billable=True,
        ))
        db.add(Mission(
            id=_M2,
            title="Downtown Office Complex — Progress Survey",
            customer_id=_CU2,
            mission_type=MissionType.SURVEY,
            description="Weekly aerial survey of 22-story office tower construction. Document excavation, steel erection, and facade progress for project management board.",
            mission_date=_days_ago(28),
            location_name="Main & Commerce St — Dallas, TX",
            status=MissionStatus.COMPLETED,
            is_billable=True,
        ))
        db.add(Mission(
            id=_M3,
            title="Route 9 Bridge Structural Inspection",
            customer_id=_CU3,
            mission_type=MissionType.INSPECTION,
            description="Visual and photographic inspection of underside deck, support columns, and expansion joints per AASHTO standards. FAA Part 107 waiver approved for under-deck operations.",
            mission_date=_days_ago(14),
            location_name="Route 9 Bridge over Lake Springfield, IL",
            status=MissionStatus.COMPLETED,
            is_billable=True,
        ))
        db.add(Mission(
            id=_M4,
            title="Solar Farm Q2 Thermal Inspection",
            customer_id=_CU1,
            mission_type=MissionType.INSPECTION,
            description="Follow-up Q2 thermal inspection covering the north array expansion added in March. Re-scan original arrays to verify Q1 repair effectiveness.",
            mission_date=_days_ago(3),
            location_name="Solana Solar Farm — Gila Bend, AZ",
            status=MissionStatus.DRAFT,
            is_billable=True,
        ))
        db.add(Mission(
            id=_M5,
            title="Warehouse Roof Orthomosaic Mapping",
            customer_id=_CU2,
            mission_type=MissionType.MAPPING,
            description="Orthomosaic map of 3 warehouse rooftops for leak detection assessment and HVAC unit placement planning.",
            mission_date=_days_ago(7),
            location_name="Acme Distribution Center — Irving, TX",
            status=MissionStatus.DRAFT,
            is_billable=True,
        ))
        db.add(Mission(
            id=_M6,
            title="City Hall Aerial Photography",
            customer_id=_CU3,
            mission_type=MissionType.PHOTOGRAPHY,
            description="Promotional aerial photography of city hall and surrounding civic district for the annual report publication.",
            mission_date=_days_ago(45),
            location_name="Springfield City Hall — Springfield, IL",
            status=MissionStatus.COMPLETED,
            is_billable=False,
        ))

        await db.flush()
        logger.info("demo_seed: missions flushed.")

        # ── Flights ──────────────────────────────────────────────────────────
        logger.info("demo_seed: inserting flights...")

        flights = [
            # M1 — Solar Q1 Thermal: 3 flights
            Flight(
                id=_F1,
                name="Solana North Array — Run 1",
                aircraft_id=ac_id_map.get(_AC1, _AC1),
                pilot_id=_P1,
                drone_model="DJI Matrice 30T",
                drone_name="Matrice-30T Alpha",
                drone_serial="1ZXDK4C0030001",
                start_time=_dt_days_ago(62, hour=7, minute=15),
                duration_secs=2580.0,
                total_distance=4320.0,
                max_altitude=91.0,
                max_speed=10.5,
                home_lat=32.9448,
                home_lon=-112.6974,
                point_count=1548,
                source="dji_txt",
                notes="North array complete. 3 hotspots flagged at rows 14, 22, and 41.",
                tags=["thermal", "solar", "Q1"],
            ),
            Flight(
                id=_F2,
                name="Solana South Array — Run 2",
                aircraft_id=ac_id_map.get(_AC1, _AC1),
                pilot_id=_P1,
                drone_model="DJI Matrice 30T",
                drone_name="Matrice-30T Alpha",
                drone_serial="1ZXDK4C0030001",
                start_time=_dt_days_ago(62, hour=9, minute=30),
                duration_secs=2940.0,
                total_distance=4980.0,
                max_altitude=91.0,
                max_speed=10.8,
                home_lat=32.9388,
                home_lon=-112.6980,
                point_count=1764,
                source="dji_txt",
                notes="South array complete. 1 hotspot in row 7. Panel replacement recommended.",
                tags=["thermal", "solar", "Q1"],
            ),
            Flight(
                id=_F3,
                name="Solana East Inverter Bank — Run 3",
                aircraft_id=ac_id_map.get(_AC1, _AC1),
                pilot_id=_P1,
                drone_model="DJI Matrice 30T",
                drone_name="Matrice-30T Alpha",
                drone_serial="1ZXDK4C0030001",
                start_time=_dt_days_ago(62, hour=12, minute=0),
                duration_secs=1620.0,
                total_distance=2750.0,
                max_altitude=76.0,
                max_speed=9.2,
                home_lat=32.9410,
                home_lon=-112.6920,
                point_count=972,
                source="dji_txt",
                notes="Inverter bank inspection complete. No anomalies detected.",
                tags=["thermal", "solar", "Q1"],
            ),
            # M2 — Acme Construction: 2 flights
            Flight(
                id=_F4,
                name="Office Tower Progress — Orbit Pass",
                aircraft_id=ac_id_map.get(_AC3, _AC3),
                pilot_id=_P2,
                drone_model="DJI Mavic 3 Enterprise",
                drone_name="Mavic-3E Bravo",
                drone_serial="1ZXME3G0020003",
                start_time=_dt_days_ago(28, hour=8, minute=0),
                duration_secs=1440.0,
                total_distance=2100.0,
                max_altitude=122.0,
                max_speed=8.3,
                home_lat=32.7769,
                home_lon=-96.7970,
                point_count=864,
                source="litchi_csv",
                notes="360° orbit at 3 altitudes. Steel erection visible at floor 12.",
                tags=["construction", "progress", "weekly"],
            ),
            Flight(
                id=_F5,
                name="Office Tower Progress — Nadir Map",
                aircraft_id=ac_id_map.get(_AC3, _AC3),
                pilot_id=_P2,
                drone_model="DJI Mavic 3 Enterprise",
                drone_name="Mavic-3E Bravo",
                drone_serial="1ZXME3G0020003",
                start_time=_dt_days_ago(28, hour=9, minute=30),
                duration_secs=2100.0,
                total_distance=3600.0,
                max_altitude=110.0,
                max_speed=9.0,
                home_lat=32.7765,
                home_lon=-96.7968,
                point_count=1260,
                source="litchi_csv",
                notes="Orthomosaic grid of site perimeter and staging area.",
                tags=["construction", "mapping", "weekly"],
            ),
            # M3 — Bridge Inspection: 2 flights
            Flight(
                id=_F6,
                name="Route 9 Bridge — Underside Pass East",
                aircraft_id=ac_id_map.get(_AC2, _AC2),
                pilot_id=_P1,
                drone_model="DJI Matrice 4TD",
                drone_name="Matrice-4TD Charlie",
                drone_serial="1ZXDK5F0040002",
                start_time=_dt_days_ago(14, hour=7, minute=45),
                duration_secs=1980.0,
                total_distance=2850.0,
                max_altitude=30.0,
                max_speed=5.1,
                home_lat=39.7995,
                home_lon=-89.6540,
                point_count=1188,
                source="dji_txt",
                notes="East span underside pass. Minor surface cracking at pier 3 noted for engineering review.",
                tags=["bridge", "inspection", "infrastructure"],
            ),
            Flight(
                id=_F7,
                name="Route 9 Bridge — Underside Pass West",
                aircraft_id=ac_id_map.get(_AC2, _AC2),
                pilot_id=_P1,
                drone_model="DJI Matrice 4TD",
                drone_name="Matrice-4TD Charlie",
                drone_serial="1ZXDK5F0040002",
                start_time=_dt_days_ago(14, hour=9, minute=30),
                duration_secs=1800.0,
                total_distance=2600.0,
                max_altitude=30.0,
                max_speed=4.8,
                home_lat=39.7992,
                home_lon=-89.6560,
                point_count=1080,
                source="dji_txt",
                notes="West span complete. No structural anomalies detected on this span.",
                tags=["bridge", "inspection", "infrastructure"],
            ),
            # M4 — Solar Q2: 2 flights (in progress)
            Flight(
                id=_F8,
                name="Solana North Expansion — Run 1",
                aircraft_id=ac_id_map.get(_AC1, _AC1),
                pilot_id=_P1,
                drone_model="DJI Matrice 30T",
                drone_name="Matrice-30T Alpha",
                drone_serial="1ZXDK4C0030001",
                start_time=_dt_days_ago(3, hour=7, minute=0),
                duration_secs=3060.0,
                total_distance=5000.0,
                max_altitude=91.0,
                max_speed=10.3,
                home_lat=32.9455,
                home_lon=-112.6950,
                point_count=1836,
                source="dji_txt",
                notes="New north expansion array covered. Data uploaded, thermal analysis pending.",
                tags=["thermal", "solar", "Q2"],
            ),
            Flight(
                id=_F9,
                name="Solana Original Array — Re-scan",
                aircraft_id=ac_id_map.get(_AC1, _AC1),
                pilot_id=_P2,
                drone_model="DJI Matrice 30T",
                drone_name="Matrice-30T Alpha",
                drone_serial="1ZXDK4C0030001",
                start_time=_dt_days_ago(3, hour=10, minute=15),
                duration_secs=2700.0,
                total_distance=4500.0,
                max_altitude=91.0,
                max_speed=10.1,
                home_lat=32.9448,
                home_lon=-112.6974,
                point_count=1620,
                source="dji_txt",
                notes="Re-scan of original array to verify Q1 repairs. Results pending client review.",
                tags=["thermal", "solar", "Q2"],
            ),
            # M5 — Warehouse Mapping: 2 flights
            Flight(
                id=_F10,
                name="Warehouse A & B Rooftop Grid",
                aircraft_id=ac_id_map.get(_AC3, _AC3),
                pilot_id=_P2,
                drone_model="DJI Mavic 3 Enterprise",
                drone_name="Mavic-3E Bravo",
                drone_serial="1ZXME3G0020003",
                start_time=_dt_days_ago(7, hour=8, minute=30),
                duration_secs=2280.0,
                total_distance=3750.0,
                max_altitude=61.0,
                max_speed=8.7,
                home_lat=32.8600,
                home_lon=-97.0105,
                point_count=1368,
                source="litchi_csv",
                notes="Warehouses A and B complete. 80% front overlap, 75% side overlap achieved.",
                tags=["mapping", "rooftop", "orthomosaic"],
            ),
            Flight(
                id=_F11,
                name="Warehouse C Rooftop Grid",
                aircraft_id=ac_id_map.get(_AC3, _AC3),
                pilot_id=_P2,
                drone_model="DJI Mavic 3 Enterprise",
                drone_name="Mavic-3E Bravo",
                drone_serial="1ZXME3G0020003",
                start_time=_dt_days_ago(7, hour=10, minute=45),
                duration_secs=1320.0,
                total_distance=2100.0,
                max_altitude=61.0,
                max_speed=8.4,
                home_lat=32.8595,
                home_lon=-97.0090,
                point_count=792,
                source="litchi_csv",
                notes="Warehouse C complete. HVAC unit positions documented.",
                tags=["mapping", "rooftop", "orthomosaic"],
            ),
            # M6 — City Hall Photography: 1 flight
            Flight(
                id=_F12,
                name="City Hall Aerial Promo Shoot",
                aircraft_id=ac_id_map.get(_AC3, _AC3),
                pilot_id=_P2,
                drone_model="DJI Mavic 3 Enterprise",
                drone_name="Mavic-3E Bravo",
                drone_serial="1ZXME3G0020003",
                start_time=_dt_days_ago(45, hour=8, minute=0),
                duration_secs=1080.0,
                total_distance=1450.0,
                max_altitude=107.0,
                max_speed=7.2,
                home_lat=39.7990,
                home_lon=-89.6443,
                point_count=648,
                source="dji_txt",
                notes="Golden hour photography. 47 high-resolution stills delivered to client.",
                tags=["photography", "civic", "promo"],
            ),
        ]

        for f in flights:
            db.add(f)

        await db.flush()
        logger.info("demo_seed: %d flights flushed.", len(flights))

        # ── MissionFlight join records ────────────────────────────────────────
        logger.info("demo_seed: creating mission-flight links...")

        mission_flight_map = [
            # (mission_id, flight_id, aircraft_id) — use ac_id_map for resolved IDs
            (_M1, _F1,  ac_id_map.get(_AC1, _AC1)),
            (_M1, _F2,  ac_id_map.get(_AC1, _AC1)),
            (_M1, _F3,  ac_id_map.get(_AC1, _AC1)),
            (_M2, _F4,  ac_id_map.get(_AC3, _AC3)),
            (_M2, _F5,  ac_id_map.get(_AC3, _AC3)),
            (_M3, _F6,  ac_id_map.get(_AC2, _AC2)),
            (_M3, _F7,  ac_id_map.get(_AC2, _AC2)),
            (_M4, _F8,  ac_id_map.get(_AC1, _AC1)),
            (_M4, _F9,  ac_id_map.get(_AC1, _AC1)),
            (_M5, _F10, ac_id_map.get(_AC3, _AC3)),
            (_M5, _F11, ac_id_map.get(_AC3, _AC3)),
            (_M6, _F12, ac_id_map.get(_AC3, _AC3)),
        ]

        for mission_id, flight_id, aircraft_id in mission_flight_map:
            db.add(MissionFlight(
                id=uuid.uuid4(),
                mission_id=mission_id,
                flight_id=flight_id,
                aircraft_id=aircraft_id,
            ))

        await db.flush()
        logger.info("demo_seed: mission-flight links flushed.")

        # ── Maintenance Schedules ─────────────────────────────────────────────
        logger.info("demo_seed: inserting maintenance schedules...")

        maintenance_schedules = [
            MaintenanceSchedule(
                id=uuid.UUID("e1000000-de00-0000-0000-000000000001"),
                aircraft_id=ac_id_map.get(_AC1, _AC1),
                maintenance_type="Propeller Inspection & Replacement",
                interval_hours=50.0,
                interval_days=None,
                last_performed=_days_ago(30),
                description="Inspect all 4 propellers for cracks, chips, and balance issues. Replace any defective blade sets.",
            ),
            MaintenanceSchedule(
                id=uuid.UUID("e1000000-de00-0000-0000-000000000002"),
                aircraft_id=ac_id_map.get(_AC1, _AC1),
                maintenance_type="Gimbal & Camera Calibration",
                interval_hours=100.0,
                interval_days=90,
                last_performed=_days_ago(55),
                description="Full gimbal calibration, lens cleaning, thermal sensor verification, and IMU re-calibration.",
            ),
            MaintenanceSchedule(
                id=uuid.UUID("e1000000-de00-0000-0000-000000000003"),
                aircraft_id=ac_id_map.get(_AC2, _AC2),
                maintenance_type="Propeller Inspection & Replacement",
                interval_hours=50.0,
                interval_days=None,
                last_performed=_days_ago(18),
                description="Inspect all 4 propellers for cracks, chips, and balance issues. Replace any defective blade sets.",
            ),
            MaintenanceSchedule(
                id=uuid.UUID("e1000000-de00-0000-0000-000000000004"),
                aircraft_id=ac_id_map.get(_AC2, _AC2),
                maintenance_type="Firmware Update",
                interval_hours=None,
                interval_days=60,
                last_performed=_days_ago(10),
                description="Check DJI Pilot 2 app and aircraft firmware for available updates. Apply and verify post-update stability.",
            ),
            MaintenanceSchedule(
                id=uuid.UUID("e1000000-de00-0000-0000-000000000005"),
                aircraft_id=ac_id_map.get(_AC3, _AC3),
                maintenance_type="Motor & Frame Inspection",
                interval_hours=75.0,
                interval_days=None,
                last_performed=_days_ago(42),
                description="Inspect motor mounts, arm fold hinges, and frame for stress cracks or loose fasteners. Clean motor cans.",
            ),
            MaintenanceSchedule(
                id=uuid.UUID("e1000000-de00-0000-0000-000000000006"),
                aircraft_id=ac_id_map.get(_AC3, _AC3),
                maintenance_type="Battery Storage & Health Check",
                interval_hours=None,
                interval_days=30,
                last_performed=_days_ago(8),
                description="Full charge cycle, capacity test, and storage charge (50–60%). Retire any cell below 80% rated capacity.",
            ),
        ]

        for ms in maintenance_schedules:
            db.add(ms)

        await db.flush()
        logger.info("demo_seed: maintenance schedules flushed.")

        # ── Commit ────────────────────────────────────────────────────────────
        await db.commit()
        logger.info(
            "demo_seed: complete — %d aircraft, %d pilots, %d customers, "
            "%d missions, %d flights, %d maintenance schedules seeded.",
            len(aircraft_list),
            2,  # pilots
            4,  # customers
            6,  # missions
            len(flights),
            len(maintenance_schedules),
        )

    except Exception as exc:
        await db.rollback()
        logger.error(
            "demo_seed: FAILED with error — rolling back all changes. Error: %s",
            exc,
            exc_info=True,
        )
        raise
