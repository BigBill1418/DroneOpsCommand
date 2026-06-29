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
from app.database import async_session, engine, get_db
import app.models  # noqa: F401 — register all models on Base.metadata (Alembic + legacy helpers read it)
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
                # ADR-0015 — runtime audience-leak gate (soft block). Both additive
                # with safe defaults so existing rows (and any future path that
                # writes a report without going through generation) read cleanly.
                # Failover-safe per CLAUDE.md §Failover Guard.
                ("has_audience_leak",     "ALTER TABLE reports ADD COLUMN has_audience_leak BOOLEAN NOT NULL DEFAULT FALSE"),
                ("audience_leak_details", "ALTER TABLE reports ADD COLUMN audience_leak_details JSONB NOT NULL DEFAULT '[]'::jsonb"),
            ],
            "missions": [
                ("unas_folder_path", "ALTER TABLE missions ADD COLUMN unas_folder_path VARCHAR(500)"),
                ("download_link_url", "ALTER TABLE missions ADD COLUMN download_link_url VARCHAR(1000)"),
                ("download_link_expires_at", "ALTER TABLE missions ADD COLUMN download_link_expires_at TIMESTAMP"),
                ("client_notes", "ALTER TABLE missions ADD COLUMN client_notes TEXT"),
                # ADR-0016 — lead-source attribution (answers "how much
                # job revenue came from the website"). Plain VARCHAR, NOT
                # a PG enum, so this is a single additive nullable ALTER
                # with no CREATE TYPE step. Existing rows read as NULL
                # ("origin unknown"). Failover-safe per CLAUDE.md
                # §Failover Guard — standby promotion runs the same
                # idempotent ALTER.
                ("source",     "ALTER TABLE missions ADD COLUMN source VARCHAR(50)"),
                ("source_ref", "ALTER TABLE missions ADD COLUMN source_ref VARCHAR(255)"),
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
                # Dunning / payment-reminder tracking (2026-05-24).
                # Nullable, no default — safe additive ALTER on a populated table.
                ("billed_at",            "ALTER TABLE invoices ADD COLUMN billed_at TIMESTAMP"),
                ("reminder_sent_at",     "ALTER TABLE invoices ADD COLUMN reminder_sent_at TIMESTAMP"),
                ("final_notice_sent_at", "ALTER TABLE invoices ADD COLUMN final_notice_sent_at TIMESTAMP"),
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


def _create_hot_indexes(conn):
    """Create hot-path indexes idempotently (ADR-0021).

    NOTE: synchronous — runs via ``conn.run_sync()`` inside the same
    primary-only guarded block as ``_add_missing_columns``. Do NOT make
    this async or the body will never execute.

    Each index is ``CREATE INDEX IF NOT EXISTS`` so a re-run on a DB that
    already has it is a no-op. The guard in the lifespan ensures this only
    runs on a writable primary — the created indexes replicate to the
    CHAD-HQ failback standby via WAL, which is exactly why this is
    failover-safe (the standby never issues the DDL itself; it receives
    the index through replication).

    Trade-off — plain ``CREATE INDEX`` (NOT ``CONCURRENTLY``):
    A non-concurrent ``CREATE INDEX`` takes a ``SHARE`` lock that blocks
    writes (not reads) on the target table for the build duration. At the
    current table sizes (single-operator fleet; thousands of rows, not
    millions) each build is sub-second, so the brief write-lock is
    acceptable and lets us run inside the existing transactional startup
    block alongside ``create_all``/``_add_missing_columns``.
    ``CREATE INDEX CONCURRENTLY`` would avoid the write-lock but CANNOT
    run inside a transaction block and cannot be combined with other DDL
    in one ``engine.begin()`` — it would require its own autocommit
    connection and leaves an ``INVALID`` index behind on failure that must
    be dropped manually. Revisit (move to CONCURRENTLY, or to Alembic —
    see ADR-0021) if any of these tables grows past ~1M rows.

    Indexes (each justified against a real query pattern — see ADR-0021):
      - mission_flights.mission_id : Mission.flights selectin + business
        signals join (models/mission.py, business_signals.py:107). FK col;
        PG does NOT auto-index FKs.
      - flights.aircraft_id        : GROUP BY in maintenance dashboard
        /status (maintenance.py:505). FK col.
      - customers.email            : client-portal login lookup
        (client_portal.py:159). Plain column, login-path full scan.
      - line_items.invoice_id      : Invoice.line_items selectin on every
        invoice load (models/invoice.py:97). FK col.
    """
    import logging
    from sqlalchemy import text

    logger = logging.getLogger("doc.migrations")

    hot_indexes = (
        ("ix_mission_flights_mission_id", "mission_flights", "mission_id"),
        ("ix_flights_aircraft_id", "flights", "aircraft_id"),
        ("ix_customers_email", "customers", "email"),
        ("ix_line_items_invoice_id", "line_items", "invoice_id"),
    )

    for index_name, table, column in hot_indexes:
        try:
            logger.info("Ensuring index %s on %s(%s)", index_name, table, column)
            conn.execute(
                text(f"CREATE INDEX IF NOT EXISTS {index_name} ON {table} ({column})")
            )
        except Exception as exc:
            # Best-effort per-index: a missing table (fresh DB mid-create_all
            # ordering, or a table that legitimately doesn't exist yet) must
            # not abort the whole startup. create_all + _add_missing_columns
            # ran first in the same block, so the table normally exists.
            logger.warning("Could not create index %s on %s(%s): %s", index_name, table, column, exc)

    logger.info("Hot-path index check complete")


async def _is_in_recovery() -> bool:
    """Return True if the connected PostgreSQL is a read-only standby.

    Runs ``SELECT pg_is_in_recovery()`` on a fresh connection. On a
    promoted primary this returns ``False``; on a streaming standby (or a
    node still replaying WAL after a crash) it returns ``True``.

    ADR-0021 — this is the failover guard. If the backend boots while its
    ``DATABASE_URL`` points at a standby/recovering node (mid-failover or
    misconfiguration), the startup DDL/seed/backfill block would raise
    *"cannot execute … in a read-only transaction"* and crash-loop the
    container during the exact window a customer-facing outage is least
    acceptable. By detecting recovery first and skipping all writes, the
    backend instead comes up serving READ traffic against the standby.

    Promotion behaviour (documented, intentional): if this node is later
    promoted to primary, this already-running backend will NOT
    retroactively run the skipped DDL/seed — the guard is evaluated once
    at startup. A container restart after promotion (which the blue-green
    deploy flow performs, and which an operator can trigger) re-evaluates
    the guard, sees ``pg_is_in_recovery() = false``, and runs the schema
    sync normally. Restart-after-promotion is the accepted recovery path.

    Fail-safe: if the probe itself errors (driver hiccup, transient
    network), we return ``False`` (assume primary) so a healthy primary is
    never wrongly treated as a standby and left un-migrated. A genuine
    standby would have already failed ``_wait_for_db``'s ``SELECT 1`` if
    truly unreachable; a reachable standby answers ``pg_is_in_recovery()``
    cleanly.
    """
    from sqlalchemy import text

    try:
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT pg_is_in_recovery()"))
            return bool(result.scalar())
    except Exception as exc:
        logger.warning(
            "STARTUP: pg_is_in_recovery() probe failed (%s) — assuming primary, "
            "schema sync will proceed", exc,
        )
        return False


async def _run_startup_schema_and_seed():
    """Run all schema DDL, seed, and backfill writes (primary-only).

    ADR-0021 — extracted verbatim from the lifespan so the
    ``pg_is_in_recovery()`` guard can skip the ENTIRE write block in one
    place, and so the skip/run decision is unit-testable. Every statement
    in here mutates the database and therefore MUST NOT run against a
    read-only standby.

    ADR-0022 — the schema step is now a real **Alembic** migration run
    (``run_migrations_sync``) instead of the legacy ``create_all`` +
    ``_add_missing_columns`` + ``_create_hot_indexes`` triple. The legacy
    helpers are RETAINED (imported by the baseline migration 0001, which
    reproduces the exact pre-Alembic schema) but no longer drive startup.
    The migration runner is SYNCHRONOUS (psycopg2) and is dispatched to a
    thread via ``run_in_executor`` so it never blocks the event loop — the
    same house discipline applied to backup/Stripe/PIL offloads. It detects
    a brownfield prod DB (schema present, no ``alembic_version``) and stamps
    the baseline before upgrading; a fresh DB builds from 0001. See ADR-0022.
    """
    # Run Alembic migrations to head (ADR-0022). Synchronous Alembic +
    # psycopg2 → offload to a thread so the async event loop stays free.
    from app.db_migrations import run_migrations_sync
    loop = asyncio.get_running_loop()
    action = await loop.run_in_executor(None, run_migrations_sync)
    logger.info("STARTUP: Alembic migrations applied (%s) — ADR-0022", action)

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

    # ── Failover guard (ADR-0021) ────────────────────────────────────────
    # If the connected Postgres is a read-only standby (mid-failover or a
    # misconfigured DATABASE_URL), SKIP every schema-DDL / seed / backfill
    # write and come up serving READ traffic only. Running create_all /
    # ALTER / seed against a standby raises "cannot execute … in a
    # read-only transaction" and crash-loops the container during the exact
    # window a customer-facing outage is least acceptable. See ADR-0021 and
    # `_is_in_recovery` for the documented promotion behaviour
    # (restart-after-promotion re-runs the sync).
    if await _is_in_recovery():
        logger.warning(
            "STARTUP: PostgreSQL is in recovery (read-only standby) — "
            "SKIPPING all schema DDL, seed, and backfill. Serving READ "
            "traffic only. Restart this backend after the DB is promoted "
            "to primary to run the schema sync (ADR-0021)."
        )
    else:
        logger.info("STARTUP: PostgreSQL is a writable primary — running schema sync + seed (ADR-0021)")
        await _run_startup_schema_and_seed()

    # ── The remaining startup steps below are filesystem-only (no DB
    # writes) and run regardless of recovery state so a read-replica
    # backend still serves bundled assets and has its upload dirs. ───────

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

    yield

    await engine.dispose()


limiter = Limiter(key_func=get_remote_address)

# Multipart SPOOL threshold — NOT a size cap. Starlette backs each file part
# with SpooledTemporaryFile(max_size=max_file_size): parts beyond it roll over
# to a disk temp file and are never rejected (formparsers.py:204). v2.39.3 set
# this to 200 MB believing it was a hard limit, which silently pinned every
# upload fully in RAM and OOM-killed the backend (1 GiB cgroup) during 45 MB
# DJI mission-image uploads on 2026-06-11. Keep it small so big uploads spool
# to disk; per-route size caps live in the routers (e.g. missions.py 60 MB).
MultiPartParser.max_file_size = 4 * 1024 * 1024  # 4 MB spool-to-disk threshold
logger.info("MultiPartParser spool threshold set to 4 MB (large uploads spool to disk)")

app = FastAPI(
    title="D.O.C — Drone Operations Command",
    description="Self-hosted mission management, flight log analysis, AI report generation, invoicing, telemetry visualization, and real-time airspace monitoring for commercial drone operators.",
    version="2.75.0",
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


async def _probe_stripe_cached(db: AsyncSession) -> tuple[str, str | None]:
    """Probe Stripe connectivity with a 30s TTL.

    Stripe rate-limits API calls; healthchecks fire every 10s in
    docker-compose.yml. Without a cache we'd burn 6 API calls/min.
    Returns (status, error_or_none). status ∈ {"ok", "unconfigured", "error"}.

    v2.67.4 (Tier 2 A7) — read the secret from `system_settings` (the
    canonical store, set live via Settings UI / DB row) with `settings.stripe_secret_key`
    (env-only fallback) as a backstop. Previously this probe checked
    only env, so even after the operator pasted a live key into the
    Settings table the health endpoint kept reporting "unconfigured" —
    misleading anyone debugging.
    """
    now = time.monotonic()
    if now - _HEALTH_CACHE["checked_at"] < _HEALTH_STRIPE_TTL_SECONDS \
            and _HEALTH_CACHE["stripe_status"] is not None:
        return _HEALTH_CACHE["stripe_status"], _HEALTH_CACHE["stripe_error"]

    # Resolve the secret: system_settings DB row first, then env fallback.
    secret_key: str | None = None
    try:
        from app.services.stripe_service import get_stripe_settings
        cfg = await get_stripe_settings(db)
        secret_key = cfg.get("stripe_secret_key") or None
    except Exception:
        # If the lookup itself fails (DB blip, Stripe service import error),
        # don't crash the probe — fall through to env.
        secret_key = None
    if not secret_key:
        secret_key = settings.stripe_secret_key or None

    if not secret_key:
        _HEALTH_CACHE.update({
            "checked_at": now,
            "stripe_status": "unconfigured",
            "stripe_error": None,
        })
        return "unconfigured", None

    try:
        import stripe as _stripe
        _stripe.api_key = secret_key
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

    Returns 200 + `{"status":"healthy", ...}` when DB + Redis are reachable.
    Returns 503 + `{"status":"degraded", ...}` on a **DB or Redis** probe
    failure so Docker / NOC / Watchtower see an explicit unhealthy signal
    (see `docker-compose.yml` healthcheck — `curl -sf` treats 5xx as fail).

    ADR-0022 (audit P3-1): Stripe is probed and reported in the body for
    observability but is EXCLUDED from the 503 gate — a third-party Stripe
    outage must not restart a healthy API. Only DB + Redis gate container
    health.
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

    # Stripe (cached, only if configured).
    #
    # ADR-0022 (audit P3-1) — Stripe status is reported in the body for
    # observability but does NOT drive the `degraded` flag / 503. Only DB +
    # Redis gate CONTAINER health. Coupling liveness to Stripe meant a
    # third-party Stripe outage or a bad API key returned 503 → the Docker
    # healthcheck (`curl -sf` treats 5xx as fail) marked the container
    # unhealthy → `restart: unless-stopped` recreated a perfectly-serving
    # API because *Stripe* was down. A payment provider must never be able
    # to restart the API.
    stripe_status, stripe_err = await _probe_stripe_cached(db)
    body["stripe"] = stripe_status
    if stripe_status == "error":
        body["stripe_error"] = stripe_err

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
