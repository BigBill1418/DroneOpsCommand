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
# No flag-gating: the check itself is cheap (one SELECT) and the alert
# transport (ADR-0036 ntfy) is a no-op if
# NTFY_DRONEOPS_PUBLISHER_TOKEN is unset.
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
    # Payment reminders / dunning — daily at 16:00 UTC (~9am Pacific).
    "payment-reminders": {
        "task": "send_payment_reminders",
        "schedule": crontab(hour=16, minute=0),
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


def _apply_audience_findings(report, llm_content: str) -> None:
    """Run the audience-leak detector and persist findings on ``report``.

    ADR-0015 soft-block runtime gate. This NEVER raises and NEVER mutates
    ``llm_content`` — its only job is to populate the two flag columns so
    the editorial gate in the frontend can surface a warning banner.
    Detector failure is logged and the report saves with `has_audience_leak`
    left at its DB default (False) so generation never 500s on a regex bug.

    Kept as a module-level helper (rather than inline in the task body) so
    it's directly testable without a sync engine — tests can hand it a
    plain ``SimpleNamespace`` and assert the post-state.
    """
    try:
        from app.services.report_audience import detect_audience_leaks

        leaks = detect_audience_leaks(llm_content or "")
        report.audience_leak_details = [
            {"rule": leak.rule, "snippet": leak.snippet, "start": leak.start, "end": leak.end}
            for leak in leaks
        ]
        report.has_audience_leak = bool(leaks)
        if leaks:
            logger.warning(
                "Audience-leak detector flagged %d match(es) on mission %s: rules=%s",
                len(leaks),
                getattr(report, "mission_id", "<unknown>"),
                sorted({leak.rule for leak in leaks}),
            )
    except Exception as exc:
        # Detector must never block report persistence — that would
        # violate the always-generate contract. Leave flags at their
        # defaults and continue.
        logger.error("Audience-leak detector failed; report will save with flags unset: %s", exc, exc_info=True)
        report.has_audience_leak = False
        report.audience_leak_details = []


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

    from app.models.report import Report
    from app.services.llm_provider import generate_report as llm_generate_report
    from app.services.llm_provider import get_llm_provider
    from app.tasks.async_db import new_task_loop_session

    # Task-local loop + NullPool engine — never the module-global async_session,
    # whose asyncpg connections are bound to a prior (closed) loop and raise
    # "got Future attached to a different loop" when reused across tasks.
    loop, make_session, _task_engine = new_task_loop_session()

    try:
        # Determine provider — only wait for Ollama if that's the active provider
        async def _resolve_provider():
            async with make_session() as db:
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
            async with make_session() as db:
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

            # ADR-0015 — soft-block audience-leak gate. Runs AFTER content
            # is set on the row so the helper sees the post-state. Always
            # saves; never raises.
            _apply_audience_findings(report, llm_content)

            db.commit()

        logger.info("Report generated for mission %s", mission_id)
        return {"status": "complete", "mission_id": mission_id}

    except Exception as exc:
        logger.error("Report generation failed for mission %s: %s", mission_id, exc)
        raise self.retry(exc=exc, max_retries=3, countdown=15)
    finally:
        try:
            loop.run_until_complete(_task_engine.dispose())
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

    Each matching key fires an ntfy alert (ADR-0036), deduped by
    `DEVICE_SILENCE_DEDUP_HOURS` (default 12) so a long outage does
    not spam Bill's phone. Emits a structured INFO log regardless of
    whether the ntfy publisher token is wired, so Loki/Grafana can
    surface the same signal without the alert transport.
    """
    from sqlalchemy import create_engine, select, and_
    from sqlalchemy.orm import Session

    from app.models.device_api_key import DeviceApiKey
    from app.services.ntfy import send_alert_sync

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


# ── Payment reminders / dunning sweep ─────────────────────────────────
@celery_app.task(name="send_payment_reminders")
def send_payment_reminders_task() -> dict:
    """Daily dunning sweep — see app/services/dunning.py."""
    from app.services.dunning import run_dunning_sweep
    from app.tasks.async_db import task_event_loop

    # Task-local loop + NullPool engine (see async_db.py) — the module-global
    # async_session would raise "got Future attached to a different loop" here.
    with task_event_loop() as (loop, make_session):
        async def _run():
            async with make_session() as db:
                return await run_dunning_sweep(db)
        return loop.run_until_complete(_run())


# ── FU-8 #4 — backup create/restore as Celery jobs with progress polling ──
@celery_app.task(name="run_backup_job", bind=True)
def run_backup_job_task(self, kind: str, temp_path: str | None = None) -> dict:
    """Run a backup ``create`` or ``restore`` off the request path.

    Reuses the thread-safe synchronous helpers in ``app.routers.backup``
    (``_run_pg_command`` / ``_validate_archive`` / ``_compute_sha256`` etc.)
    — the same code the in-request endpoints offload to an executor — and
    streams ``{phase, progress}`` to Redis via ``backup_jobs.write_job_state``
    keyed by this task's id so the poll route can report mid-flight progress.

    ``kind="restore"`` operates on ``temp_path`` — an already-uploaded-and-
    validated dump (the ``temp_path`` returned by ``/api/backup/validate-upload``),
    the same artifact the synchronous ``restore-from-upload`` flow restores.
    The router validates that ``temp_path`` lives under the restore-spool dir
    before enqueuing; the task re-validates the archive before touching the DB.

    Returns the same result dict shape as the corresponding synchronous
    endpoint so a caller can treat job-result and direct-call identically.
    """
    import os
    import subprocess
    from datetime import datetime, timedelta

    from app.routers import backup as backup_mod
    from app.tasks.backup_jobs import (
        STATUS_COMPLETE,
        STATUS_FAILED,
        STATUS_RUNNING,
        write_job_state,
    )

    job_id = self.request.id

    def _progress(phase: str, progress: int) -> None:
        write_job_state(job_id, status=STATUS_RUNNING, phase=phase, progress=progress)

    conn = backup_mod._pg_conn_args()
    env = {"PGPASSWORD": conn["password"]}

    try:
        if kind == "create":
            _progress("starting", 5)
            os.makedirs(backup_mod.BACKUP_DIR, exist_ok=True)
            timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            filename = f"doc_backup_{timestamp}.dump"
            filepath = os.path.join(backup_mod.BACKUP_DIR, filename)

            _progress("dumping", 20)
            cmd = [
                "pg_dump", "-h", conn["host"], "-p", conn["port"],
                "-U", conn["user"], "-Fc", "-f", filepath, conn["dbname"],
            ]
            result = backup_mod._run_pg_command(cmd, env, timeout=600)
            if result.returncode != 0:
                raise RuntimeError(f"Backup failed: {result.stderr[:500]}")

            _progress("validating", 70)
            archive_valid, toc_entries = backup_mod._validate_archive(filepath, env)
            if not archive_valid:
                try:
                    os.unlink(filepath)
                except OSError:
                    pass
                raise RuntimeError("Backup archive failed validation — file is corrupt")

            _progress("hashing", 85)
            checksum = backup_mod._compute_sha256(filepath)
            size_bytes = os.path.getsize(filepath)
            backup_mod._write_sidecar(filepath, checksum)

            _progress("retention", 95)
            removed = _prune_backups(backup_mod, keep=filename)

            payload = {
                "kind": "create",
                "filename": filename,
                "filepath": filepath,
                "size_bytes": size_bytes,
                "sha256": checksum,
                "toc_entries": toc_entries,
                "removed_old_backups": removed,
            }
            write_job_state(job_id, status=STATUS_COMPLETE, phase="done", progress=100, result=payload)
            logger.info("Backup job %s complete: %s (%d bytes)", job_id, filename, size_bytes)
            return payload

        if kind == "restore":
            if not temp_path or not os.path.isfile(temp_path):
                raise FileNotFoundError("Uploaded backup file not found — re-upload and retry")
            safe_name = os.path.basename(temp_path)

            _progress("validating", 15)
            archive_valid, toc_entries = backup_mod._validate_archive(temp_path, env)
            if not archive_valid:
                raise RuntimeError(
                    "Invalid backup file — archive validation failed. Restore aborted to protect existing data."
                )

            _progress("restoring", 40)
            cmd = [
                "pg_restore", "-h", conn["host"], "-p", conn["port"],
                "-U", conn["user"], "-d", conn["dbname"],
                "--clean", "--if-exists", "--no-owner", "--no-privileges", temp_path,
            ]
            result = backup_mod._run_pg_command(cmd, env, timeout=600)
            if result.returncode != 0 and result.stderr:
                errors = [l for l in result.stderr.splitlines() if "ERROR" in l]
                if errors:
                    raise RuntimeError(f"Restore had errors: {errors[0][:200]}")

            _progress("verifying", 85)
            verify = backup_mod._run_pg_command(
                [
                    "psql", "-h", conn["host"], "-p", conn["port"],
                    "-U", conn["user"], "-d", conn["dbname"],
                    "-c", "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public';",
                    "-t", "-A",
                ],
                env, timeout=15,
            )
            table_count = 0
            if verify.returncode == 0:
                try:
                    table_count = int(verify.stdout.strip())
                except ValueError:
                    pass

            # Clean up the uploaded spool now that the restore consumed it.
            try:
                os.unlink(temp_path)
                os.rmdir(os.path.dirname(temp_path))
            except OSError:
                pass

            payload = {
                "kind": "restore",
                "restored": True,
                "filename": safe_name,
                "toc_entries": toc_entries,
                "table_count": table_count,
                "warnings": result.stderr[:1000] if result.stderr else None,
            }
            write_job_state(job_id, status=STATUS_COMPLETE, phase="done", progress=100, result=payload)
            logger.info("Restore job %s complete: %s (%d tables)", job_id, safe_name, table_count)
            return payload

        raise ValueError(f"Unknown backup job kind: {kind!r}")

    except subprocess.TimeoutExpired:
        write_job_state(job_id, status=STATUS_FAILED, phase="timeout", progress=100,
                        error=f"{kind} timed out")
        logger.error("Backup job %s (%s) timed out", job_id, kind)
        raise
    except FileNotFoundError as exc:
        write_job_state(job_id, status=STATUS_FAILED, phase="error", progress=100, error=str(exc))
        logger.error("Backup job %s (%s) failed: %s", job_id, kind, exc)
        raise
    except Exception as exc:
        write_job_state(job_id, status=STATUS_FAILED, phase="error", progress=100, error=str(exc))
        logger.error("Backup job %s (%s) failed: %s", job_id, kind, exc)
        raise


def _prune_backups(backup_mod, *, keep: str) -> list[str]:
    """Delete dumps older than the configured retention window, plus their
    ``.sha256`` sidecars. Mirrors the cleanup in the synchronous
    ``run_backup_now`` endpoint but reads retention from a fresh sync session
    so the task carries no async DB state."""
    import os
    from datetime import datetime, timedelta

    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session

    from app.models.system_settings import SystemSetting

    retention_days = 30
    try:
        engine = create_engine(settings.database_url_sync)
        with Session(engine) as db:
            row = db.execute(
                select(SystemSetting).where(SystemSetting.key == "backup_retention_days")
            ).scalar_one_or_none()
            if row is not None:
                try:
                    retention_days = int(row.value)
                except (ValueError, TypeError):
                    retention_days = 30
        engine.dispose()
    except Exception as exc:  # pragma: no cover - default applies
        logger.warning("Backup job: could not read retention setting, using 30d: %s", exc)

    cutoff = datetime.utcnow() - timedelta(days=retention_days)
    removed: list[str] = []
    try:
        for fname in os.listdir(backup_mod.BACKUP_DIR):
            if not fname.endswith(".dump") or fname == keep:
                continue
            fpath = os.path.join(backup_mod.BACKUP_DIR, fname)
            try:
                if datetime.utcfromtimestamp(os.path.getmtime(fpath)) < cutoff:
                    os.unlink(fpath)
                    try:
                        os.unlink(backup_mod._sidecar_path(fpath))
                    except OSError:
                        pass
                    removed.append(fname)
            except OSError as exc:
                logger.warning("Backup job: could not remove old backup %s: %s", fpath, exc)
    except OSError as exc:
        logger.warning("Backup job: could not list backup dir for cleanup: %s", exc)
    return removed


# ── P2-2 / ADR-0023 — device flight-log parse as a Celery job ─────────
@celery_app.task(name="parse_device_flight", bind=True)
def parse_device_flight_task(
    self,
    batch_id: str,
    tmp_path: str,
    filename: str,
    file_hash: str,
    parser_headers: dict,
) -> dict:
    """Parse one device-uploaded flight log off the request path (ADR-0023).

    The async route (``device_upload_flights_async``) has already streamed the
    upload to ``tmp_path`` (via the existing OOM-safe ``_spool_upload``, which
    also persisted the original bytes to the hash-named store) and run the cheap
    pre-parse SHA-256 dedup short-circuit. This task does the *expensive* half —
    the parser POST + flight construction — that the legacy synchronous route
    holds the HTTP connection open for. The connection is now released after the
    byte-stream; this work runs here.

    Sync-context pattern (chosen to match the ``celery_tasks.py`` house style):
    a Celery task is a synchronous worker context and cannot reuse the request's
    async DB session, and the parse logic the route uses (``_SpooledUpload.parse``,
    the per-flight dedup ``db.execute``, and ``_build_flight_from_parsed``) is
    *async*. Rather than re-implementing those DB writes synchronously (which
    would let the two code paths drift — the exact failure ADR-0023 §3.2 warns
    against), we run the existing async helpers verbatim inside
    ``task_event_loop()`` — the same fresh-loop + NullPool pattern
    ``send_payment_reminders_task`` uses (``async_db.py`` documents why the
    module-global pooled session would raise a cross-loop error here). One
    file == one task == one ``per_file`` entry; the contract's ``per_file``
    array is plural so a future multi-file submit needs no backend change.

    Failure posture mirrors the route's per-file ``except`` (flight_library.py
    ~:1061): a parser-down / parse error records that file ``state=error`` and
    completes the batch — it does NOT raise the whole batch (so a Celery
    AsyncResult FAILURE is reserved for an *unexpected* crash before any per-file
    result, matching ADR-0023 §2.4's "failed vs complete-with-error" split).
    """
    import os

    import httpx
    from sqlalchemy import select

    from app.models.flight import Flight
    from app.routers import flight_library as fl
    from app.tasks.async_db import task_event_loop
    from app.tasks.device_upload_jobs import (
        STATUS_COMPLETE,
        STATUS_FAILED,
        STATUS_RUNNING,
        write_batch_state,
    )

    # The single per-file overlay entry. Mutated in place as we make progress so
    # every Redis write reflects the latest known state of this file.
    entry: dict = {
        "name": filename,
        "state": "parsing",
        "imported": 0,
        "skipped": 0,
        "error": None,
    }

    write_batch_state(
        batch_id, status=STATUS_RUNNING, phase="parsing", progress=10,
        per_file=[entry],
    )

    # Resolve the upload on the SHARED volume — not the cross-container /tmp path.
    # The original bytes were persisted to the hash-named store
    # (flight_library._store_original_from_path) at spool time; that store lives
    # under UPLOAD_DIR (/data/uploads/flight_logs) on the app_data:/data volume
    # mounted by BOTH the `backend` and `worker` containers. The `tmp_path`
    # spool, by contrast, is on the *backend* container's private /tmp and is
    # invisible here — opening it raised FileNotFoundError and failed every
    # async device upload (ADR-0023 cross-container temp-handoff regression,
    # field-reported on the Mavic 4 Pro 2026-06-24: ENOENT '/tmp/flight_upload_*').
    # Prefer the shared store; fall back to tmp_path only when it actually exists
    # (same-container/dev, or a legacy in-flight task message).
    stored = fl._get_stored_file_path(file_hash)
    if stored is not None:
        parse_path: str | None = str(stored)
    elif tmp_path and os.path.exists(tmp_path):
        parse_path = tmp_path
    else:
        parse_path = None

    if parse_path is None:
        entry["state"] = "error"
        entry["error"] = (
            f"{filename}: upload artifact not found on shared store "
            f"(hash={file_hash}); /tmp spool not visible to worker container"
        )
        logger.error(
            "device flight parse job %s: artifact missing for file=%s hash=%s "
            "(tmp_path=%s absent on worker, hash store has no match) — "
            "check app_data volume is mounted on both backend and worker",
            batch_id, filename, file_hash, tmp_path,
        )
        write_batch_state(
            batch_id, status=STATUS_COMPLETE, phase="done", progress=100,
            per_file=[entry], error=entry["error"],
        )
        return {"batch_id": batch_id, "per_file": [entry]}

    try:
        # Reuse the route's exact parse + persist logic under a task-local loop.
        with task_event_loop() as (loop, make_session):

            async def _run() -> None:
                # Mirror _SpooledUpload.parse — POST the resolved file to the
                # parser sidecar. Same client/timeout/shape the route uses.
                async with httpx.AsyncClient(timeout=120) as client:
                    with open(parse_path, "rb") as parse_src:
                        resp = await client.post(
                            f"{fl.PARSER_URL}/parse",
                            files={"file": (filename or "upload.txt", parse_src)},
                            headers=parser_headers,
                        )
                if resp.status_code != 200:
                    # Same text the synchronous route appends (ADR-0023 §2.4),
                    # so the client's existing error rendering is reused verbatim.
                    entry["state"] = "error"
                    entry["error"] = f"{filename}: parser returned {resp.status_code}"
                    return
                data = resp.json()

                parse_errors = data.get("errors") or []

                async with make_session() as db:
                    imported = 0
                    skipped = 0
                    for parsed in data.get("flights", []):
                        ph = parsed.get("file_hash", file_hash)
                        dup = await db.execute(
                            select(Flight).where(Flight.source_file_hash == ph)
                        )
                        if dup.scalar_one_or_none():
                            skipped += 1
                            continue
                        # The SAME builder the route + /upload + /reprocess use.
                        await fl._build_flight_from_parsed(db, parsed, file_hash, filename)
                        imported += 1
                    await db.commit()

                entry["imported"] = imported
                entry["skipped"] = skipped
                if imported:
                    entry["state"] = "imported"
                elif skipped:
                    entry["state"] = "skipped"
                elif parse_errors:
                    entry["state"] = "error"
                    entry["error"] = "; ".join(str(e) for e in parse_errors)
                else:
                    # Parser returned 200 with no flights and no errors — nothing
                    # to import; treat as a no-op skip rather than an error.
                    entry["state"] = "skipped"

            loop.run_until_complete(_run())

    except httpx.ConnectError:
        entry["state"] = "error"
        entry["error"] = f"{filename}: flight-parser service unavailable"
    except Exception as exc:  # noqa: BLE001 - mirror the route's broad per-file except
        entry["state"] = "error"
        entry["error"] = f"{filename}: {exc}"

    # Batch completes regardless of per-file state (ADR-0023 §2.4): the rollup
    # is "all jobs terminal", not "all succeeded". A file in `error` lives
    # inside a `complete` batch.
    write_batch_state(
        batch_id, status=STATUS_COMPLETE, phase="done", progress=100,
        per_file=[entry],
        error=(entry["error"] if entry["state"] == "error" else None),
    )

    # Best-effort cleanup of the spool temp file now the parse consumed it. The
    # original bytes were already persisted to the hash store at spool time, so
    # losing this temp file costs nothing.
    try:
        os.unlink(tmp_path)
    except OSError:
        pass

    logger.info(
        "device flight parse job %s complete: file=%s state=%s imported=%d skipped=%d",
        batch_id, filename, entry["state"], entry["imported"], entry["skipped"],
    )
    return {"batch_id": batch_id, "per_file": [entry]}
