#!/usr/bin/env bash
# DroneOps Command DB snapshot — pg_dump to ~/droneops/backups (gzipped,
# 14-day local retention) AND off-host push to Cloudflare R2, plus an
# incremental sync of the app-data volume's uploads/ tree (signed-TOS legal
# PDFs + flight logs). Cron-friendly (no interactive prompts, never writes to
# stdout on success). Modeled on the glitchtip-pg-backup.sh R2 pattern.
#
# Crontab line (BOS-HQ, runs daily at 03:23 local — staggered from
# CallSign's 03:30 and glitchtip's 03:30 to avoid disk-IO/R2 collision):
#   23 3 * * * /home/bbarnard065/droneops/scripts/snapshot.sh >> /home/bbarnard065/droneops/backups/snapshot.log 2>&1
#
# Off-host layout (reuses the obs R2 creds + the obs-glitchtip-backups bucket,
# with dedicated droneops/ prefixes — the R2 token is bucket-scoped so a
# sibling prefix is the correct reuse):
#   s3://$BUCKET/droneops/db/YYYY/MM/DD/droneops-<TS>.sql.gz   (nightly dump)
#   s3://$BUCKET/droneops/uploads/...                          (incremental sync)
#
# On FULL success (DB dump pushed AND uploads synced) writes the node-exporter
# textfile freshness metric droneops_backup_last_success_timestamp_seconds;
# the Grafana rule obs-rule-droneops-backup-stale pages (via infrawatch-alerts)
# on age > 28h or a never-written metric. Any dump/upload FAILURE pushes an
# ntfy `high` to the SAME infrawatch-alerts topic and exits non-zero.
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
# App-data volume holding uploads/ (uploads/tos_signed = signed legal PDFs,
# uploads/<flight logs>). NOT ${REPO_ROOT}/data/tos_signed (which never
# existed — the pre-2026-07-16 tos step silently no-op'd every night).
APP_DATA_VOLUME="${DRONEOPS_APP_DATA_VOLUME:-droneops_app_data}"

# R2 / obs credential source + ntfy failure routing (no-new-topics: reuse the
# long-established infrawatch-alerts topic, 2026-07-14 operator decision).
ENV_FILE="${DRONEOPS_R2_ENV_FILE:-/opt/observability/.env}"
NTFY_TOPIC="${NTFY_TOPIC:-infrawatch-alerts}"
NTFY_HELPER="${NTFY_HELPER:-${HOME}/.local/bin/ntfy-publish.sh}"
AWSCLI_IMAGE="${AWSCLI_IMAGE:-amazon/aws-cli}"
TEXTFILE_DIR="${TEXTFILE_DIR:-/var/lib/node_exporter/textfile_collector}"
TEXTFILE_METRIC="droneops_backup_last_success_timestamp_seconds"
TEXTFILE_PATH="${TEXTFILE_DIR}/droneops_backup.prom"

TIMESTAMP="$(date -u +%Y%m%d-%H%M%S)"
OUT="${BACKUP_DIR}/droneops-${TIMESTAMP}.sql.gz"

fail() {
  local msg="$1"
  echo "[snapshot] FAIL: ${msg}" >&2
  if [[ -x "${NTFY_HELPER}" ]]; then
    "${NTFY_HELPER}" --topic "${NTFY_TOPIC}" --priority high \
      --title "[DroneOps] nightly backup FAILED" \
      --tags "backup,error" \
      --dedup-key droneops-backup --cooldown 21600 \
      -- "droneops snapshot.sh failed: ${msg}" || true
  fi
  exit 1
}

# Atomic node-exporter textfile freshness metric (best-effort; a textfile
# hiccup must not fail an otherwise-good backup). Only called on FULL success.
emit_freshness_metric() {
  local now tmp
  now="$(date -u +%s)"
  if ! tmp="$(mktemp "${TEXTFILE_DIR}/.droneops_backup.prom.XXXXXX" 2>/dev/null)"; then
    echo "[snapshot] WARN: could not create temp file in ${TEXTFILE_DIR}; freshness metric not updated" >&2
    return 0
  fi
  {
    echo "# HELP ${TEXTFILE_METRIC} Unix epoch of the last successful droneops DB dump + uploads sync -> R2."
    echo "# TYPE ${TEXTFILE_METRIC} gauge"
    echo "${TEXTFILE_METRIC} ${now}"
  } > "${tmp}" 2>/dev/null || { rm -f "${tmp}"; return 0; }
  chmod 0644 "${tmp}" 2>/dev/null || true
  mv -f "${tmp}" "${TEXTFILE_PATH}" 2>/dev/null || { rm -f "${tmp}"; return 0; }
  echo "[snapshot] freshness metric updated: ${TEXTFILE_METRIC}=${now}"
}

mkdir -p "${BACKUP_DIR}"
chmod 700 "${BACKUP_DIR}"

# Resolve DB user/name from .env if present, else use compose defaults.
# We deliberately do NOT `source` .env — it contains free-form values
# like SMTP_FROM_NAME='BarnardHQ Drone Operations' that break sh parsing.
DB_USER="${DRONEOPS_DB_USER:-droneops}"
DB_NAME="${DRONEOPS_DB_NAME:-droneops}"
if [[ -f "${REPO_ROOT}/.env" ]]; then
  pg_user_line="$(grep -E '^POSTGRES_USER=' "${REPO_ROOT}/.env" | head -1 | cut -d= -f2-)"
  pg_db_line="$(grep -E '^POSTGRES_DB=' "${REPO_ROOT}/.env" | head -1 | cut -d= -f2-)"
  [[ -n "${pg_user_line}" ]] && POSTGRES_USER="${pg_user_line}"
  [[ -n "${pg_db_line}"   ]] && POSTGRES_DB="${pg_db_line}"
fi

if ! docker ps --format '{{.Names}}' | grep -qx "${DB_CONTAINER}"; then
  fail "container ${DB_CONTAINER} is not running"
fi

# --- 1. Local gzipped pg_dump (durable on-host copy, 14-day retention) -------
echo "[snapshot] $(date -u +%FT%TZ) starting pg_dump -> ${OUT}"
if ! docker exec -i "${DB_CONTAINER}" \
      pg_dump --no-owner --no-privileges --format=plain \
        -U "${POSTGRES_USER:-${DB_USER}}" \
        -d "${POSTGRES_DB:-${DB_NAME}}" \
      | gzip -9 > "${OUT}"; then
  rm -f "${OUT}"
  fail "pg_dump|gzip to ${OUT} failed"
fi
chmod 600 "${OUT}"
# Guard against a truncated/empty dump masquerading as success.
if [[ ! -s "${OUT}" ]] || ! gzip -t "${OUT}" 2>/dev/null; then
  fail "local dump ${OUT} is empty or not a valid gzip"
fi

# --- 2. Source R2 creds (off-host stages) -----------------------------------
[[ -r "${ENV_FILE}" ]] || fail "R2 env file not readable: ${ENV_FILE}"
set +x
# shellcheck disable=SC1090
source "${ENV_FILE}"
: "${OBS_GLITCHTIP_BACKUPS_R2_ENDPOINT:?OBS_GLITCHTIP_BACKUPS_R2_ENDPOINT unset}"
: "${OBS_GLITCHTIP_BACKUPS_R2_BUCKET:?OBS_GLITCHTIP_BACKUPS_R2_BUCKET unset}"
: "${OBS_GLITCHTIP_BACKUPS_R2_ACCESS_KEY_ID:?OBS_GLITCHTIP_BACKUPS_R2_ACCESS_KEY_ID unset}"
: "${OBS_GLITCHTIP_BACKUPS_R2_SECRET_ACCESS_KEY:?OBS_GLITCHTIP_BACKUPS_R2_SECRET_ACCESS_KEY unset}"
BUCKET="${OBS_GLITCHTIP_BACKUPS_R2_BUCKET}"
DB_KEY="droneops/db/$(date -u +%Y/%m/%d)/droneops-${TIMESTAMP}.sql.gz"

# --- 3. Push the DB dump off-host to R2 -------------------------------------
echo "[snapshot] pushing db dump -> s3://${BUCKET}/${DB_KEY}"
if ! docker run --rm -i --network host \
      -e AWS_ACCESS_KEY_ID="${OBS_GLITCHTIP_BACKUPS_R2_ACCESS_KEY_ID}" \
      -e AWS_SECRET_ACCESS_KEY="${OBS_GLITCHTIP_BACKUPS_R2_SECRET_ACCESS_KEY}" \
      -e AWS_EC2_METADATA_DISABLED=true \
      "${AWSCLI_IMAGE}" s3 cp \
        --endpoint-url "${OBS_GLITCHTIP_BACKUPS_R2_ENDPOINT}" \
        --content-type application/gzip \
        - "s3://${BUCKET}/${DB_KEY}" < "${OUT}" >/dev/null; then
  fail "R2 upload of db dump ${DB_KEY} failed"
fi

# --- 4. Incremental sync of the uploads/ tree (signed-TOS + flight logs) -----
# uploads/tos_signed holds signed legal PDFs; uploads/ also holds flight logs.
# Mount the volume read-only into the aws-cli container and `s3 sync`
# (size+mtime incremental). This REPLACES the pre-2026-07-16 broken tar of
# ${REPO_ROOT}/data/tos_signed (a path that never existed).
echo "[snapshot] syncing ${APP_DATA_VOLUME}:/uploads -> s3://${BUCKET}/droneops/uploads/"
if ! docker run --rm --network host \
      -e AWS_ACCESS_KEY_ID="${OBS_GLITCHTIP_BACKUPS_R2_ACCESS_KEY_ID}" \
      -e AWS_SECRET_ACCESS_KEY="${OBS_GLITCHTIP_BACKUPS_R2_SECRET_ACCESS_KEY}" \
      -e AWS_EC2_METADATA_DISABLED=true \
      -v "${APP_DATA_VOLUME}":/data:ro \
      "${AWSCLI_IMAGE}" s3 sync \
        --endpoint-url "${OBS_GLITCHTIP_BACKUPS_R2_ENDPOINT}" \
        --only-show-errors \
        /data/uploads "s3://${BUCKET}/droneops/uploads/"; then
  fail "R2 sync of ${APP_DATA_VOLUME} uploads/ failed"
fi

# --- 5. Full success: stamp freshness, then local retention housekeeping -----
emit_freshness_metric

# Retention sweep — never deletes the most recent file even if older than N days.
# Runs AFTER the freshness stamp so a housekeeping hiccup can't block the metric;
# still fatal (no silent swallow) so a broken sweep is surfaced.
find "${BACKUP_DIR}" -maxdepth 1 -type f -name 'droneops-*.sql.gz' \
  -mtime "+${RETENTION_DAYS}" -print -delete

echo "[snapshot] done. db=$(du -h "${OUT}" | cut -f1) r2_db=${DB_KEY} uploads=synced"
