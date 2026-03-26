import logging
import os
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.formparsers import MultiPartParser

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
            "aircraft": [
                ("serial_number", "ALTER TABLE aircraft ADD COLUMN serial_number VARCHAR(255)"),
            ],
            "maintenance_records": [
                ("images", "ALTER TABLE maintenance_records ADD COLUMN images JSONB DEFAULT '[]'"),
            ],
            # password_compliant column removed from model in v2.43.0 — column left in DB (harmless)
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

        # --- Widen maintenance_type columns from VARCHAR(100) to TEXT ---
        for table in ("maintenance_records", "maintenance_schedules"):
            if inspector.has_table(table):
                for col in inspector.get_columns(table):
                    if col["name"] == "maintenance_type" and hasattr(col["type"], "length") and col["type"].length:
                        logger.info("Widening %s.maintenance_type to TEXT", table)
                        conn.execute(text(f"ALTER TABLE {table} ALTER COLUMN maintenance_type TYPE TEXT"))

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

    # Post-seed verification: log the admin hash state
    from app.models.user import User
    from app.auth.jwt import verify_password
    async with async_session() as verify_session:
        result = await verify_session.execute(
            select(User).where(User.username == settings.admin_username)
        )
        admin = result.scalar_one_or_none()
        if admin:
            env_matches = verify_password(settings.admin_password, admin.hashed_password)
            logger.info(
                "STARTUP: Admin '%s' hash=%s... env_password_matches=%s",
                admin.username,
                admin.hashed_password[:10] if admin.hashed_password else "EMPTY",
                env_matches,
            )
        else:
            logger.critical("STARTUP: Admin user '%s' NOT FOUND after seed!", settings.admin_username)

    # Ensure upload/report directories exist
    os.makedirs(settings.upload_dir, exist_ok=True)
    os.makedirs(settings.reports_dir, exist_ok=True)

    # Copy bundled default aircraft images to uploads if not already present
    bundled_aircraft_dir = os.path.join(os.path.dirname(__file__), "static", "aircraft")
    if os.path.isdir(bundled_aircraft_dir):
        import shutil
        for fname in os.listdir(bundled_aircraft_dir):
            dest = os.path.join(settings.upload_dir, fname)
            if not os.path.exists(dest):
                try:
                    shutil.copy2(os.path.join(bundled_aircraft_dir, fname), dest)
                except (PermissionError, OSError) as e:
                    logger.warning(
                        "Could not copy default aircraft image %s to uploads: %s "
                        "(will serve from /static/aircraft/ instead)", fname, e
                    )

    # Auto-backfill: link unmatched flights to fleet aircraft
    try:
        from app.models.flight import Flight
        from app.models.aircraft import Aircraft
        from app.routers.flight_library import _match_fleet_aircraft
        async with async_session() as backfill_session:
            result = await backfill_session.execute(
                select(Flight).where(Flight.aircraft_id.is_(None))
            )
            unlinked = result.scalars().all()
            matched = 0
            for flight in unlinked:
                fleet_match = await _match_fleet_aircraft(backfill_session, flight.drone_serial, flight.drone_model)
                if fleet_match:
                    flight.aircraft_id = fleet_match.id
                    flight.drone_model = fleet_match.model_name
                    matched += 1
            if matched > 0:
                await backfill_session.commit()
            logger.info("STARTUP: Aircraft backfill — %d/%d unlinked flights matched to fleet", matched, len(unlinked))
    except Exception as e:
        logger.warning("STARTUP: Aircraft backfill failed: %s", e)

    yield

    await engine.dispose()


limiter = Limiter(key_func=get_remote_address)

# Raise Starlette's default multipart file-size limit (1 MB) so DJI flight logs,
# mission images, and backup restores can upload without being silently rejected.
MultiPartParser.max_file_size = 200 * 1024 * 1024  # 200 MB
logger.info("MultiPartParser max_file_size set to 200 MB")

app = FastAPI(
    title="D.O.C — Drone Operations Command",
    description="Self-hosted mission management, flight log analysis, AI report generation, invoicing, telemetry visualization, and real-time airspace monitoring for commercial drone operators.",
    version="2.49.1",
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS — allow any origin for LAN-only self-hosted deployment.
# All endpoints are behind JWT or device-API-key auth so origin
# restriction adds no real security on a private network.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Fallback route: serve default aircraft SVGs from bundled static if not in uploads
_bundled_aircraft_dir = os.path.join(os.path.dirname(__file__), "static", "aircraft")


@app.get("/uploads/{filename:path}")
async def serve_upload_with_fallback(filename: str):
    """Serve uploaded files, falling back to bundled defaults for aircraft SVGs."""
    import mimetypes
    # Prevent path traversal
    if ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid path")
    # Try the uploads directory first
    upload_path = os.path.join(settings.upload_dir, filename)
    if os.path.isfile(upload_path):
        media_type, _ = mimetypes.guess_type(upload_path)
        if filename.endswith(".svg"):
            media_type = "image/svg+xml"
        return FileResponse(upload_path, media_type=media_type)
    # Fallback: if it's a default aircraft image, serve from bundled static
    if "/" not in filename:
        bundled_path = os.path.join(_bundled_aircraft_dir, filename)
        if os.path.isfile(bundled_path):
            mt = "image/svg+xml" if filename.endswith(".svg") else None
            return FileResponse(bundled_path, media_type=mt)
    raise HTTPException(status_code=404, detail="File not found")


# Static files for aircraft images
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

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
