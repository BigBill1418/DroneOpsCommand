import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pythonjsonlogger import json as json_logger
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
from app.routers import auth, customers, aircraft, missions, flights, maps, reports, invoices, rate_templates, llm, system_settings, financials, weather, intake, flight_library, batteries, maintenance, backup, device_keys, pilots, client_portal, stripe_webhook, business_signals, admin_device_rotation, tos


def _setup_json_logging() -> None:
    """Wire structured-JSON logging on the root logger.

    Phase 5 observability pre-req — replaces the plain
    ``logging.basicConfig(format="%(asctime)s [%(levelname)s] ...")``
    setup with a ``python-json-logger`` handler so every log line Docker
    collects is a parseable JSON object. Alloy's label-based discovery
    then stamps ``service=droneops-api``/``droneops-worker`` + tenant/env
    on the stream at ingest. Non-Docker consumers that grep plaintext
    level prefixes need to migrate to JSON parsing — see ADR.
    """
    handler = logging.StreamHandler()
    formatter = json_logger.JsonFormatter(
        fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
        rename_fields={"asctime": "timestamp", "levelname": "level"},
    )
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)

    # Keep uvicorn.access noise at WARNING — the middleware below logs
    # every request/response with our own structured fields.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


_setup_json_logging()
logger = logging.getLogger("doc")

# Observability bootstrap. Both inits are DSN/endpoint-gated — unset
# env = no-op, so self-hosted single-tenant installs keep working
# without the central plane. Runs AFTER logging setup so the init logs
# are JSON-shaped, BEFORE FastAPI construction so the SDK's integrations
# can hook import paths that routers may trigger.
from app.observability import init_otel, init_sentry, instrument_fastapi  # noqa: E402

init_sentry(service="droneops-api")
init_otel(service="droneops-api")


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
                ("client_notes", "ALTER TABLE missions ADD COLUMN client_notes TEXT"),
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
                ("portal_password_hash", "ALTER TABLE customers ADD COLUMN portal_password_hash VARCHAR(255)"),
                ("portal_password_set_at", "ALTER TABLE customers ADD COLUMN portal_password_set_at TIMESTAMP"),
            ],
            "mission_flights": [
                ("flight_id", "ALTER TABLE mission_flights ADD COLUMN flight_id UUID REFERENCES flights(id) ON DELETE SET NULL"),
            ],
            "flights": [
                ("drone_name", "ALTER TABLE flights ADD COLUMN drone_name VARCHAR(255)"),
                ("pilot_id", "ALTER TABLE flights ADD COLUMN pilot_id VARCHAR(36) REFERENCES pilots(id) ON DELETE SET NULL"),
            ],
            "batteries": [
                ("name", "ALTER TABLE batteries ADD COLUMN name VARCHAR(255)"),
            ],
            "aircraft": [
                ("serial_number", "ALTER TABLE aircraft ADD COLUMN serial_number VARCHAR(255)"),
            ],
            "invoices": [
                ("stripe_payment_intent_id", "ALTER TABLE invoices ADD COLUMN stripe_payment_intent_id VARCHAR(255)"),
                ("stripe_checkout_session_id", "ALTER TABLE invoices ADD COLUMN stripe_checkout_session_id VARCHAR(255)"),
                ("payment_method", "ALTER TABLE invoices ADD COLUMN payment_method VARCHAR(50)"),
                ("paid_at", "ALTER TABLE invoices ADD COLUMN paid_at TIMESTAMP"),
                # ADR-0009 — two-phase deposit + balance billing.
                # All additive with safe defaults; failover-safe (no PK/FK
                # changes; standby promotion runs the same idempotent ALTERs).
                ("deposit_required",            "ALTER TABLE invoices ADD COLUMN deposit_required BOOLEAN NOT NULL DEFAULT FALSE"),
                ("deposit_amount",              "ALTER TABLE invoices ADD COLUMN deposit_amount NUMERIC(10,2) NOT NULL DEFAULT 0"),
                ("deposit_paid",                "ALTER TABLE invoices ADD COLUMN deposit_paid BOOLEAN NOT NULL DEFAULT FALSE"),
                ("deposit_paid_at",             "ALTER TABLE invoices ADD COLUMN deposit_paid_at TIMESTAMP"),
                ("deposit_payment_intent_id",   "ALTER TABLE invoices ADD COLUMN deposit_payment_intent_id VARCHAR(255)"),
                ("deposit_checkout_session_id", "ALTER TABLE invoices ADD COLUMN deposit_checkout_session_id VARCHAR(255)"),
                ("deposit_payment_method",      "ALTER TABLE invoices ADD COLUMN deposit_payment_method VARCHAR(50)"),
            ],
            "maintenance_records": [
                ("images", "ALTER TABLE maintenance_records ADD COLUMN images JSONB DEFAULT '[]'"),
            ],
            # ADR-0003 — zero-touch device API key rotation grace window.
            # Additive only; existing rows have NULLs for both columns which
            # means "no rotation in flight". Failover-safe per repo CLAUDE.md
            # §Failover Guard (no PK / FK / index changes; standby promotion
            # runs the same idempotent ALTER).
            "device_api_keys": [
                ("rotated_to_key_hash",  "ALTER TABLE device_api_keys ADD COLUMN rotated_to_key_hash VARCHAR(64)"),
                ("rotation_grace_until", "ALTER TABLE device_api_keys ADD COLUMN rotation_grace_until TIMESTAMP"),
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

        # ADR-0009 — invoice deposit CHECK constraints (idempotent).
        # Wrapped in DO/EXCEPTION so a re-run on a DB that already has
        # the constraint is a no-op. The application also enforces these
        # in `app/routers/invoices.py:create_invoice` so the DB layer is
        # belt-and-suspenders.
        if inspector.has_table("invoices"):
            invoice_cols = {c["name"] for c in inspector.get_columns("invoices")}
            if {"deposit_amount", "deposit_required"}.issubset(invoice_cols):
                deposit_constraints = (
                    ("deposit_amount_nonneg",
                     "ALTER TABLE invoices ADD CONSTRAINT deposit_amount_nonneg "
                     "CHECK (deposit_amount >= 0)"),
                    ("deposit_amount_le_total",
                     "ALTER TABLE invoices ADD CONSTRAINT deposit_amount_le_total "
                     "CHECK (deposit_amount <= total)"),
                    ("deposit_required_consistent",
                     "ALTER TABLE invoices ADD CONSTRAINT deposit_required_consistent "
                     "CHECK (deposit_required = false OR deposit_amount > 0)"),
                )
                for name, alter in deposit_constraints:
                    conn.execute(text(
                        f"DO $$ BEGIN {alter}; "
                        f"EXCEPTION WHEN duplicate_object THEN NULL; END $$;"
                    ))
                    logger.debug("Ensured CHECK constraint %s on invoices", name)

        logger.info("Column migration check complete")
    except Exception as exc:
        logger.error("Column migration failed: %s", exc)
        raise


async def _wait_for_db(max_retries: int = 10, delay: float = 3.0):
    """Retry DB connection on startup — handles race conditions after restart."""
    from sqlalchemy import text
    for attempt in range(1, max_retries + 1):
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            logger.info("STARTUP: Database connection OK (attempt %d)", attempt)
            return
        except Exception as exc:
            if attempt == max_retries:
                logger.critical("STARTUP: Database unreachable after %d attempts: %s", max_retries, exc)
                raise
            logger.warning("STARTUP: DB not ready (attempt %d/%d): %s — retrying in %.0fs", attempt, max_retries, exc, delay)
            await asyncio.sleep(delay)


async def _wait_for_redis(max_retries: int = 10, delay: float = 3.0):
    """Retry Redis connection on startup."""
    import redis.asyncio as aioredis
    for attempt in range(1, max_retries + 1):
        try:
            r = aioredis.from_url(settings.redis_url)
            await r.ping()
            await r.aclose()
            logger.info("STARTUP: Redis connection OK (attempt %d)", attempt)
            return
        except Exception as exc:
            if attempt == max_retries:
                logger.critical("STARTUP: Redis unreachable after %d attempts: %s", max_retries, exc)
                raise
            logger.warning("STARTUP: Redis not ready (attempt %d/%d): %s — retrying in %.0fs", attempt, max_retries, exc, delay)
            await asyncio.sleep(delay)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Warn about insecure default credentials
    if settings.jwt_secret_key == "changeme_generate_a_random_secret":
        logger.warning("SECURITY: JWT_SECRET_KEY is using the default value — change it in production!")

    # Wait for dependencies to be ready (handles restart race conditions)
    await _wait_for_db()
    await _wait_for_redis()

    # Create tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_add_missing_columns)

    # Seed data
    from app.seed import seed_database
    async with async_session() as session:
        await seed_database(session)

    # Demo mode: seed sample data for the demo instance
    if settings.demo_mode:
        from app.demo_seed import seed_demo_data
        async with async_session() as demo_session:
            await seed_demo_data(demo_session)
        logger.info("STARTUP: Demo data seeded")

    # Post-seed: log setup status (no auto-repair — credentials managed via UI)
    from app.models.user import User
    async with async_session() as verify_session:
        result = await verify_session.execute(select(User))
        user_count = len(result.scalars().all())
        if user_count == 0:
            # Managed instance: auto-create admin from env vars instead of setup wizard
            if settings.managed_instance and settings.admin_username and settings.admin_password:
                from app.auth.jwt import hash_password
                admin = User(
                    username=settings.admin_username,
                    hashed_password=hash_password(settings.admin_password),
                )
                verify_session.add(admin)
                await verify_session.commit()
                logger.info("STARTUP: Managed instance — admin user '%s' created from env vars", settings.admin_username)
            else:
                logger.info("STARTUP: No users in database — setup wizard will appear on first visit")
        else:
            logger.info("STARTUP: %d user(s) in database — login ready", user_count)

    # Ensure upload/report directories exist
    os.makedirs(settings.upload_dir, exist_ok=True)
    os.makedirs(settings.reports_dir, exist_ok=True)

    # Copy bundled default aircraft images into uploads on every boot.
    # v2.63.13: always overwrite so artwork updates ship with the image.
    # User-uploaded images live under uploads/aircraft/<uuid>/ and are
    # never touched here (we only iterate files at the top level of the
    # bundled directory, never subdirectories).
    bundled_aircraft_dir = os.path.join(os.path.dirname(__file__), "static", "aircraft")
    if os.path.isdir(bundled_aircraft_dir):
        import shutil
        for fname in os.listdir(bundled_aircraft_dir):
            src = os.path.join(bundled_aircraft_dir, fname)
            if not os.path.isfile(src):
                continue
            dest = os.path.join(settings.upload_dir, fname)
            try:
                shutil.copy2(src, dest)
            except (PermissionError, OSError) as e:
                logger.warning(
                    "Could not copy default aircraft image %s to uploads: %s "
                    "(will serve from /static/aircraft/ instead)", fname, e
                )

    # Auto-backfill: link UNATTACHED flights to fleet aircraft (Phase 1 only).
    #
    # v2.63.15 (ADR-0007 follow-up): Phase 2 — normalizing `drone_model` on
    # already-linked flights to the canonical fleet `model_name` — was
    # removed from this startup path. It used to run on every container
    # restart and would silently overwrite operator-curated `drone_model`
    # values (e.g. a flight manually attached to a fleet aircraft where
    # the operator left the parsed model string verbatim). The same logic
    # remains available on demand via the manual POST `/api/flight-library/backfill-aircraft`
    # endpoint, which is the right place for "renamed an aircraft, sync
    # all linked flights" workflows.
    try:
        from app.models.flight import Flight
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
            logger.info("STARTUP: Aircraft backfill — %d/%d unlinked matched (Phase 2 normalize moved to manual endpoint)",
                        matched, len(unlinked))
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
    version="2.67.1",
    lifespan=lifespan,
)

# OTel FastAPI auto-instrumentation — no-op unless OTEL endpoint is set.
instrument_fastapi(app)

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

# Demo mode guard — blocks destructive operations in demo instances
if settings.demo_mode:
    from app.middleware.demo_guard import DemoGuardMiddleware
    app.add_middleware(DemoGuardMiddleware)
    logger.info("DEMO MODE enabled — destructive operations are blocked")

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
app.include_router(pilots.router)
app.include_router(client_portal.router)
app.include_router(stripe_webhook.router)
app.include_router(business_signals.router)
app.include_router(admin_device_rotation.router)
app.include_router(tos.router)


# ── Demo status endpoint (no auth required) ───────────────────────────
@app.get("/api/demo/status")
async def demo_status():
    """Public endpoint — tells the frontend whether demo mode is active."""
    return {
        "demo_mode": settings.demo_mode,
        "message": "This is a demo instance of DroneOpsCommand. Some actions are restricted."
        if settings.demo_mode else None,
    }


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


_HEALTH_CACHE: dict = {"checked_at": 0.0, "stripe_status": None, "stripe_error": None}
_HEALTH_STRIPE_TTL_SECONDS = 30.0


async def _probe_stripe_cached() -> tuple[str, str | None]:
    """Probe Stripe connectivity with a 30s TTL.

    Stripe rate-limits API calls; healthchecks fire every 10s in
    docker-compose.yml. Without a cache we'd burn 6 API calls/min.
    Returns (status, error_or_none). status ∈ {"ok", "unconfigured", "error"}.
    """
    now = time.monotonic()
    if now - _HEALTH_CACHE["checked_at"] < _HEALTH_STRIPE_TTL_SECONDS \
            and _HEALTH_CACHE["stripe_status"] is not None:
        return _HEALTH_CACHE["stripe_status"], _HEALTH_CACHE["stripe_error"]

    if not settings.stripe_secret_key:
        _HEALTH_CACHE.update({
            "checked_at": now,
            "stripe_status": "unconfigured",
            "stripe_error": None,
        })
        return "unconfigured", None

    try:
        import stripe as _stripe
        _stripe.api_key = settings.stripe_secret_key
        # Account.retrieve is the cheapest authoritative ping.
        await asyncio.to_thread(_stripe.Account.retrieve)
        _HEALTH_CACHE.update({
            "checked_at": now,
            "stripe_status": "ok",
            "stripe_error": None,
        })
        return "ok", None
    except Exception as exc:
        err = type(exc).__name__
        _HEALTH_CACHE.update({
            "checked_at": now,
            "stripe_status": "error",
            "stripe_error": err,
        })
        return "error", err


@app.get("/api/health")
async def health_check(db: AsyncSession = Depends(get_db)):
    """Real liveness + dependency probe (Fix 7, v2.66.0).

    Probes:
      - DB: `SELECT 1` against the configured PostgreSQL.
      - Redis: `PING`.
      - Stripe: cached `Account.retrieve` (30s TTL) IF a key is configured.

    Returns 200 + `{"status":"healthy", ...}` when everything is reachable.
    Returns 503 + `{"status":"degraded", ...}` on any probe failure so
    Docker / NOC / Watchtower see an explicit unhealthy signal (see
    `docker-compose.yml` healthcheck — `curl -sf` treats 5xx as fail).
    """
    from sqlalchemy import text as sa_text
    import redis.asyncio as aioredis
    from fastapi.responses import JSONResponse

    body: dict[str, object] = {
        "status": "healthy",
        "service": "D.O.C — Drone Operations Command",
    }
    if settings.managed_instance:
        body["managed"] = True
        if settings.client_id:
            body["client_id"] = settings.client_id

    degraded = False

    # DB
    try:
        await db.execute(sa_text("SELECT 1"))
        body["db"] = "ok"
    except Exception as exc:
        body["db"] = "error"
        body["db_error"] = type(exc).__name__
        degraded = True
        logger.error("[HEALTH] DB probe failed: %s", exc)

    # Redis
    try:
        r = aioredis.from_url(settings.redis_url, socket_timeout=2)
        await r.ping()
        await r.aclose()
        body["redis"] = "ok"
    except Exception as exc:
        body["redis"] = "error"
        body["redis_error"] = type(exc).__name__
        degraded = True
        logger.error("[HEALTH] Redis probe failed: %s", exc)

    # Stripe (cached, only if configured)
    stripe_status, stripe_err = await _probe_stripe_cached()
    body["stripe"] = stripe_status
    if stripe_status == "error":
        body["stripe_error"] = stripe_err
        degraded = True

    if degraded:
        body["status"] = "degraded"
        return JSONResponse(status_code=503, content=body)
    return body


@app.get("/health")
async def health_check_root(db: AsyncSession = Depends(get_db)):
    """Top-level /health alias.

    Publicly tunneled clients (stale DroneOpsSync APKs, CF tunnel health probes,
    generic uptime monitors) commonly hit bare ``/health`` rather than the
    ``/api/health`` path that the SPA reserves under ``/api/*``. Without this
    route, nginx/React serves the SPA HTML and any non-browser client chokes
    trying to parse it as JSON (the DroneOpsSync diagnostic log showed
    ``IOException: Use JsonReader.setLenient(true)...`` when this happened
    against a pre-2.34 APK on the operator's DJI RC Pro — 2026-04-24).

    Returns the same payload as ``/api/health`` so the alias is safe to rely on.
    """
    return await health_check(db=db)


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
