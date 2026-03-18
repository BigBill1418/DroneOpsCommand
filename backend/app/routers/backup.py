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

BACKUP_DIR = "/data/backups"


def _pg_conn_args() -> dict:
    """Extract PostgreSQL connection args from the database URL."""
    # database_url_sync: postgresql://doc:changeme@db:5432/doc
    url = settings.database_url_sync
    # Strip scheme
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


@router.post("/create")
async def create_backup(
    _user: User = Depends(get_current_user),
):
    """Create a full PostgreSQL backup (custom format) with SHA-256 checksum."""
    os.makedirs(BACKUP_DIR, exist_ok=True)

    conn = _pg_conn_args()
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    filename = f"doc_backup_{timestamp}.dump"
    filepath = os.path.join(BACKUP_DIR, filename)

    env = {"PGPASSWORD": conn["password"]}
    cmd = [
        "pg_dump",
        "-h", conn["host"],
        "-p", conn["port"],
        "-U", conn["user"],
        "-Fc",  # custom format — supports selective restore, compression
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

    # Compute SHA-256 checksum
    sha256 = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    checksum = sha256.hexdigest()

    # Write checksum file
    checksum_path = filepath + ".sha256"
    with open(checksum_path, "w") as f:
        f.write(f"{checksum}  {filename}\n")

    file_size = os.path.getsize(filepath)
    logger.info("Backup created: %s (%d bytes, sha256=%s)", filename, file_size, checksum)

    return {
        "filename": filename,
        "size_bytes": file_size,
        "sha256": checksum,
        "created_at": datetime.utcnow().isoformat(),
    }


@router.get("/download/{filename}")
async def download_backup(
    filename: str,
    _user: User = Depends(get_current_user),
):
    """Download a backup file."""
    # Sanitize filename to prevent path traversal
    safe_name = os.path.basename(filename)
    filepath = os.path.join(BACKUP_DIR, safe_name)

    if not os.path.isfile(filepath):
        raise HTTPException(status_code=404, detail="Backup file not found")

    def iter_file():
        with open(filepath, "rb") as f:
            while chunk := f.read(65536):
                yield chunk

    return StreamingResponse(
        iter_file(),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}"'},
    )


@router.get("/list")
async def list_backups(
    _user: User = Depends(get_current_user),
):
    """List all available backups with their checksums."""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    backups = []

    for fname in sorted(os.listdir(BACKUP_DIR), reverse=True):
        if not fname.endswith(".dump"):
            continue
        fpath = os.path.join(BACKUP_DIR, fname)
        checksum = ""
        checksum_file = fpath + ".sha256"
        if os.path.isfile(checksum_file):
            with open(checksum_file) as f:
                checksum = f.read().split()[0] if f.read else ""
                # Re-read since f.read consumed it
            with open(checksum_file) as f:
                line = f.readline().strip()
                checksum = line.split()[0] if line else ""

        backups.append({
            "filename": fname,
            "size_bytes": os.path.getsize(fpath),
            "created_at": datetime.utcfromtimestamp(os.path.getmtime(fpath)).isoformat(),
            "sha256": checksum,
        })

    return backups


@router.post("/verify/{filename}")
async def verify_backup(
    filename: str,
    _user: User = Depends(get_current_user),
):
    """Verify backup integrity by checking SHA-256 checksum and pg_restore validation."""
    safe_name = os.path.basename(filename)
    filepath = os.path.join(BACKUP_DIR, safe_name)

    if not os.path.isfile(filepath):
        raise HTTPException(status_code=404, detail="Backup file not found")

    # 1. Verify SHA-256 checksum
    sha256 = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    actual_checksum = sha256.hexdigest()

    checksum_file = filepath + ".sha256"
    stored_checksum = ""
    checksum_match = None
    if os.path.isfile(checksum_file):
        with open(checksum_file) as f:
            line = f.readline().strip()
            stored_checksum = line.split()[0] if line else ""
        checksum_match = actual_checksum == stored_checksum

    # 2. Validate with pg_restore --list (checks archive structure without restoring)
    conn = _pg_conn_args()
    env = {"PGPASSWORD": conn["password"]}
    try:
        result = _run_pg_command(["pg_restore", "--list", filepath], env, timeout=60)
        archive_valid = result.returncode == 0
        toc_entries = len([l for l in result.stdout.splitlines() if l.strip() and not l.startswith(";")])
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="pg_restore not found — postgresql-client not installed")
    except subprocess.TimeoutExpired:
        archive_valid = False
        toc_entries = 0

    is_valid = archive_valid and (checksum_match is not False)

    return {
        "filename": safe_name,
        "valid": is_valid,
        "sha256_actual": actual_checksum,
        "sha256_stored": stored_checksum,
        "checksum_match": checksum_match,
        "archive_valid": archive_valid,
        "toc_entries": toc_entries,
        "size_bytes": os.path.getsize(filepath),
    }


@router.post("/restore/{filename}")
async def restore_backup(
    filename: str,
    _user: User = Depends(get_current_user),
):
    """Restore the database from a backup file. WARNING: this replaces ALL current data."""
    safe_name = os.path.basename(filename)
    filepath = os.path.join(BACKUP_DIR, safe_name)

    if not os.path.isfile(filepath):
        raise HTTPException(status_code=404, detail="Backup file not found")

    conn = _pg_conn_args()
    env = {"PGPASSWORD": conn["password"]}

    # Use pg_restore with --clean to drop existing objects first
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
        raise HTTPException(status_code=504, detail="Restore timed out")
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="pg_restore not found — postgresql-client not installed")

    # pg_restore may return non-zero for warnings (e.g. "role does not exist") which are harmless
    if result.returncode != 0 and "ERROR" in result.stderr:
        # Check if there are actual errors vs just warnings
        errors = [l for l in result.stderr.splitlines() if "ERROR" in l]
        if errors:
            logger.error("pg_restore errors: %s", "\n".join(errors[:10]))
            raise HTTPException(status_code=500, detail=f"Restore had errors: {errors[0][:200]}")

    logger.info("Database restored from %s", safe_name)
    return {
        "restored": True,
        "filename": safe_name,
        "warnings": result.stderr[:1000] if result.stderr else None,
    }


@router.post("/upload-restore")
async def upload_and_restore(
    file: UploadFile = File(...),
    _user: User = Depends(get_current_user),
):
    """Upload a backup file and restore from it."""
    os.makedirs(BACKUP_DIR, exist_ok=True)

    # Save uploaded file
    safe_name = os.path.basename(file.filename or "uploaded_backup.dump")
    filepath = os.path.join(BACKUP_DIR, safe_name)

    content = await file.read()
    with open(filepath, "wb") as f:
        f.write(content)

    # Compute and store checksum
    sha256 = hashlib.sha256(content).hexdigest()
    with open(filepath + ".sha256", "w") as f:
        f.write(f"{sha256}  {safe_name}\n")

    # Verify archive is valid before restoring
    conn = _pg_conn_args()
    env = {"PGPASSWORD": conn["password"]}

    try:
        check = _run_pg_command(["pg_restore", "--list", filepath], env, timeout=60)
        if check.returncode != 0:
            raise HTTPException(status_code=400, detail="Invalid backup file — not a valid PostgreSQL archive")
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="pg_restore not found — postgresql-client not installed")

    # Restore
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
        raise HTTPException(status_code=504, detail="Restore timed out")

    if result.returncode != 0 and "ERROR" in result.stderr:
        errors = [l for l in result.stderr.splitlines() if "ERROR" in l]
        if errors:
            raise HTTPException(status_code=500, detail=f"Restore had errors: {errors[0][:200]}")

    logger.info("Database restored from uploaded file %s (sha256=%s)", safe_name, sha256)
    return {
        "restored": True,
        "filename": safe_name,
        "sha256": sha256,
        "size_bytes": len(content),
    }
