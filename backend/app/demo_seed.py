"""Demo data seeder — populates the database with realistic sample data.

Called on startup when DEMO_MODE=true. Idempotent: checks for existing
demo data before inserting.
"""

import logging
import uuid
from datetime import datetime, timedelta, date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.aircraft import Aircraft
from app.models.customer import Customer
from app.models.flight import Flight
from app.models.mission import Mission, MissionType, MissionStatus
from app.models.pilot import Pilot

logger = logging.getLogger("doc.demo")


async def seed_demo_data(db: AsyncSession) -> None:
    """Seed realistic sample data for the demo instance."""

    # Idempotency check — if demo customer exists, skip
    result = await db.execute(
        select(Customer).where(Customer.name == "Demo Solar Co.")
    )
    if result.scalar_one_or_none():
        logger.info("Demo data already seeded — skipping")
        return

    logger.info("Seeding demo data...")
    now = datetime.utcnow()
    today = date.today()

    # ── Aircraft ──────────────────────────────────────────────────────
    ac_m30t = Aircraft(
        id=uuid.uuid4(),
        model_name="Matrice 30T",
        manufacturer="DJI",
        serial_number="1ZNBJ4R00C0000",
        specs={
            "max_flight_time": "41 min",
            "max_speed": "23 m/s",
            "camera": "Wide + Zoom + Thermal",
            "weight": "3770g",
            "ip_rating": "IP55",
        },
    )
    ac_m4td = Aircraft(
        id=uuid.uuid4(),
        model_name="Matrice 4TD",
        manufacturer="DJI",
        serial_number="5YNCK8D00D0000",
        specs={
            "max_flight_time": "38 min",
            "max_speed": "21 m/s",
            "camera": "Wide + Telephoto + Thermal",
            "weight": "1650g",
            "ip_rating": "IP54",
        },
    )
    ac_m3e = Aircraft(
        id=uuid.uuid4(),
        model_name="Mavic 3 Enterprise",
        manufacturer="DJI",
        serial_number="3LNCM6E00B0000",
        specs={
            "max_flight_time": "45 min",
            "max_speed": "21 m/s",
            "camera": "4/3 CMOS + Telephoto",
            "weight": "915g",
            "ip_rating": "IP43",
        },
    )
    db.add_all([ac_m30t, ac_m4td, ac_m3e])

    # ── Pilots ────────────────────────────────────────────────────────
    pilot_mike = Pilot(
        id=str(uuid.uuid4()),
        name="Mike Barnard",
        email="mike@barnardhq.com",
        phone="(555) 123-4567",
        faa_certificate_number="4567890",
        faa_certificate_expiry=datetime(2027, 6, 15),
        is_active=True,
        notes="Lead pilot. Part 107 + Part 107 waiver for night ops.",
    )
    pilot_sarah = Pilot(
        id=str(uuid.uuid4()),
        name="Sarah Chen",
        email="sarah@barnardhq.com",
        phone="(555) 234-5678",
        faa_certificate_number="5678901",
        faa_certificate_expiry=datetime(2026, 11, 30),
        is_active=True,
        notes="Thermal inspection specialist.",
    )
    db.add_all([pilot_mike, pilot_sarah])

    # ── Customers ─────────────────────────────────────────────────────
    cust_solar = Customer(
        id=uuid.uuid4(),
        name="Demo Solar Co.",
        company="Demo Solar Co.",
        email="ops@demosolar.example.com",
        phone="(555) 444-1000",
        address="1200 Solar Panel Way",
        city="Phoenix",
        state="AZ",
        zip_code="85001",
        notes="Solar farm thermal inspection contract — quarterly.",
    )
    cust_acme = Customer(
        id=uuid.uuid4(),
        name="Acme Construction",
        company="Acme Construction LLC",
        email="pm@acmeconstruction.example.com",
        phone="(555) 444-2000",
        address="88 Builder Blvd",
        city="Austin",
        state="TX",
        zip_code="73301",
        notes="Monthly construction progress mapping.",
    )
    cust_city = Customer(
        id=uuid.uuid4(),
        name="City of Springfield",
        company="City of Springfield",
        email="pubworks@springfield.example.gov",
        phone="(555) 444-3000",
        address="100 Main St",
        city="Springfield",
        state="IL",
        zip_code="62701",
        notes="Bridge and infrastructure inspection contract.",
    )
    cust_barnard = Customer(
        id=uuid.uuid4(),
        name="Barnard Aerial Services",
        company="Barnard Aerial Services",
        email="info@barnardhq.com",
        phone="(555) 444-4000",
        address="42 Hangar Ln",
        city="Dallas",
        state="TX",
        zip_code="75201",
        notes="Internal — training and equipment testing flights.",
    )
    db.add_all([cust_solar, cust_acme, cust_city, cust_barnard])

    # ── Missions ──────────────────────────────────────────────────────
    missions = [
        Mission(
            id=uuid.uuid4(),
            customer_id=cust_solar.id,
            title="Q1 Solar Farm Thermal Inspection",
            mission_type=MissionType.INSPECTION,
            description="Thermal imaging of 2,400 panels across the north array. Looking for hotspot anomalies, bypass diode failures, and cell degradation.",
            mission_date=today - timedelta(days=15),
            location_name="Phoenix Solar Farm — North Array",
            status=MissionStatus.COMPLETED,
            is_billable=True,
        ),
        Mission(
            id=uuid.uuid4(),
            customer_id=cust_acme.id,
            title="Phase 3 Construction Progress",
            mission_type=MissionType.MAPPING,
            description="Orthomosaic mapping of the phase 3 build area. Client needs updated 3D model for project management board.",
            mission_date=today - timedelta(days=8),
            location_name="Acme Heights Development — Phase 3",
            status=MissionStatus.COMPLETED,
            is_billable=True,
        ),
        Mission(
            id=uuid.uuid4(),
            customer_id=cust_city.id,
            title="MLK Bridge Structural Inspection",
            mission_type=MissionType.INSPECTION,
            description="Close-range visual and thermal inspection of bridge deck, pylons, and expansion joints. FAA Part 107 waiver approved for flight under bridge deck.",
            mission_date=today - timedelta(days=3),
            location_name="MLK Jr. Memorial Bridge — Springfield",
            status=MissionStatus.COMPLETED,
            is_billable=True,
        ),
        Mission(
            id=uuid.uuid4(),
            customer_id=cust_acme.id,
            title="Site Security Perimeter Survey",
            mission_type=MissionType.SECURITY_INVESTIGATIONS,
            description="Perimeter fence-line survey with thermal. Checking for access breaches and unauthorized entry points.",
            mission_date=today - timedelta(days=1),
            location_name="Acme Heights Development — Perimeter",
            status=MissionStatus.DRAFT,
            is_billable=True,
        ),
        Mission(
            id=uuid.uuid4(),
            customer_id=cust_barnard.id,
            title="New Pilot Training Flight",
            mission_type=MissionType.OTHER,
            description="Proficiency check flights for Sarah. M30T and M4TD familiarization.",
            mission_date=today - timedelta(days=20),
            location_name="Barnard Flight Training Field",
            status=MissionStatus.COMPLETED,
            is_billable=False,
        ),
        Mission(
            id=uuid.uuid4(),
            customer_id=cust_solar.id,
            title="Q2 Solar Farm Pre-Survey",
            mission_type=MissionType.SURVEY,
            description="Pre-survey reconnaissance of the south array expansion area.",
            mission_date=today + timedelta(days=12),
            location_name="Phoenix Solar Farm — South Expansion",
            status=MissionStatus.DRAFT,
            is_billable=True,
        ),
    ]
    db.add_all(missions)

    # ── Flights ───────────────────────────────────────────────────────
    flight_data = [
        # Solar inspection flights
        (missions[0].id, ac_m30t.id, pilot_mike.id, "Matrice 30T", "dji", 2100, 120.5, 3200, today - timedelta(days=15)),
        (missions[0].id, ac_m30t.id, pilot_mike.id, "Matrice 30T", "dji", 1800, 115.0, 2800, today - timedelta(days=15)),
        (missions[0].id, ac_m4td.id, pilot_sarah.id, "Matrice 4TD", "dji", 1950, 100.0, 2400, today - timedelta(days=15)),
        # Construction mapping
        (missions[1].id, ac_m3e.id, pilot_mike.id, "Mavic 3 Enterprise", "litchi", 2400, 200.0, 4500, today - timedelta(days=8)),
        (missions[1].id, ac_m3e.id, pilot_mike.id, "Mavic 3 Enterprise", "litchi", 2100, 200.0, 3800, today - timedelta(days=8)),
        # Bridge inspection
        (missions[2].id, ac_m30t.id, pilot_mike.id, "Matrice 30T", "dji", 1500, 80.0, 1200, today - timedelta(days=3)),
        (missions[2].id, ac_m30t.id, pilot_sarah.id, "Matrice 30T", "dji", 1200, 60.0, 800, today - timedelta(days=3)),
        (missions[2].id, ac_m4td.id, pilot_sarah.id, "Matrice 4TD", "dji", 900, 50.0, 600, today - timedelta(days=3)),
        # Training flights
        (missions[4].id, ac_m30t.id, pilot_sarah.id, "Matrice 30T", "dji", 1800, 100.0, 2000, today - timedelta(days=20)),
        (missions[4].id, ac_m4td.id, pilot_sarah.id, "Matrice 4TD", "dji", 1500, 100.0, 1500, today - timedelta(days=20)),
        (missions[4].id, ac_m3e.id, pilot_sarah.id, "Mavic 3 Enterprise", "dji", 2100, 150.0, 3000, today - timedelta(days=19)),
        # Older standalone flight
        (None, ac_m30t.id, pilot_mike.id, "Matrice 30T", "dji", 3000, 350.0, 5000, today - timedelta(days=45)),
    ]

    for mission_id, aircraft_id, pilot_id, drone_model, source, duration, altitude, distance, fdate in flight_data:
        flight = Flight(
            id=uuid.uuid4(),
            mission_id=mission_id,
            aircraft_id=aircraft_id,
            pilot_id=pilot_id,
            drone_model=drone_model,
            source=source,
            duration_secs=duration,
            max_altitude=altitude,
            distance=distance,
            date=datetime.combine(fdate, datetime.min.time()) + timedelta(hours=9, minutes=30),
        )
        db.add(flight)

    await db.commit()
    logger.info(
        "Demo data seeded: 3 aircraft, 2 pilots, 4 customers, 6 missions, 12 flights"
    )
