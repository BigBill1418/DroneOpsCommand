#!/usr/bin/env bash
# DroneOps Command quarterly RESTORE DRILL — proves the OFF-HOST backup leg is
# actually restorable, not just present. Downloads the newest droneops DB dump
# from Cloudflare R2 (the copy that matters in a real disaster), restores it
# into a throwaway database on the standby container, sanity-checks row counts
# against the live database, then drops the throwaway DB.
#
# Scheduled by systemd (droneops-restore-drill.timer, quarterly on the 16th of
# Jan/Apr/Jul/Oct at 16:23 UTC, Persistent=true). Unit files live in
# scripts/systemd/; install with:
#   sudo cp scripts/systemd/droneops-restore-drill.{service,timer} /etc/systemd/system/
#   sudo systemctl daemon-reload && sudo systemctl enable --now droneops-restore-drill.timer
#
# On FULL success writes the node-exporter textfile metric
# droneops_restore_drill_last_success_timestamp_seconds; the Grafana rule
# obs-rule-droneops-restore-drill-stale pages (infrawatch-alerts) if the drill
# hasn't succeeded in >100 days or the metric is absent — so a silently-dead
# timer cannot recreate the "someone has to remember quarterly" gap.
# Success also publishes ONE default-priority ntfy note (4x/year, quarterly
# digest class per ADR-0037); any failure publishes `high` and exits non-zero.
set -euo pipefail

DB_CONTAINER="droneops-standby-db"          # promoted primary (post-2026-04-20 topology)
DRILL_DB="droneops_restore_drill"
LIVE_DB="${DRONEOPS_DB_NAME:-droneops}"
DB_USER="${DRONEOPS_DB_USER:-droneops}"

ENV_FILE="${DRONEOPS_R2_ENV_FILE:-/opt/observability/.env}"
NTFY_TOPIC="${NTFY_TOPIC:-infrawatch-alerts}"
NTFY_HELPER="${NTFY_HELPER:-${HOME}/.local/bin/ntfy-publish.sh}"
AWSCLI_IMAGE="${AWSCLI_IMAGE:-amazon/aws-cli}"
TEXTFILE_DIR="${TEXTFILE_DIR:-/var/lib/node_exporter/textfile_collector}"
TEXTFILE_METRIC="droneops_restore_drill_last_success_timestamp_seconds"
TEXTFILE_PATH="${TEXTFILE_DIR}/droneops_restore_drill.prom"

# Drill dump must contain at least this fraction of the live flights count —
# the dump is nightly so it can trail live slightly, but a near-empty restore
# that "succeeds" is exactly the failure mode this drill exists to catch.
MIN_FLIGHTS_RATIO_PCT=90

WORKDIR="$(mktemp -d /tmp/droneops-restore-drill.XXXXXX)"

notify() { # notify <priority> <title> <extra-helper-args...> -- <msg>
  local prio="$1" title="$2"; shift 2
  if [[ -x "${NTFY_HELPER}" ]]; then
    "${NTFY_HELPER}" --topic "${NTFY_TOPIC}" --priority "${prio}" \
      --title "${title}" "$@" || true
  fi
}

fail() {
  local msg="$1"
  echo "[restore-drill] FAIL: ${msg}" >&2
  notify high "[DroneOps] quarterly restore drill FAILED" \
    --tags "backup,error" --dedup-key droneops-restore-drill --cooldown 21600 \
    -- "restore-drill.sh failed: ${msg}"
  exit 1
}

cleanup() {
  rm -rf "${WORKDIR}"
  # Best-effort: never leave the scratch DB behind, even on failure paths.
  docker exec "${DB_CONTAINER}" psql -U "${DB_USER}" -d "${LIVE_DB}" -qAtc \
    "DROP DATABASE IF EXISTS ${DRILL_DB} WITH (FORCE)" >/dev/null 2>&1 || true
}
trap cleanup EXIT

emit_freshness_metric() {
  local now tmp
  now="$(date -u +%s)"
  if ! tmp="$(mktemp "${TEXTFILE_DIR}/.droneops_restore_drill.prom.XXXXXX" 2>/dev/null)"; then
    echo "[restore-drill] WARN: could not create temp file in ${TEXTFILE_DIR}; freshness metric not updated" >&2
    return 0
  fi
  {
    echo "# HELP ${TEXTFILE_METRIC} Unix epoch of the last successful droneops R2 restore drill."
    echo "# TYPE ${TEXTFILE_METRIC} gauge"
    echo "${TEXTFILE_METRIC} ${now}"
  } > "${tmp}" 2>/dev/null || { rm -f "${tmp}"; return 0; }
  chmod 0644 "${tmp}" 2>/dev/null || true
  mv -f "${tmp}" "${TEXTFILE_PATH}" 2>/dev/null || { rm -f "${tmp}"; return 0; }
  echo "[restore-drill] freshness metric updated: ${TEXTFILE_METRIC}=${now}"
}

docker ps --format '{{.Names}}' | grep -qx "${DB_CONTAINER}" \
  || fail "container ${DB_CONTAINER} is not running"

# --- 1. Source R2 creds ------------------------------------------------------
[[ -r "${ENV_FILE}" ]] || fail "R2 env file not readable: ${ENV_FILE}"
# shellcheck disable=SC1090
source "${ENV_FILE}"
: "${OBS_GLITCHTIP_BACKUPS_R2_ENDPOINT:?OBS_GLITCHTIP_BACKUPS_R2_ENDPOINT unset}"
: "${OBS_GLITCHTIP_BACKUPS_R2_BUCKET:?OBS_GLITCHTIP_BACKUPS_R2_BUCKET unset}"
: "${OBS_GLITCHTIP_BACKUPS_R2_ACCESS_KEY_ID:?OBS_GLITCHTIP_BACKUPS_R2_ACCESS_KEY_ID unset}"
: "${OBS_GLITCHTIP_BACKUPS_R2_SECRET_ACCESS_KEY:?OBS_GLITCHTIP_BACKUPS_R2_SECRET_ACCESS_KEY unset}"
BUCKET="${OBS_GLITCHTIP_BACKUPS_R2_BUCKET}"

r2() {
  docker run --rm -i --network host \
    -e AWS_ACCESS_KEY_ID="${OBS_GLITCHTIP_BACKUPS_R2_ACCESS_KEY_ID}" \
    -e AWS_SECRET_ACCESS_KEY="${OBS_GLITCHTIP_BACKUPS_R2_SECRET_ACCESS_KEY}" \
    -e AWS_EC2_METADATA_DISABLED=true \
    "${AWSCLI_IMAGE}" s3 "$@" --endpoint-url "${OBS_GLITCHTIP_BACKUPS_R2_ENDPOINT}"
}

# --- 2. Locate + download the newest dump in R2 ------------------------------
# Keys are date-pathed (droneops/db/YYYY/MM/DD/droneops-<TS>.sql.gz) so a
# lexical sort of the key column is chronological.
LATEST_KEY="$(r2 ls "s3://${BUCKET}/droneops/db/" --recursive 2>/dev/null \
  | awk '{print $4}' | grep '\.sql\.gz$' | sort | tail -1)"
[[ -n "${LATEST_KEY}" ]] || fail "no droneops db dumps found in s3://${BUCKET}/droneops/db/"

echo "[restore-drill] $(date -u +%FT%TZ) newest R2 dump: ${LATEST_KEY}"
DUMP="${WORKDIR}/$(basename "${LATEST_KEY}")"
r2 cp "s3://${BUCKET}/${LATEST_KEY}" - > "${DUMP}" \
  || fail "download of s3://${BUCKET}/${LATEST_KEY} failed"
[[ -s "${DUMP}" ]] && gzip -t "${DUMP}" 2>/dev/null \
  || fail "downloaded dump is empty or not a valid gzip"
echo "[restore-drill] downloaded $(du -h "${DUMP}" | cut -f1)"

# Refuse to certify a stale backup: the newest off-host dump must be <48h old.
DUMP_TS="$(basename "${LATEST_KEY}" | sed -E 's/^droneops-([0-9]{8})-([0-9]{6})\.sql\.gz$/\1 \2/')"
DUMP_EPOCH="$(date -u -d "${DUMP_TS:0:8} ${DUMP_TS:9:2}:${DUMP_TS:11:2}:${DUMP_TS:13:2}" +%s 2>/dev/null || echo 0)"
AGE_H=$(( ($(date -u +%s) - DUMP_EPOCH) / 3600 ))
[[ "${DUMP_EPOCH}" -gt 0 && "${AGE_H}" -lt 48 ]] \
  || fail "newest R2 dump is ${AGE_H}h old (>48h) — nightly off-host push is broken"

# --- 3. Restore into a throwaway database ------------------------------------
docker exec "${DB_CONTAINER}" psql -U "${DB_USER}" -d "${LIVE_DB}" -qAtc \
  "DROP DATABASE IF EXISTS ${DRILL_DB} WITH (FORCE)" >/dev/null
docker exec "${DB_CONTAINER}" psql -U "${DB_USER}" -d "${LIVE_DB}" -qAtc \
  "CREATE DATABASE ${DRILL_DB}" >/dev/null

echo "[restore-drill] restoring into ${DRILL_DB}"
gunzip -c "${DUMP}" | docker exec -i "${DB_CONTAINER}" \
  psql -U "${DB_USER}" -d "${DRILL_DB}" -q -v ON_ERROR_STOP=1 >/dev/null \
  || fail "psql restore into ${DRILL_DB} failed"

# --- 4. Sanity-check restored data vs live -----------------------------------
count() { # count <db> <table>
  docker exec "${DB_CONTAINER}" psql -U "${DB_USER}" -d "$1" -qAtc \
    "SELECT count(*) FROM $2"
}
DRILL_FLIGHTS="$(count "${DRILL_DB}" flights)"       || fail "flights table missing in restored DB"
DRILL_BATT="$(count "${DRILL_DB}" battery_logs)"     || fail "battery_logs table missing in restored DB"
DRILL_TOS="$(count "${DRILL_DB}" tos_acceptances)"   || fail "tos_acceptances table missing in restored DB"
LIVE_FLIGHTS="$(count "${LIVE_DB}" flights)"

[[ "${DRILL_FLIGHTS}" -gt 0 && "${DRILL_BATT}" -gt 0 && "${DRILL_TOS}" -gt 0 ]] \
  || fail "restored counts empty: flights=${DRILL_FLIGHTS} battery_logs=${DRILL_BATT} tos_acceptances=${DRILL_TOS}"
[[ $(( DRILL_FLIGHTS * 100 )) -ge $(( LIVE_FLIGHTS * MIN_FLIGHTS_RATIO_PCT )) ]] \
  || fail "restored flights=${DRILL_FLIGHTS} < ${MIN_FLIGHTS_RATIO_PCT}% of live=${LIVE_FLIGHTS}"

echo "[restore-drill] verified: flights=${DRILL_FLIGHTS}/${LIVE_FLIGHTS} battery_logs=${DRILL_BATT} tos_acceptances=${DRILL_TOS}"

# --- 5. Success: stamp metric, quarterly OK note (cleanup trap drops the DB) --
emit_freshness_metric
notify default "[DroneOps] quarterly restore drill OK" \
  --tags "backup,white_check_mark" --dedup-key droneops-restore-drill-ok --cooldown 86400 \
  -- "Restored ${LATEST_KEY} (${AGE_H}h old) from R2 into ${DRILL_DB}: flights=${DRILL_FLIGHTS}/${LIVE_FLIGHTS}, battery_logs=${DRILL_BATT}, tos_acceptances=${DRILL_TOS}. Scratch DB dropped."
echo "[restore-drill] done. key=${LATEST_KEY} flights=${DRILL_FLIGHTS}/${LIVE_FLIGHTS}"
