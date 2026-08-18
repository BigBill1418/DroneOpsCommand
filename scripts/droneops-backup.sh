#!/usr/bin/env bash
# DroneOps Command — comprehensive ENCRYPTED backup to Cloudflare R2 (ADR-0041).
#
# Supersedes scripts/snapshot.sh (plaintext `aws s3 cp` + `s3 sync`). Every
# artifact needed for a from-nothing rebuild is captured, client-side encrypted
# by restic, retained off-host, and integrity-checked on every run.
#
# Four lanes, each its own restic tag:
#   db      pg_dump -Fc -Z0 of `droneops` on droneops-standby-db  --stdin
#   files   droneops_app_data -> /data/uploads + /data/reports
#   config  ~/droneops/.env + compose files + crontab + volume list + git HEAD
#   legacy  one-shot pre-migration dump (see --legacy below)
#
# ---------------------------------------------------------------------------
# WHY THE DUMP IS NOT COMPRESSED BEFORE restic (ADR-0041 D1 — do not "optimise")
# ---------------------------------------------------------------------------
# `pg_dump -Fc` applies its own per-block zlib by default, and `| gzip` is
# worse still. Either produces a completely different byte stream every night,
# so restic's content-defined chunking finds ~zero shared blocks and stores a
# fresh ~55 MB daily forever. `-Z0` hands restic stable byte runs; restic then
# dedups the ~99% that did not change and applies its own zstd. This is the
# difference between ~10 MB/day and ~55 MB/day of new R2 data. Prove it, do not
# assume it:  restic stats --mode raw-data   before and after a second run.
#
# ---------------------------------------------------------------------------
# FAIL CLOSED
# ---------------------------------------------------------------------------
# Every error path calls fail(): ntfy `high` + non-zero exit. This deliberately
# does NOT copy DR3-Vision's dr3-pg-backup.sh `exit 0`-when-env-missing idiom —
# that is the silent-death class that let the old HSH-HQ path die unnoticed on
# 2026-04-15 and go unnoticed for four months. A missing secrets file is a
# LOUD failure here, not a quiet no-op.
#
# The node-exporter freshness metric is stamped ONLY after every lane, the
# retention pass and the integrity check have all succeeded.
#
# ---------------------------------------------------------------------------
# Usage
#   droneops-backup.sh                 # normal run: db + files + config lanes
#   droneops-backup.sh --legacy PATH   # one-shot: archive PATH under tag `legacy`
#
# Secrets (mode 600, dir 700), NOT in git — mirrored to the 1Password Fleet
# vault as "DroneOps Command Backup Restic Password" (THE recovery key) and
# "DroneOps Command Backup R2 Credentials":
#   ~/.droneops-secrets/restic-droneops.env
#     R2_ENDPOINT= R2_BUCKET= R2_ACCESS_KEY_ID= R2_SECRET_ACCESS_KEY= RESTIC_PASSWORD=
#
# Scheduling: droneops-backup.timer (03:23 + 15:23 UTC, Persistent=true).
# Restore:    docs/runbooks/droneops-backup-restore.md
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

SECRETS_ENV="${DRONEOPS_RESTIC_ENV_FILE:-${HOME}/.droneops-secrets/restic-droneops.env}"
# Pinned image: the tool version is declared in the repo and cannot drift with
# an apt upgrade of the host's restic (0.16.4).
RESTIC_IMAGE="${RESTIC_IMAGE:-restic/restic:0.17.3}"

DB_CONTAINER="${DRONEOPS_DB_CONTAINER:-droneops-standby-db}"
APP_DATA_VOLUME="${DRONEOPS_APP_DATA_VOLUME:-droneops_app_data}"

# Local plain .sql.gz + 14-day sweep is KEPT deliberately: it is the break-glass
# restore path that needs no RESTIC_PASSWORD at all. Removing it would make a
# lost repository password unrecoverable (ADR-0041, Option C mitigations).
BACKUP_DIR="${DRONEOPS_BACKUP_DIR:-${REPO_ROOT}/backups}"
RETENTION_DAYS="${DRONEOPS_BACKUP_RETENTION_DAYS:-14}"

# Retention enforced IN R2 (closes ADR-0041 Gap 4 — the old find -mtime sweep
# pruned only the local copy).
KEEP_DAILY=14
KEEP_WEEKLY=8
KEEP_MONTHLY=24
KEEP_YEARLY=unlimited   # operator decision 2026-08-18: yearly snapshots are kept forever (was 7)

NTFY_TOPIC="${NTFY_TOPIC:-infrawatch-alerts}"
NTFY_HELPER="${NTFY_HELPER:-${HOME}/.local/bin/ntfy-publish.sh}"
NTFY_CLICK="https://noc-mastercontrol.barnardhq.com/status/droneops"

# ⚠️ DO NOT RENAME. Consumed by obs-rule-droneops-backup-stale in
# /opt/infrawatch/grafana/provisioning/alerting/observability-alerts.yml
# (>28h, or absent()*9999, severity high). Renaming without editing that YAML in
# the same change converts a live alert into a permanently-green dead man.
TEXTFILE_DIR="${TEXTFILE_DIR:-/var/lib/node_exporter/textfile_collector}"
TEXTFILE_METRIC="droneops_backup_last_success_timestamp_seconds"
TEXTFILE_PATH="${TEXTFILE_DIR}/droneops_backup.prom"

TIMESTAMP="$(date -u +%Y%m%d-%H%M%S)"
OUT="${BACKUP_DIR}/droneops-${TIMESTAMP}.sql.gz"

LEGACY_FILE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --legacy) LEGACY_FILE="${2:?--legacy requires a path}"; shift 2;;
    *) echo "usage: $(basename "$0") [--legacy PATH]" >&2; exit 2;;
  esac
done

WORKDIR="$(mktemp -d /tmp/droneops-backup.XXXXXX)"
chmod 700 "${WORKDIR}"
cleanup() { rm -rf "${WORKDIR}"; }
trap cleanup EXIT

log() { echo "[droneops-backup] $(date -u +%FT%TZ) $*"; }

fail() {
  local msg="$1"
  echo "[droneops-backup] FAIL: ${msg}" >&2
  if [[ -x "${NTFY_HELPER}" ]]; then
    "${NTFY_HELPER}" --topic "${NTFY_TOPIC}" --priority high \
      --title "[DroneOps Command] nightly backup FAILED" \
      --tags "backup,error" \
      --click "${NTFY_CLICK}" \
      --dedup-key droneops-backup --cooldown 21600 \
      -- "droneops-backup.sh failed: ${msg}" || true
  fi
  exit 1
}

# --- Single-instance lock ---------------------------------------------------
# systemd will not start a second droneops-backup.service while one is active,
# but the runbook (§6) tells operators to run this script BY HAND, and a manual
# run overlapping a timer run collides on restic's EXCLUSIVE repository lock
# during `forget --prune` / `check` — turning a benign overlap into a fail()
# page. Two concurrent pg_dumps of the same database is also pure waste.
#
# An overlap exits 0 WITHOUT stamping the freshness metric. That asymmetry is
# deliberate: a one-off overlap must not page, while a PERSISTENT overlap stops
# advancing the metric and is therefore caught within 28 h by
# obs-rule-droneops-backup-stale. Failing loudly here would page on the benign
# case; exiting 0 with a stamped metric would hide the pathological one.
#
# Declared after fail()/log() because it uses them.
LOCK_PATH="${DRONEOPS_BACKUP_LOCK:-${HOME}/.droneops-backup.lock}"
exec 9>"${LOCK_PATH}" || fail "cannot open lock file ${LOCK_PATH}"
if ! flock -n 9; then
  log "another droneops-backup run holds ${LOCK_PATH}; exiting 0 without stamping the freshness metric"
  exit 0
fi

# Atomic node-exporter textfile write (mktemp + mv -f). Best-effort by design:
# a textfile hiccup must not fail an otherwise-good backup. Called ONLY after
# full success of every lane.
emit_freshness_metric() {
  local now tmp
  now="$(date -u +%s)"
  if ! tmp="$(mktemp "${TEXTFILE_DIR}/.droneops_backup.prom.XXXXXX" 2>/dev/null)"; then
    echo "[droneops-backup] WARN: could not create temp file in ${TEXTFILE_DIR}; freshness metric not updated" >&2
    return 0
  fi
  {
    echo "# HELP ${TEXTFILE_METRIC} Unix epoch of the last fully successful droneops restic backup (db+files+config -> R2)."
    echo "# TYPE ${TEXTFILE_METRIC} gauge"
    echo "${TEXTFILE_METRIC} ${now}"
  } > "${tmp}" 2>/dev/null || { rm -f "${tmp}"; return 0; }
  chmod 0644 "${tmp}" 2>/dev/null || true
  mv -f "${tmp}" "${TEXTFILE_PATH}" 2>/dev/null || { rm -f "${tmp}"; return 0; }
  log "freshness metric updated: ${TEXTFILE_METRIC}=${now}"
}

# --- Credentials ------------------------------------------------------------
# Fail CLOSED on a missing/unreadable secrets file (see header).
[[ -r "${SECRETS_ENV}" ]] || fail "restic secrets file not readable: ${SECRETS_ENV} (see ADR-0041 §D2 / 1Password Fleet vault)"
set -a
# shellcheck disable=SC1090
source "${SECRETS_ENV}"
set +a
: "${R2_ENDPOINT:?R2_ENDPOINT unset in ${SECRETS_ENV}}"
: "${R2_BUCKET:?R2_BUCKET unset in ${SECRETS_ENV}}"
: "${R2_ACCESS_KEY_ID:?R2_ACCESS_KEY_ID unset in ${SECRETS_ENV}}"
: "${R2_SECRET_ACCESS_KEY:?R2_SECRET_ACCESS_KEY unset in ${SECRETS_ENV}}"
: "${RESTIC_PASSWORD:?RESTIC_PASSWORD unset in ${SECRETS_ENV}}"

# Translate to restic-native names in a mode-600 file inside the 700 WORKDIR.
# Deliberately NOT passed as `docker run -e` args: that would put the R2 secret
# and the repository password into /proc/<pid>/cmdline, readable by any local
# process for the lifetime of the container.
RESTIC_ENV="${WORKDIR}/restic.env"
( umask 077
  {
    printf 'RESTIC_REPOSITORY=s3:%s/%s/restic\n' "${R2_ENDPOINT}" "${R2_BUCKET}"
    printf 'RESTIC_PASSWORD=%s\n' "${RESTIC_PASSWORD}"
    printf 'AWS_ACCESS_KEY_ID=%s\n' "${R2_ACCESS_KEY_ID}"
    printf 'AWS_SECRET_ACCESS_KEY=%s\n' "${R2_SECRET_ACCESS_KEY}"
  } > "${RESTIC_ENV}"
)

# restic_do [SRC:DST:ro ...] -- <restic args...>
restic_do() {
  local mounts=()
  while [[ "${1:-}" != "--" ]]; do
    [[ $# -gt 0 ]] || { echo "restic_do: missing -- separator" >&2; return 2; }
    mounts+=("-v" "$1"); shift
  done
  shift
  docker run --rm -i --network host --env-file "${RESTIC_ENV}" \
    ${mounts[@]+"${mounts[@]}"} "${RESTIC_IMAGE}" "$@"
}

# --- Preflight --------------------------------------------------------------
docker ps --format '{{.Names}}' | grep -qx "${DB_CONTAINER}" \
  || fail "container ${DB_CONTAINER} is not running"

# Idempotent init so a fresh host does not need a manual bootstrap step.
if ! restic_do -- cat config >/dev/null 2>&1; then
  log "repository not initialised; running restic init"
  restic_do -- init || fail "restic init failed against s3:${R2_ENDPOINT}/${R2_BUCKET}/restic (check R2 credentials)"
fi

# --- Lane: legacy (one-shot) ------------------------------------------------
if [[ -n "${LEGACY_FILE}" ]]; then
  [[ -f "${LEGACY_FILE}" ]] || fail "legacy file not found: ${LEGACY_FILE}"
  legacy_dir="$(cd "$(dirname "${LEGACY_FILE}")" && pwd)"
  legacy_name="$(basename "${LEGACY_FILE}")"
  log "legacy lane: archiving ${LEGACY_FILE} ($(du -h "${LEGACY_FILE}" | cut -f1))"
  restic_do "${legacy_dir}:/legacy:ro" -- backup "/legacy/${legacy_name}" --tag legacy \
    || fail "restic backup of legacy file ${LEGACY_FILE} failed"
  restic_do -- snapshots --tag legacy
  log "legacy lane done (no freshness metric — this is a one-shot, not a scheduled backup)"
  exit 0
fi

# Resolve DB user/name from .env if present. We deliberately do NOT `source`
# .env — it holds free-form values (SMTP_FROM_NAME='BarnardHQ Drone Operations')
# that break sh parsing.
DB_USER="${DRONEOPS_DB_USER:-droneops}"
DB_NAME="${DRONEOPS_DB_NAME:-droneops}"
if [[ -f "${REPO_ROOT}/.env" ]]; then
  v="$(grep -E '^POSTGRES_USER=' "${REPO_ROOT}/.env" | head -1 | cut -d= -f2-)"; [[ -n "${v}" ]] && DB_USER="${v}"
  v="$(grep -E '^POSTGRES_DB=' "${REPO_ROOT}/.env" | head -1 | cut -d= -f2-)"; [[ -n "${v}" ]] && DB_NAME="${v}"
fi

mkdir -p "${BACKUP_DIR}"
chmod 700 "${BACKUP_DIR}"

# --- Lane: db ---------------------------------------------------------------
# ONE dump serves both destinations, so the off-host and break-glass copies are
# the same consistent snapshot: -Fc -Z0 feeds restic (dedup-friendly), and
# pg_restore converts that same archive to the plain SQL the local .sql.gz
# break-glass path has always used.
DUMP="${WORKDIR}/droneops.dump"
log "db lane: pg_dump -Fc -Z0 ${DB_NAME}"
docker exec -i "${DB_CONTAINER}" \
  pg_dump --no-owner --no-privileges -Fc -Z0 -U "${DB_USER}" -d "${DB_NAME}" > "${DUMP}" \
  || fail "pg_dump of ${DB_NAME} failed"
[[ -s "${DUMP}" ]] || fail "pg_dump produced an empty archive"
# A dump that "succeeded" into garbage is exactly what this guard is for.
docker exec -i "${DB_CONTAINER}" pg_restore -l < "${DUMP}" >/dev/null \
  || fail "pg_dump archive failed pg_restore -l validation (truncated or corrupt)"
log "db lane: archive $(du -h "${DUMP}" | cut -f1) validated; pushing to restic"
restic_do -- backup --stdin --stdin-filename droneops.dump --tag db < "${DUMP}" \
  || fail "restic backup of the db lane failed"

# Local break-glass copy (plain SQL, gzip) — no RESTIC_PASSWORD required.
log "db lane: writing local break-glass copy ${OUT}"
docker exec -i "${DB_CONTAINER}" pg_restore --no-owner --no-privileges -f - < "${DUMP}" \
  | gzip -9 > "${OUT}" \
  || { rm -f "${OUT}"; fail "local break-glass dump ${OUT} failed"; }
chmod 600 "${OUT}"
[[ -s "${OUT}" ]] && gzip -t "${OUT}" 2>/dev/null \
  || fail "local dump ${OUT} is empty or not a valid gzip"

# --- Lane: files ------------------------------------------------------------
# /data/reports closes Gap 6; snapshotting (rather than `s3 sync`) closes Gap 3 —
# a corrupted flight log is now recoverable instead of silently mirrored over.
log "files lane: ${APP_DATA_VOLUME} -> /data/uploads + /data/reports"
restic_do "${APP_DATA_VOLUME}:/data:ro" -- backup /data/uploads /data/reports --tag files \
  || fail "restic backup of the files lane failed"

# --- Lane: config (closes Gap 5, the critical one) --------------------------
# Without these, a restore of db+files still will not boot: compose refuses to
# start on the ADR-0012 `:?` guards and every issued JWT is invalid.
#
# This is an explicit ALLOWLIST, not an exclude pattern: `.env.bak-*` and
# `*.bak-rotate-*` hold ROTATED secrets and are structurally unable to be
# picked up here. An exclude list would have to stay in sync with new suffixes;
# an allowlist cannot drift open.
STAGE="${WORKDIR}/config"
mkdir -p "${STAGE}"
chmod 700 "${STAGE}"
staged=0
for f in .env docker-compose.override.yml docker-compose.yml docker-compose.bos-prod.yml \
         docker-compose.standby.yml docker-compose.standby.override.yml .env.example; do
  if [[ -f "${REPO_ROOT}/${f}" ]]; then
    cp -p "${REPO_ROOT}/${f}" "${STAGE}/${f}" || fail "staging ${f} for the config lane failed"
    staged=$((staged + 1))
  fi
done
# .env is the single artifact that makes this lane load-bearing — its absence
# must never be silently tolerated.
[[ -f "${STAGE}/.env" ]] || fail "config lane: ${REPO_ROOT}/.env is missing — this is the artifact that closes ADR-0041 Gap 5"
crontab -l > "${STAGE}/crontab.txt" 2>/dev/null || echo "(no crontab)" > "${STAGE}/crontab.txt"
docker volume ls --format '{{.Name}}' | grep -i droneops > "${STAGE}/docker-volumes.txt" || true
git -C "${REPO_ROOT}" rev-parse HEAD > "${STAGE}/git-head.txt" 2>/dev/null || echo "(not a git checkout)" > "${STAGE}/git-head.txt"
log "config lane: ${staged} config files staged + crontab/volumes/git-HEAD"
restic_do "${STAGE}:/config:ro" -- backup /config --tag config \
  || fail "restic backup of the config lane failed"

# --- Retention (in R2) ------------------------------------------------------
# --group-by tags is REQUIRED: without it restic applies one policy across all
# lanes mixed together and prunes config/legacy wrongly.
#
# ⚠️ Each lane carries exactly ONE stable tag. Do NOT add a per-run date tag:
# --group-by tags groups on the FULL tag set, so `db,2026-08-17` and
# `db,2026-08-18` would be separate single-member groups and `forget` would
# silently become a no-op — leaving Gap 4 open while looking configured.
log "retention: forget --prune ${KEEP_DAILY}d/${KEEP_WEEKLY}w/${KEEP_MONTHLY}m/${KEEP_YEARLY}y --group-by tags"
restic_do -- forget --prune \
  --keep-daily "${KEEP_DAILY}" --keep-weekly "${KEEP_WEEKLY}" \
  --keep-monthly "${KEEP_MONTHLY}" --keep-yearly "${KEEP_YEARLY}" \
  --group-by tags \
  || fail "restic forget --prune failed"

# --- Integrity --------------------------------------------------------------
# Structure every run; actually READ 5% of pack data on Sundays, which catches
# bit-rot the metadata check cannot see.
if [[ "$(date -u +%u)" == "7" ]]; then
  log "integrity: restic check --read-data-subset=5% (Sunday)"
  restic_do -- check --read-data-subset=5% || fail "restic check --read-data-subset=5% failed (possible bit-rot in R2)"
else
  log "integrity: restic check"
  restic_do -- check || fail "restic check failed (repository structure damaged)"
fi

# --- Full success -----------------------------------------------------------
emit_freshness_metric

# Local sweep runs AFTER the metric so a housekeeping hiccup cannot block the
# freshness stamp; still fatal, so a broken sweep is surfaced rather than
# swallowed. Never touches the most recent file (it is younger than N days).
find "${BACKUP_DIR}" -maxdepth 1 -type f -name 'droneops-*.sql.gz' \
  -mtime "+${RETENTION_DAYS}" -print -delete \
  || fail "local retention sweep of ${BACKUP_DIR} failed (break-glass copies may be growing unbounded)"

log "done. local=$(du -h "${OUT}" | cut -f1) repo=s3:${R2_BUCKET}/restic lanes=db,files,config"
# Cosmetic tail only. Explicitly non-fatal: the backup is already complete and
# stamped, so a transient R2 hiccup listing snapshots must not mark the unit
# failed — a `set -e` exit here would leave systemd red against a green metric,
# which is a contradiction an operator would waste time chasing.
restic_do -- snapshots --latest 3 || true
