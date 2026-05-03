#!/usr/bin/env bash
# DroneOps Command DB snapshot — pg_dump to ~/droneops/backups,
# gzipped, retains last 14 days. Cron-friendly (no interactive prompts,
# never writes to stdout on success). Modeled on ~/callsign/scripts/snapshot.sh.
#
# Crontab line (BOS-HQ, runs daily at 03:23 local — staggered from
# CallSign's 03:17 to avoid disk-IO collision):
#   23 3 * * * /home/bbarnard065/droneops/scripts/snapshot.sh >> /home/bbarnard065/droneops/backups/snapshot.log 2>&1
#
# Restore drill (quarterly):
#   gunzip -c <latest>.sql.gz | docker exec -i droneops-standby-db \
#     psql -U droneops -d droneops_restore_drill
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="${DRONEOPS_BACKUP_DIR:-/home/bbarnard065/droneops/backups}"
RETENTION_DAYS="${DRONEOPS_BACKUP_RETENTION_DAYS:-14}"

# Post-2026-04-20 promoted-standby topology — droneops-standby-db is the
# primary that the app writes to. droneops-db is the legacy original
# (neutralized). Both backups would be redundant; we snapshot the writable one.
DB_CONTAINER="droneops-standby-db"

TIMESTAMP="$(date -u +%Y%m%d-%H%M%S)"
OUT="${BACKUP_DIR}/droneops-${TIMESTAMP}.sql.gz"

mkdir -p "${BACKUP_DIR}"
chmod 700 "${BACKUP_DIR}"

# Resolve DB user/name from .env if present, else use compose defaults.
# We deliberately do NOT `source` .env — it contains free-form values
# like SMTP_FROM_NAME='BarnardHQ Drone Operations' that break sh parsing.
# Read just the two keys we need via grep.
DB_USER="${DRONEOPS_DB_USER:-droneops}"
DB_NAME="${DRONEOPS_DB_NAME:-droneops}"
if [[ -f "${REPO_ROOT}/.env" ]]; then
  pg_user_line="$(grep -E '^POSTGRES_USER=' "${REPO_ROOT}/.env" | head -1 | cut -d= -f2-)"
  pg_db_line="$(grep -E '^POSTGRES_DB=' "${REPO_ROOT}/.env" | head -1 | cut -d= -f2-)"
  [[ -n "${pg_user_line}" ]] && POSTGRES_USER="${pg_user_line}"
  [[ -n "${pg_db_line}"   ]] && POSTGRES_DB="${pg_db_line}"
fi

if ! docker ps --format '{{.Names}}' | grep -qx "${DB_CONTAINER}"; then
  echo "[snapshot] ERROR: container ${DB_CONTAINER} is not running" >&2
  exit 1
fi

echo "[snapshot] $(date -u +%FT%TZ) starting pg_dump -> ${OUT}"
docker exec -i "${DB_CONTAINER}" \
  pg_dump --no-owner --no-privileges --format=plain \
    -U "${POSTGRES_USER:-${DB_USER}}" \
    -d "${POSTGRES_DB:-${DB_NAME}}" \
  | gzip -9 > "${OUT}"

chmod 600 "${OUT}"

# Retention sweep — never deletes the most recent file even if older than N days.
find "${BACKUP_DIR}" -maxdepth 1 -type f -name 'droneops-*.sql.gz' \
  -mtime "+${RETENTION_DAYS}" -print -delete || true

# Also snapshot the signed-TOS PDF directory so legal artifacts are
# included alongside the DB row that references them. Tar+gzip is fine
# (these are already-compressed PDFs but the tar wrapper keeps the
# audit-id-named files together).
TOS_DIR="${REPO_ROOT}/data/tos_signed"
if [[ -d "${TOS_DIR}" ]]; then
  TOS_OUT="${BACKUP_DIR}/droneops-tos-${TIMESTAMP}.tar.gz"
  tar -czf "${TOS_OUT}" -C "$(dirname "${TOS_DIR}")" "$(basename "${TOS_DIR}")" 2>/dev/null || true
  chmod 600 "${TOS_OUT}"
  find "${BACKUP_DIR}" -maxdepth 1 -type f -name 'droneops-tos-*.tar.gz' \
    -mtime "+${RETENTION_DAYS}" -print -delete || true
fi

echo "[snapshot] done. db=$(du -h "${OUT}" | cut -f1) tos=$([[ -f "${TOS_OUT:-}" ]] && du -h "${TOS_OUT}" | cut -f1 || echo 'n/a')"
