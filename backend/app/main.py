import logging
import os
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.config import settings
from app.database import Base, async_session, engine, get_db
import app.models  # noqa: F401 — ensure all models registered with Base before create_all
from app.routers import auth, customers, aircraft, missions, flights, maps, reports, invoices, rate_templates, llm, system_settings, financials, weather, intake, flight_library, batteries, maintenance, backup, device_keys

# Configure root logger for the app
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("doc")


def _add_missing_columns(conn):
    """Add columns and enum values that create_all won't add to existing tables.

    NOTE: This is a synchronous function — it runs via conn.run_sync().
    Do NOT make this async or the body will never execute.
    """
    import logging
    from sqlalchemy import text, inspect as sa_inspect

    logger = logging.getLogger("doc.migrations")

    try:
        inspector = sa_inspect(conn)

        # --- Sync PostgreSQL enum types with Python enum values ---
        # create_all creates enum types once but never adds new values.
        # This causes INSERT failures when new Python enum members are used.
        from app.models.mission import MissionType, MissionStatus
        from app.models.invoice import LineItemCategory

        pg_enum_sync = {
            "missiontype": [e.value for e in MissionType],
            "missionstatus": [e.value for e in MissionStatus],
            "lineitemcategory": [e.value for e in LineItemCategory],
        }

        for enum_name, expected_values in pg_enum_sync.items():
            # Get current values in the PostgreSQL enum type
            result = conn.execute(
                text("SELECT enumlabel FROM pg_enum JOIN pg_type ON pg_enum.enumtypid = pg_type.oid WHERE pg_type.typname = :name"),
                {"name": enum_name},
            )
            existing_values = {row[0] for row in result}

            if not existing_values:
                # Enum type doesn't exist yet — create_all will handle it
                continue

            for val in expected_values:
                if val not in existing_values:
                    logger.info("Adding enum value '%s' to PostgreSQL type '%s'", val, enum_name)
                    # ALTER TYPE ... ADD VALUE cannot run inside a transaction on older PG,
                    # but on PG 12+ it works inside a transaction block.
                    conn.execute(text(f"ALTER TYPE {enum_name} ADD VALUE IF NOT EXISTS '{val}'"))

        # --- Add missing columns ---
        migrations = {
            "reports": [
                ("include_download_link", "ALTER TABLE reports ADD COLUMN include_download_link BOOLEAN DEFAULT FALSE"),
            ],
            "missions": [
                ("unas_folder_path", "ALTER TABLE missions ADD COLUMN unas_folder_path VARCHAR(500)"),
                ("download_link_url", "ALTER TABLE missions ADD COLUMN download_link_url VARCHAR(1000)"),
                ("download_link_expires_at", "ALTER TABLE missions ADD COLUMN download_link_expires_at TIMESTAMP"),
            ],
            "customers": [
                ("intake_token", "ALTER TABLE customers ADD COLUMN intake_token VARCHAR(64) UNIQUE"),
                ("intake_token_expires_at", "ALTER TABLE customers ADD COLUMN intake_token_expires_at TIMESTAMP"),
                ("intake_completed_at", "ALTER TABLE customers ADD COLUMN intake_completed_at TIMESTAMP"),
                ("tos_signed", "ALTER TABLE customers ADD COLUMN tos_signed BOOLEAN DEFAULT FALSE"),
                ("tos_signed_at", "ALTER TABLE customers ADD COLUMN tos_signed_at TIMESTAMP"),
                ("signature_data", "ALTER TABLE customers ADD COLUMN signature_data TEXT"),
                ("tos_pdf_path", "ALTER TABLE customers ADD COLUMN tos_pdf_path VARCHAR(500)"),
                ("city", "ALTER TABLE customers ADD COLUMN city VARCHAR(255)"),
                ("state", "ALTER TABLE customers ADD COLUMN state VARCHAR(100)"),
                ("zip_code", "ALTER TABLE customers ADD COLUMN zip_code VARCHAR(20)"),
            ],
            "mission_flights": [
                ("flight_id", "ALTER TABLE mission_flights ADD COLUMN flight_id UUID REFERENCES flights(id) ON DELETE SET NULL"),
            ],
            "flights": [
                ("drone_name", "ALTER TABLE flights ADD COLUMN drone_name VARCHAR(255)"),
            ],
            "batteries": [
                ("name", "ALTER TABLE batteries ADD COLUMN name VARCHAR(255)"),
            ],
        }

        # Make opendronelog_flight_id nullable for existing tables (new flights use flight_id)
        try:
            conn.execute(text("ALTER TABLE mission_flights ALTER COLUMN opendronelog_flight_id DROP NOT NULL"))
        except Exception:
            pass  # already nullable or column doesn't exist

        for table, columns in migrations.items():
            if not inspector.has_table(table):
                continue
            existing = {c["name"] for c in inspector.get_columns(table)}
            for col_name, alter_sql in columns:
                if col_name not in existing:
                    logger.info("Adding column %s.%s", table, col_name)
                    conn.execute(text(alter_sql))

        logger.info("Column migration check complete")
    except Exception as exc:
        logger.error("Column migration failed: %s", exc)
        raise


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Warn about insecure default credentials
    if settings.jwt_secret_key == "changeme_generate_a_random_secret":
        logger.warning("SECURITY: JWT_SECRET_KEY is using the default value — change it in production!")
    if settings.admin_password == "changeme_in_production":
        logger.warning("SECURITY: ADMIN_PASSWORD is using the default value — change it in production!")

    # Create tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_add_missing_columns)

    # Seed data
    from app.seed import seed_database
    async with async_session() as session:
        await seed_database(session)

    # Ensure upload/report directories exist
    os.makedirs(settings.upload_dir, exist_ok=True)
    os.makedirs(settings.reports_dir, exist_ok=True)

    yield

    await engine.dispose()


limiter = Limiter(key_func=get_remote_address)

app = FastAPI(
    title="D.O.C — Drone Operations Command",
    description="Mission management, flight data, and after-action reporting for drone operations",
    version="2.21.1",
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS - restrict to configured frontend URL
_cors_origins = [settings.frontend_url.rstrip("/")]
# Also allow localhost for local development
if not any("localhost" in o for o in _cors_origins):
    _cors_origins.append("http://localhost:3080")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Device-Api-Key"],
)

# Static files for aircraft images
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

# Mount upload directory for serving images
if os.path.exists(settings.upload_dir):
    app.mount("/uploads", StaticFiles(directory=settings.upload_dir), name="uploads")

# Register routers
app.include_router(auth.router)
app.include_router(customers.router)
app.include_router(aircraft.router)
app.include_router(missions.router)
app.include_router(flights.router)
app.include_router(maps.router)
app.include_router(reports.router)
app.include_router(invoices.router)
app.include_router(rate_templates.router)
app.include_router(llm.router)
app.include_router(system_settings.router)
app.include_router(financials.router)
app.include_router(weather.router)
app.include_router(intake.router)
app.include_router(flight_library.router)
app.include_router(batteries.router)
app.include_router(maintenance.router)
app.include_router(backup.router)
app.include_router(device_keys.router)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log every request with timing — critical for diagnosing hangs."""
    start = time.perf_counter()
    method = request.method
    path = request.url.path
    logger.info("REQ %s %s", method, path)
    try:
        response = await call_next(request)
        elapsed = time.perf_counter() - start
        level = logging.WARNING if elapsed > 5.0 else logging.INFO
        logger.log(level, "RES %s %s %s %.2fs", method, path, response.status_code, elapsed)
        return response
    except Exception as exc:
        elapsed = time.perf_counter() - start
        logger.error("ERR %s %s failed after %.2fs: %s", method, path, elapsed, exc)
        raise


@app.get("/api/health")
async def health_check():
    return {"status": "healthy", "service": "D.O.C — Drone Operations Command"}


@app.get("/api/branding")
async def get_public_branding(db: AsyncSession = Depends(get_db)):
    """Public endpoint: returns branding settings (no auth required)."""
    from app.models.system_settings import SystemSetting
    from app.routers.system_settings import BRANDING_KEYS, BRANDING_DEFAULTS

    result = await db.execute(
        select(SystemSetting).where(SystemSetting.key.in_(BRANDING_KEYS))
    )
    rows = {r.key: r.value for r in result.scalars().all()}
    return {key: rows.get(key, BRANDING_DEFAULTS.get(key, "")) for key in BRANDING_KEYS}
