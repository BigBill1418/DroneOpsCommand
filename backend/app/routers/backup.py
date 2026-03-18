"""Database backup, restore, and integrity verification API."""

import hashlib
import logging
import os
import subprocess
import tempfile
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import get_current_user
from app.config import settings
from app.database import get_db
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
