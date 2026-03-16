import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.database import Base, async_session, engine
import app.models  # noqa: F401 — ensure all models registered with Base before create_all
from app.routers import auth, customers, aircraft, missions, flights, maps, reports, invoices, rate_templates, llm, system_settings, financials, weather


async def _add_missing_columns(conn):
    """Add columns and enum values that create_all won't add to existing tables."""
    import logging
    from sqlalchemy import text, inspect as sa_inspect

    logger = logging.getLogger("droneops.migrations")

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
        }

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


app = FastAPI(
    title="DroneOpsReport",
    description="Invoicing and after-action reporting tool for drone operations",
    version="1.7.1",
    lifespan=lifespan,
)

# CORS - allow all origins for development, restrict in production
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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


@app.get("/api/health")
async def health_check():
    return {"status": "healthy", "service": "DroneOpsReport"}
