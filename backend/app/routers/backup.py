"""Database backup, restore, and integrity verification API."""

import hashlib
import logging
import os
import subprocess
import tempfile
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import get_current_user
from app.config import settings
from app.database import get_db
from app.models.system_settings import SystemSetting
from app.models.user import User

logger = logging.getLogger("doc.backup")

router = APIRouter(prefix="/api/backup", tags=["backup"])


def _pg_conn_args() -> dict:
    """Extract PostgreSQL connection args from the database URL."""
    url = settings.database_url_sync
    rest = url.split("://", 1)[1]
    userinfo, hostinfo = rest.rsplit("@", 1)
    user, password = userinfo.split(":", 1)
    hostport, dbname = hostinfo.split("/", 1)
    host, port = hostport.split(":", 1) if ":" in hostport else (hostport, "5432")
    return {"user": user, "password": password, "host": host, "port": port, "dbname": dbname}


def _run_pg_command(cmd: list[str], env: dict, timeout: int = 300) -> subprocess.CompletedProcess:
    """Run a PostgreSQL command with proper environment."""
    return subprocess.run(
        cmd, env={**os.environ, **env},
        capture_output=True, text=True, timeout=timeout,
    )


def _compute_sha256(filepath: str) -> str:
    """Compute SHA-256 hash of a file."""
    sha256 = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def _validate_archive(filepath: str, env: dict) -> tuple[bool, int]:
    """Validate a pg_dump archive. Returns (valid, toc_entry_count)."""
    try:
        result = _run_pg_command(["pg_restore", "--list", filepath], env, timeout=60)
        valid = result.returncode == 0
        entries = len([l for l in result.stdout.splitlines() if l.strip() and not l.startswith(";")])
        return valid, entries
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="pg_restore not found — postgresql-client not installed")
    except subprocess.TimeoutExpired:
        return False, 0


@router.post("/create-and-download")
async def create_and_download(
    _user: User = Depends(get_current_user),
):
    """Create a PostgreSQL backup, verify it, then stream it as a download.

    The backup is created in a temp file, validated for integrity, then
    streamed directly to the client browser as a file download (Save As).
    SHA-256 checksum is included in response headers for verification.
    """
    conn = _pg_conn_args()
    env = {"PGPASSWORD": conn["password"]}
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    filename = f"doc_backup_{timestamp}.dump"

    # Create backup in a temp directory
    tmpdir = tempfile.mkdtemp(prefix="doc_backup_")
    filepath = os.path.join(tmpdir, filename)

    cmd = [
        "pg_dump",
        "-h", conn["host"],
        "-p", conn["port"],
        "-U", conn["user"],
        "-Fc",  # custom format — compressed, supports selective restore
        "-f", filepath,
        conn["dbname"],
    ]

    try:
        result = _run_pg_command(cmd, env, timeout=600)
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Backup timed out")
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="pg_dump not found — postgresql-client not installed")

    if result.returncode != 0:
        logger.error("pg_dump failed: %s", result.stderr)
        raise HTTPException(status_code=500, detail=f"Backup failed: {result.stderr[:500]}")

    # Validate the archive before sending to user
    archive_valid, toc_entries = _validate_archive(filepath, env)
    if not archive_valid:
        os.unlink(filepath)
        os.rmdir(tmpdir)
        raise HTTPException(status_code=500, detail="Backup archive failed validation — file is corrupt")

    # Compute SHA-256 checksum
    checksum = _compute_sha256(filepath)
    file_size = os.path.getsize(filepath)

    logger.info(
        "Backup created & verified: %s (%d bytes, sha256=%s, %d objects)",
        filename, file_size, checksum, toc_entries,
    )

    def iter_file():
        try:
            with open(filepath, "rb") as f:
                while chunk := f.read(65536):
                    yield chunk
        finally:
            # Clean up temp file after streaming
            try:
                os.unlink(filepath)
                os.rmdir(tmpdir)
            except OSError:
                pass

    return StreamingResponse(
        iter_file(),
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(file_size),
            "X-Backup-SHA256": checksum,
            "X-Backup-Objects": str(toc_entries),
            "X-Backup-Timestamp": timestamp,
            "Access-Control-Expose-Headers": "X-Backup-SHA256, X-Backup-Objects, X-Backup-Timestamp, Content-Disposition",
        },
    )


@router.post("/validate-upload")
async def validate_upload(
    file: UploadFile = File(...),
    _user: User = Depends(get_current_user),
):
    """Upload a backup file and validate it WITHOUT restoring.

    Returns integrity info so the user can confirm before proceeding.
    The file is saved to a temp location for the subsequent restore call.
    """
    conn = _pg_conn_args()
    env = {"PGPASSWORD": conn["password"]}

    # Save to temp file for validation
    tmpdir = tempfile.mkdtemp(prefix="doc_restore_")
    safe_name = os.path.basename(file.filename or "uploaded_backup.dump")
    filepath = os.path.join(tmpdir, safe_name)

    content = await file.read()
    with open(filepath, "wb") as f:
        f.write(content)

    # Compute checksum
    checksum = hashlib.sha256(content).hexdigest()

    # Validate archive structure
    archive_valid, toc_entries = _validate_archive(filepath, env)

    if not archive_valid:
        # Clean up
        os.unlink(filepath)
        os.rmdir(tmpdir)
        raise HTTPException(
            status_code=400,
            detail="Invalid backup file — not a valid PostgreSQL custom-format archive. "
                   "Only .dump files created by pg_dump -Fc are supported.",
        )

    logger.info(
        "Upload validated: %s (%d bytes, sha256=%s, %d objects)",
        safe_name, len(content), checksum, toc_entries,
    )

    return {
        "valid": True,
        "filename": safe_name,
        "temp_path": filepath,
        "sha256": checksum,
        "size_bytes": len(content),
        "toc_entries": toc_entries,
    }


@router.post("/restore-from-upload")
async def restore_from_upload(
    file: UploadFile = File(...),
    _user: User = Depends(get_current_user),
):
    """Upload a backup file and restore the database from it.

    Validates the archive first, then performs the restore.
    This replaces ALL current database contents.
    """
    conn = _pg_conn_args()
    env = {"PGPASSWORD": conn["password"]}

    # Save to temp file
    tmpdir = tempfile.mkdtemp(prefix="doc_restore_")
    safe_name = os.path.basename(file.filename or "uploaded_backup.dump")
    filepath = os.path.join(tmpdir, safe_name)

    content = await file.read()
    with open(filepath, "wb") as f:
        f.write(content)

    # Compute checksum
    checksum = hashlib.sha256(content).hexdigest()

    # Validate archive BEFORE restoring
    archive_valid, toc_entries = _validate_archive(filepath, env)
    if not archive_valid:
        os.unlink(filepath)
        os.rmdir(tmpdir)
        raise HTTPException(
            status_code=400,
            detail="Invalid backup file — archive validation failed. Restore aborted to protect existing data.",
        )

    logger.info(
        "Restoring from upload: %s (sha256=%s, %d objects)",
        safe_name, checksum, toc_entries,
    )

    # Perform restore
    cmd = [
        "pg_restore",
        "-h", conn["host"],
        "-p", conn["port"],
        "-U", conn["user"],
        "-d", conn["dbname"],
        "--clean",
        "--if-exists",
        "--no-owner",
        "--no-privileges",
        filepath,
    ]

    try:
        result = _run_pg_command(cmd, env, timeout=600)
    except subprocess.TimeoutExpired:
        os.unlink(filepath)
        os.rmdir(tmpdir)
        raise HTTPException(status_code=504, detail="Restore timed out")

    # Clean up temp file
    try:
        os.unlink(filepath)
        os.rmdir(tmpdir)
    except OSError:
        pass

    # pg_restore returns non-zero for harmless warnings; only fail on real errors
    if result.returncode != 0 and result.stderr:
        errors = [l for l in result.stderr.splitlines() if "ERROR" in l]
        if errors:
            logger.error("pg_restore errors: %s", "\n".join(errors[:10]))
            raise HTTPException(
                status_code=500,
                detail=f"Restore had errors: {errors[0][:200]}",
            )

    # Post-restore validation: verify key tables exist and are accessible
    verify_result = _run_pg_command(
        [
            "psql",
            "-h", conn["host"],
            "-p", conn["port"],
            "-U", conn["user"],
            "-d", conn["dbname"],
            "-c", "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public';",
            "-t", "-A",
        ],
        env,
        timeout=15,
    )
    table_count = 0
    if verify_result.returncode == 0:
        try:
            table_count = int(verify_result.stdout.strip())
        except ValueError:
            pass

    logger.info(
        "Database restored from %s — %d public tables present, sha256=%s",
        safe_name, table_count, checksum,
    )

    return {
        "restored": True,
        "filename": safe_name,
        "sha256": checksum,
        "size_bytes": len(content),
        "toc_entries": toc_entries,
        "table_count": table_count,
        "warnings": result.stderr[:1000] if result.stderr else None,
    }


# ---------------------------------------------------------------------------
# Scheduled backup settings & history endpoints
# ---------------------------------------------------------------------------

BACKUP_DIR = "/data/backups"

_SCHEDULE_KEYS = {
    "backup_enabled": "false",
    "backup_retention_days": "30",
    "backup_time": "02:00",
}


class BackupScheduleUpdate(BaseModel):
    enabled: bool = False
    retention_days: int = 30
    backup_time: str = "02:00"  # HH:MM format


async def _get_setting(db: AsyncSession, key: str) -> str:
    """Fetch a single SystemSetting value, returning the default if not set."""
    result = await db.execute(select(SystemSetting).where(SystemSetting.key == key))
    row = result.scalar_one_or_none()
    return row.value if row is not None else _SCHEDULE_KEYS.get(key, "")


async def _set_setting(db: AsyncSession, key: str, value: str) -> None:
    """Upsert a single SystemSetting key-value pair."""
    result = await db.execute(select(SystemSetting).where(SystemSetting.key == key))
    row = result.scalar_one_or_none()
    if row is None:
        db.add(SystemSetting(key=key, value=value))
    else:
        row.value = value


@router.get("/schedule")
async def get_backup_schedule(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Get the current scheduled backup settings from the SystemSetting table."""
    logger.info("Fetching backup schedule settings")
    enabled_val = await _get_setting(db, "backup_enabled")
    retention_val = await _get_setting(db, "backup_retention_days")
    time_val = await _get_setting(db, "backup_time")

    try:
        retention_days = int(retention_val)
    except ValueError:
        retention_days = 30

    return {
        "enabled": enabled_val.lower() == "true",
        "retention_days": retention_days,
        "backup_time": time_val,
    }


@router.put("/schedule")
async def update_backup_schedule(
    payload: BackupScheduleUpdate,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Update the scheduled backup settings in the SystemSetting table."""
    logger.info(
        "Updating backup schedule: enabled=%s, retention_days=%d, backup_time=%s",
        payload.enabled, payload.retention_days, payload.backup_time,
    )

    await _set_setting(db, "backup_enabled", "true" if payload.enabled else "false")
    await _set_setting(db, "backup_retention_days", str(payload.retention_days))
    await _set_setting(db, "backup_time", payload.backup_time)
    await db.commit()

    logger.info("Backup schedule updated successfully")
    return {
        "enabled": payload.enabled,
        "retention_days": payload.retention_days,
        "backup_time": payload.backup_time,
    }


@router.get("/history")
async def get_backup_history(
    _user: User = Depends(get_current_user),
):
    """List backup files saved in the backup directory with size, date, and sha256."""
    logger.info("Listing backup history from %s", BACKUP_DIR)

    if not os.path.isdir(BACKUP_DIR):
        logger.info("Backup directory %s does not exist — returning empty history", BACKUP_DIR)
        return []

    entries = []
    try:
        filenames = sorted(
            [f for f in os.listdir(BACKUP_DIR) if f.endswith(".dump")],
            reverse=True,
        )
    except OSError as exc:
        logger.error("Failed to list backup directory %s: %s", BACKUP_DIR, exc)
        raise HTTPException(status_code=500, detail=f"Could not read backup directory: {exc}")

    for filename in filenames:
        filepath = os.path.join(BACKUP_DIR, filename)
        try:
            stat = os.stat(filepath)
            size_bytes = stat.st_size
            modified_at = datetime.utcfromtimestamp(stat.st_mtime).isoformat() + "Z"
            checksum = _compute_sha256(filepath)
            entries.append({
                "filename": filename,
                "size_bytes": size_bytes,
                "modified_at": modified_at,
                "sha256": checksum,
            })
        except OSError as exc:
            logger.warning("Could not stat backup file %s: %s", filepath, exc)
            continue

    logger.info("Backup history: %d file(s) found", len(entries))
    return entries


@router.post("/run-now")
async def run_backup_now(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Trigger an immediate scheduled backup — saves the dump to disk, does not stream."""
    logger.info("Immediate scheduled backup requested")

    # Ensure backup directory exists
    try:
        os.makedirs(BACKUP_DIR, exist_ok=True)
    except OSError as exc:
        logger.error("Cannot create backup directory %s: %s", BACKUP_DIR, exc)
        raise HTTPException(status_code=500, detail=f"Cannot create backup directory: {exc}")

    conn = _pg_conn_args()
    env = {"PGPASSWORD": conn["password"]}
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    filename = f"doc_backup_{timestamp}.dump"
    filepath = os.path.join(BACKUP_DIR, filename)

    cmd = [
        "pg_dump",
        "-h", conn["host"],
        "-p", conn["port"],
        "-U", conn["user"],
        "-Fc",
        "-f", filepath,
        conn["dbname"],
    ]

    logger.info("Running pg_dump to %s", filepath)
    try:
        result = _run_pg_command(cmd, env, timeout=600)
    except subprocess.TimeoutExpired:
        logger.error("Scheduled backup timed out for %s", filename)
        raise HTTPException(status_code=504, detail="Backup timed out")
    except FileNotFoundError:
        logger.error("pg_dump not found — postgresql-client not installed")
        raise HTTPException(status_code=500, detail="pg_dump not found — postgresql-client not installed")

    if result.returncode != 0:
        logger.error("pg_dump failed for scheduled backup %s: %s", filename, result.stderr)
        raise HTTPException(status_code=500, detail=f"Backup failed: {result.stderr[:500]}")

    # Validate archive integrity
    archive_valid, toc_entries = _validate_archive(filepath, env)
    if not archive_valid:
        try:
            os.unlink(filepath)
        except OSError:
            pass
        logger.error("Scheduled backup archive failed validation: %s", filename)
        raise HTTPException(status_code=500, detail="Backup archive failed validation — file is corrupt")

    checksum = _compute_sha256(filepath)
    size_bytes = os.path.getsize(filepath)

    logger.info(
        "Scheduled backup saved: %s (%d bytes, sha256=%s, %d objects)",
        filename, size_bytes, checksum, toc_entries,
    )

    # Clean up old backups beyond retention_days
    retention_val = await _get_setting(db, "backup_retention_days")
    try:
        retention_days = int(retention_val)
    except ValueError:
        retention_days = 30

    cutoff = datetime.utcnow() - timedelta(days=retention_days)
    removed = []
    try:
        for fname in os.listdir(BACKUP_DIR):
            if not fname.endswith(".dump") or fname == filename:
                continue
            fpath = os.path.join(BACKUP_DIR, fname)
            try:
                mtime = datetime.utcfromtimestamp(os.path.getmtime(fpath))
                if mtime < cutoff:
                    os.unlink(fpath)
                    removed.append(fname)
                    logger.info("Removed expired backup: %s (mtime=%s, cutoff=%s)", fname, mtime.isoformat(), cutoff.isoformat())
            except OSError as exc:
                logger.warning("Could not remove old backup %s: %s", fpath, exc)
    except OSError as exc:
        logger.warning("Could not list backup directory for cleanup: %s", exc)

    if removed:
        logger.info("Cleaned up %d expired backup(s): %s", len(removed), removed)

    return {
        "filename": filename,
        "filepath": filepath,
        "size_bytes": size_bytes,
        "sha256": checksum,
        "toc_entries": toc_entries,
        "retention_days": retention_days,
        "removed_old_backups": removed,
    }


@router.delete("/history/{filename}")
async def delete_backup_file(
    filename: str,
    _user: User = Depends(get_current_user),
):
    """Delete a specific backup file from the backup directory."""
    # Sanitize: reject any path traversal attempts
    safe_name = os.path.basename(filename)
    if safe_name != filename or not safe_name.endswith(".dump"):
        logger.warning("Rejected invalid backup filename for deletion: %r", filename)
        raise HTTPException(status_code=400, detail="Invalid filename — only .dump files in the backup directory may be deleted")

    filepath = os.path.join(BACKUP_DIR, safe_name)

    if not os.path.isfile(filepath):
        logger.warning("Delete requested for non-existent backup: %s", filepath)
        raise HTTPException(status_code=404, detail=f"Backup file not found: {safe_name}")

    try:
        os.unlink(filepath)
    except OSError as exc:
        logger.error("Failed to delete backup file %s: %s", filepath, exc)
        raise HTTPException(status_code=500, detail=f"Could not delete backup file: {exc}")

    logger.info("Backup file deleted: %s", safe_name)
    return {"deleted": True, "filename": safe_name}
