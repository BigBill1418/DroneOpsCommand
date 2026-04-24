import asyncio
import logging
from datetime import datetime, timedelta

from celery import Celery
from celery.schedules import crontab
from celery.signals import after_setup_logger, after_setup_task_logger, heartbeat_sent, worker_ready
from pythonjsonlogger import json as json_logger

from app.config import settings


def _apply_json_formatter(logger_instance: logging.Logger) -> None:
    """Replace every handler's formatter with ``python-json-logger``.

    Celery installs its own stream handlers before our code runs — and
    the ``after_setup_logger`` / ``after_setup_task_logger`` signals
    fire immediately after that happens. We mutate the formatters in
    place so both worker-level and per-task log lines land in Loki as
    JSON, matching the ``droneops-api`` shape.
    """
    formatter = json_logger.JsonFormatter(
        fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
        rename_fields={"asctime": "timestamp", "levelname": "level"},
    )
    for handler in logger_instance.handlers:
        handler.setFormatter(formatter)


@after_setup_logger.connect
def _setup_worker_json_logging(logger=None, **kwargs):  # noqa: ARG001 — Celery signal
    if logger is not None:
        _apply_json_formatter(logger)


@after_setup_task_logger.connect
def _setup_task_json_logging(logger=None, **kwargs):  # noqa: ARG001 — Celery signal
    if logger is not None:
        _apply_json_formatter(logger)


logger = logging.getLogger(__name__)

# Observability bootstrap — runs at worker import time so task execution
# is traced and exceptions are captured. DSN/endpoint gated; no-op if
# SENTRY_DSN / OTEL_EXPORTER_OTLP_ENDPOINT are unset.
from app.observability import init_otel, init_sentry  # noqa: E402

init_sentry(service="droneops-worker")
init_otel(service="droneops-worker")

celery_app = Celery("doc", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
)

# ADR-0002 §5 layer 3 — silent-drift watchdog schedule.
# Runs hourly via the `beat` sidecar service in docker-compose.yml.
# No flag-gating: the check itself is cheap (one SELECT) and Pushover
# is a no-op if PUSHOVER_TOKEN / PUSHOVER_USER_KEY are unset.
celery_app.conf.beat_schedule = {
    "device-silence-watchdog": {
        "task": "check_device_silence",
        "schedule": crontab(minute=17),  # offset from the wall-clock hour
    },
    # ADR-0003 — promote rotated_to_key_hash → key_hash once the grace
    # window expires. Runs every 15 minutes so a finished rotation is
    # cleared from the dual-key auth path within 15 min of the deadline.
    # Beat schedule entries are crontab(minute='*/15') = 0/15/30/45,
    # offset from the silence watchdog (minute=17) to avoid co-firing.
    "device-key-rotation-finalizer": {
        "task": "finalize_key_rotations",
        "schedule": crontab(minute="*/15"),
    },
}


# Redis-backed worker heartbeat — replaces the wasteful
# `celery -A app.tasks.celery_tasks inspect ping` docker healthcheck which
# spawned a fresh Python process every 60s and re-imported the full OTel
# instrumentation chain (measured ~3-5s per check).
#
# Celery emits a `worker_heartbeat` signal on its control loop (every ~2s
# by default). We write a Redis key on each tick; the container
# healthcheck reads the key age via redis-cli. If the worker is alive and
# processing its event loop, the key is fresh. If the worker is frozen
# (deadlock, network wedge, GC pause beyond threshold), the key ages out
# and the healthcheck fails → docker restarts the container.
#
# Design notes:
#   - `setex` with 120s TTL so a dead worker's key clears itself within
#     one extra interval, preventing false-positives on healthcheck races.
#   - `on_error='ignore'` equivalent via try/except — a Redis outage must
#     NOT crash the worker; healthcheck will fail loudly instead, which is
#     the correct signal.
#   - Value is the unix timestamp so the healthcheck can compute age
#     without depending on Redis server time.
_HEARTBEAT_KEY = "droneops:worker:heartbeat"


def _write_heartbeat() -> None:
    try:
        import time

        import redis  # local import keeps cold start unaffected
        r = redis.from_url(settings.redis_url, socket_timeout=2)
        r.setex(_HEARTBEAT_KEY, 120, int(time.time()))
    except Exception as exc:  # pragma: no cover — best-effort
        logger.debug("doc.worker.heartbeat: write failed: %s", exc)


@worker_ready.connect
def _on_worker_ready(**_kwargs):
    # Seed the key immediately so the docker start_period window sees a
    # fresh heartbeat instead of waiting for the first control-loop tick.
    _write_heartbeat()
    logger.info("doc.worker.ready: heartbeat seeded")


@heartbeat_sent.connect
def _on_worker_heartbeat(**_kwargs):
    _write_heartbeat()


@celery_app.task(name="send_report_email")
def send_report_email_task(to_email: str, customer_name: str, mission_title: str, pdf_path: str):
    """Background task to send report email."""
    from app.services.email_service import send_report_email

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(
            send_report_email(
                to_email=to_email,
                customer_name=customer_name,
                mission_title=mission_title,
                pdf_path=pdf_path,
            )
        )
    finally:
        loop.close()


@celery_app.task(name="generate_report", bind=True)
def generate_report_task(
    self,
    mission_id: str,
    user_narrative: str,
    mission_title: str,
    mission_type: str,
    location: str,
    flight_summaries: list[dict],
    ground_covered_acres: float | None,
    total_duration: float,
    total_distance: float = 0,
    map_path: str | None = None,
    mission_date: str | None = None,
    company_name: str = "DroneOps",
):
    """Background task to generate LLM report content."""
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session

    from app.database import async_session
    from app.models.report import Report
    from app.services.llm_provider import generate_report as llm_generate_report
    from app.services.llm_provider import get_llm_provider

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        # Determine provider — only wait for Ollama if that's the active provider
        async def _resolve_provider():
            async with async_session() as db:
                return await get_llm_provider(db)

        provider = loop.run_until_complete(_resolve_provider())

        if provider == "ollama":
            import httpx as _httpx
            for attempt in range(6):
                try:
                    async def _check_ollama():
                        async with _httpx.AsyncClient(timeout=5) as _client:
                            return await _client.get(f"{settings.ollama_base_url}/api/tags")
                    _resp = loop.run_until_complete(_check_ollama())
                    if _resp.status_code == 200:
                        break
                except Exception:
                    pass
                if attempt < 5:
                    import time
                    logger.info("Waiting for Ollama to be ready (attempt %d/6)...", attempt + 1)
                    time.sleep(5)
            else:
                raise RuntimeError(f"Ollama not reachable at {settings.ollama_base_url} after 30s")

        # Call the LLM via the dispatcher (passes its own async DB session)
        async def _generate():
            async with async_session() as db:
                return await llm_generate_report(
                    db=db,
                    user_narrative=user_narrative,
                    mission_title=mission_title,
                    mission_type=mission_type,
                    location=location,
                    flight_summaries=flight_summaries,
                    ground_covered_acres=ground_covered_acres,
                    total_duration_seconds=total_duration,
                    total_distance_meters=total_distance,
                    mission_date=mission_date,
                    company_name=company_name,
                )

        llm_content = loop.run_until_complete(_generate())

        # Save result to database using sync engine
        engine = create_engine(settings.database_url_sync)
        with Session(engine) as db:
            report = db.execute(
                select(Report).where(Report.mission_id == mission_id)
            ).scalar_one_or_none()

            if report:
                report.llm_generated_content = llm_content
                report.final_content = llm_content
                report.generated_at = datetime.utcnow()
            else:
                report = Report(
                    mission_id=mission_id,
                    user_narrative=user_narrative,
                    llm_generated_content=llm_content,
                    final_content=llm_content,
                    ground_covered_acres=ground_covered_acres if ground_covered_acres and ground_covered_acres > 0 else None,
                    flight_duration_total_seconds=total_duration if total_duration > 0 else None,
                    map_image_path=map_path,
                    generated_at=datetime.utcnow(),
                )
                db.add(report)

            db.commit()

        logger.info("Report generated for mission %s", mission_id)
        return {"status": "complete", "mission_id": mission_id}

    except Exception as exc:
        logger.error("Report generation failed for mission %s: %s", mission_id, exc)
        raise self.retry(exc=exc, max_retries=3, countdown=15)
    finally:
        loop.close()


# ── ADR-0002 §5 layer 3 — silent-drift watchdog ───────────────────────
@celery_app.task(name="check_device_silence")
def check_device_silence_task() -> dict:
    """Detect device API keys that were recently active but have gone silent.

    Threshold logic:
      - Key is "recently active" if `last_used_at` is within
        `DEVICE_SILENCE_ACTIVITY_WINDOW_DAYS` days (default 7).
      - Key is "silent" if `last_used_at` is older than
        `DEVICE_SILENCE_HOURS` hours (default 48).
      - Intersection = "was flying, stopped flying" — the exact class
        of drift that caused the 2026-04-23 incident where Bill's RC
        Pro silently stopped uploading after a Capacitor Preferences
        wipe.

    Each matching key fires a Pushover alert, deduped by
    `DEVICE_SILENCE_DEDUP_HOURS` (default 12) so a long outage does
    not spam Bill's phone. Emits a structured INFO log regardless of
    whether Pushover is wired, so Loki/Grafana can surface the same
    signal without Pushover.
    """
    from sqlalchemy import create_engine, select, and_
    from sqlalchemy.orm import Session

    from app.models.device_api_key import DeviceApiKey
    from app.services.pushover import send_alert_sync

    now = datetime.utcnow()
    activity_cutoff = now - timedelta(days=settings.device_silence_activity_window_days)
    silence_cutoff = now - timedelta(hours=settings.device_silence_hours)

    alerts: list[dict] = []

    engine = create_engine(settings.database_url_sync, pool_pre_ping=True)
    try:
        with Session(engine) as db:
            # is_active + last_used_at >= activity_cutoff + last_used_at < silence_cutoff
            rows = db.execute(
                select(DeviceApiKey).where(
                    and_(
                        DeviceApiKey.is_active.is_(True),
                        DeviceApiKey.last_used_at.isnot(None),
                        DeviceApiKey.last_used_at >= activity_cutoff,
                        DeviceApiKey.last_used_at < silence_cutoff,
                    )
                )
            ).scalars().all()

            for row in rows:
                hours_silent = int((now - row.last_used_at).total_seconds() // 3600)
                key_prefix = row.key_hash[:8] if row.key_hash else "????????"
                entry = {
                    "event": "device_silence_detected",
                    "device_id": str(row.id),
                    "device_label": row.label,
                    "key_prefix": key_prefix,
                    "last_used_at": row.last_used_at.isoformat() + "Z",
                    "hours_silent": hours_silent,
                    "activity_window_days": settings.device_silence_activity_window_days,
                    "silence_threshold_hours": settings.device_silence_hours,
                }
                logger.warning("device_silence_detected", extra=entry)

                title = f"DroneOps — {row.label} silent for {hours_silent}h"
                message = (
                    f"{row.label} (key {key_prefix}) was last seen "
                    f"{row.last_used_at.isoformat()}Z "
                    f"({hours_silent}h ago). Threshold: "
                    f"{settings.device_silence_hours}h. "
                    "Check controller: Settings → Device not paired banner? "
                    "Re-paste key if needed."
                )
                dedup = f"device_silence:{row.id}"
                send_alert_sync(
                    title,
                    message,
                    dedup_key=dedup,
                    dedup_ttl_seconds=settings.device_silence_dedup_hours * 3600,
                )
                alerts.append(entry)

        logger.info(
            "device_silence_sweep",
            extra={
                "event": "device_silence_sweep",
                "alert_count": len(alerts),
                "activity_window_days": settings.device_silence_activity_window_days,
                "silence_threshold_hours": settings.device_silence_hours,
            },
        )
    finally:
        engine.dispose()

    return {"alerts": len(alerts)}


# ── ADR-0003 — zero-touch device API key rotation finalizer ───────────
@celery_app.task(name="finalize_key_rotations")
def finalize_key_rotations_task() -> dict:
    """Promote ``rotated_to_key_hash`` → ``key_hash`` for any device whose
    rotation grace window has expired.

    Find rows where ``rotation_grace_until IS NOT NULL AND rotation_grace_until < now()``,
    move ``rotated_to_key_hash`` into ``key_hash``, clear both grace columns,
    delete the Redis hint. After this runs, the OLD key no longer
    authenticates — the device must use the new key (which it should
    already have picked up via the device-health hint during the grace
    window).

    Idempotent: a row whose grace has expired but whose ``rotated_to_key_hash``
    is NULL is skipped (defensive — should never happen, but cheaper than a
    crash).
    """
    from sqlalchemy import create_engine, select, and_
    from sqlalchemy.orm import Session

    from app.models.device_api_key import DeviceApiKey
    from app.services.rotation_hint import delete_rotation_hint_sync

    now = datetime.utcnow()
    promoted: list[dict] = []

    logger.info(
        "finalize_key_rotations_start",
        extra={"event": "finalize_key_rotations_start", "now": now.isoformat() + "Z"},
    )

    engine = create_engine(settings.database_url_sync, pool_pre_ping=True)
    try:
        with Session(engine) as db:
            rows = db.execute(
                select(DeviceApiKey).where(
                    and_(
                        DeviceApiKey.rotation_grace_until.isnot(None),
                        DeviceApiKey.rotation_grace_until < now,
                    )
                )
            ).scalars().all()

            for row in rows:
                if not row.rotated_to_key_hash:
                    # Defensive: grace expired with no new hash. Just clear
                    # the grace timestamp so we don't keep selecting the row.
                    row.rotation_grace_until = None
                    continue

                old_prefix = row.key_hash[:8] if row.key_hash else "????????"
                new_prefix = row.rotated_to_key_hash[:8]
                row.key_hash = row.rotated_to_key_hash
                row.rotated_to_key_hash = None
                row.rotation_grace_until = None

                # Best-effort hint delete; the Redis TTL would clean up
                # eventually anyway.
                delete_rotation_hint_sync(str(row.id))

                entry = {
                    "event": "rotate_key_finalized",
                    "device_id": str(row.id),
                    "device_label": row.label,
                    "old_key_prefix": old_prefix,
                    "new_key_prefix": new_prefix,
                }
                logger.info("rotate_key_finalized", extra=entry)
                promoted.append(entry)

            if rows:
                db.commit()

        logger.info(
            "finalize_key_rotations_done",
            extra={
                "event": "finalize_key_rotations_done",
                "promoted_count": len(promoted),
            },
        )
    except Exception as exc:
        logger.error(
            "finalize_key_rotations_failed",
            extra={
                "event": "finalize_key_rotations_failed",
                "error": str(exc),
            },
        )
        raise
    finally:
        engine.dispose()

    return {"promoted": len(promoted)}
