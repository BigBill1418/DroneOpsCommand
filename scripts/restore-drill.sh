#!/usr/bin/env bash
# DroneOps Command quarterly RESTORE DRILL — proves the OFF-HOST backup leg is
# actually restorable, not just present. Pulls the newest `db`-tagged snapshot
# from the encrypted restic repository in Cloudflare R2 (the copy that matters
# in a real disaster), restores it into a throwaway database on the standby
# container, sanity-checks row counts against the live database, then drops the
# throwaway DB.
#
# ADR-0041 (2026-08-17): migrated from `aws s3 cp` of a plaintext .sql.gz to
# restic. Two consequences worth knowing before you read further:
#   - the artifact is now a pg_dump CUSTOM archive (-Fc), so the restore is
#     `pg_restore`, not `gunzip | psql`;
#   - the drill now also verifies the `config` lane, by restoring .env and
#     sha256-comparing it against the live file. That assertion is the ONLY
#     thing that proves ADR-0041 Gap 5 (host config not backed up -> a restore
#     that cannot boot) is actually closed, so it is fatal, not advisory.
#
# 2026-08-17 (DR rehearsal review): a `files` lane assertion was added. The
# drill previously certified "the backup restores" while never reading the
# largest lane. It now dumps one executed TOS PDF and checks it against the
# sha256 the DATABASE recorded at signing time — a cross-lane check, so it
# cannot go vacuous the way a file-count floor would.
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

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

DB_CONTAINER="droneops-standby-db"          # promoted primary (post-2026-04-20 topology)
DRILL_DB="droneops_restore_drill"
LIVE_DB="${DRONEOPS_DB_NAME:-droneops}"
DB_USER="${DRONEOPS_DB_USER:-droneops}"

SECRETS_ENV="${DRONEOPS_RESTIC_ENV_FILE:-${HOME}/.droneops-secrets/restic-droneops.env}"
RESTIC_IMAGE="${RESTIC_IMAGE:-restic/restic:0.17.3}"

NTFY_TOPIC="${NTFY_TOPIC:-infrawatch-alerts}"
NTFY_HELPER="${NTFY_HELPER:-${HOME}/.local/bin/ntfy-publish.sh}"
NTFY_CLICK="https://noc-mastercontrol.barnardhq.com/status/droneops"
TEXTFILE_DIR="${TEXTFILE_DIR:-/var/lib/node_exporter/textfile_collector}"
TEXTFILE_METRIC="droneops_restore_drill_last_success_timestamp_seconds"
TEXTFILE_PATH="${TEXTFILE_DIR}/droneops_restore_drill.prom"

# Drill dump must contain at least this fraction of the live flights count —
# the dump is nightly so it can trail live slightly, but a near-empty restore
# that "succeeds" is exactly the failure mode this drill exists to catch.
MIN_FLIGHTS_RATIO_PCT=90

WORKDIR="$(mktemp -d /tmp/droneops-restore-drill.XXXXXX)"
chmod 700 "${WORKDIR}"

notify() { # notify <priority> <title> <extra-helper-args...> -- <msg>
  local prio="$1" title="$2"; shift 2
  if [[ -x "${NTFY_HELPER}" ]]; then
    "${NTFY_HELPER}" --topic "${NTFY_TOPIC}" --priority "${prio}" \
      --title "${title}" --click "${NTFY_CLICK}" "$@" || true
  fi
}

fail() {
  local msg="$1"
  echo "[restore-drill] FAIL: ${msg}" >&2
  notify high "[DroneOps Command] quarterly restore drill FAILED" \
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

# --- 1. Credentials (fail closed) -------------------------------------------
[[ -r "${SECRETS_ENV}" ]] || fail "restic secrets file not readable: ${SECRETS_ENV}"
set -a
# shellcheck disable=SC1090
source "${SECRETS_ENV}"
set +a
: "${R2_ENDPOINT:?R2_ENDPOINT unset in ${SECRETS_ENV}}"
: "${R2_BUCKET:?R2_BUCKET unset in ${SECRETS_ENV}}"
: "${R2_ACCESS_KEY_ID:?R2_ACCESS_KEY_ID unset in ${SECRETS_ENV}}"
: "${R2_SECRET_ACCESS_KEY:?R2_SECRET_ACCESS_KEY unset in ${SECRETS_ENV}}"
: "${RESTIC_PASSWORD:?RESTIC_PASSWORD unset in ${SECRETS_ENV}}"

# restic-native env in a mode-600 file: keeps the repository password and the R2
# secret out of `docker run` argv (i.e. out of /proc/<pid>/cmdline).
RESTIC_ENV="${WORKDIR}/restic.env"
( umask 077
  {
    printf 'RESTIC_REPOSITORY=s3:%s/%s/restic\n' "${R2_ENDPOINT}" "${R2_BUCKET}"
    printf 'RESTIC_PASSWORD=%s\n' "${RESTIC_PASSWORD}"
    printf 'AWS_ACCESS_KEY_ID=%s\n' "${R2_ACCESS_KEY_ID}"
    printf 'AWS_SECRET_ACCESS_KEY=%s\n' "${R2_SECRET_ACCESS_KEY}"
  } > "${RESTIC_ENV}"
)

restic_do() {
  docker run --rm -i --network host --env-file "${RESTIC_ENV}" "${RESTIC_IMAGE}" "$@"
}

# --- 2. Locate + fetch the newest `db` snapshot ------------------------------
SNAP_JSON="$(restic_do snapshots --tag db --latest 1 --json 2>/dev/null)" \
  || fail "restic snapshots failed (repository unreachable or bad credentials)"
SNAP_ID="$(printf '%s' "${SNAP_JSON}" | jq -r '.[0].short_id // empty')"
SNAP_TIME="$(printf '%s' "${SNAP_JSON}" | jq -r '.[0].time // empty')"
[[ -n "${SNAP_ID}" ]] || fail "no db-tagged snapshots found in s3:${R2_BUCKET}/restic"

echo "[restore-drill] $(date -u +%FT%TZ) newest db snapshot: ${SNAP_ID} @ ${SNAP_TIME}"

# Refuse to certify a stale backup: the newest off-host snapshot must be <48h old.
SNAP_EPOCH="$(date -u -d "${SNAP_TIME}" +%s 2>/dev/null || echo 0)"
AGE_H=$(( ($(date -u +%s) - SNAP_EPOCH) / 3600 ))
[[ "${SNAP_EPOCH}" -gt 0 && "${AGE_H}" -lt 48 ]] \
  || fail "newest off-host db snapshot is ${AGE_H}h old (>48h) — the scheduled backup is broken"

DUMP="${WORKDIR}/droneops.dump"
restic_do dump "${SNAP_ID}" /droneops.dump > "${DUMP}" \
  || fail "restic dump of ${SNAP_ID}:/droneops.dump failed"
[[ -s "${DUMP}" ]] || fail "restored dump is empty"
docker exec -i "${DB_CONTAINER}" pg_restore -l < "${DUMP}" >/dev/null \
  || fail "restored artifact is not a valid pg_dump custom archive"
echo "[restore-drill] fetched $(du -h "${DUMP}" | cut -f1) from R2"

# --- 3. Restore into a throwaway database ------------------------------------
docker exec "${DB_CONTAINER}" psql -U "${DB_USER}" -d "${LIVE_DB}" -qAtc \
  "DROP DATABASE IF EXISTS ${DRILL_DB} WITH (FORCE)" >/dev/null
docker exec "${DB_CONTAINER}" psql -U "${DB_USER}" -d "${LIVE_DB}" -qAtc \
  "CREATE DATABASE ${DRILL_DB}" >/dev/null

echo "[restore-drill] restoring into ${DRILL_DB}"
docker exec -i "${DB_CONTAINER}" \
  pg_restore --no-owner --no-privileges --exit-on-error -U "${DB_USER}" -d "${DRILL_DB}" \
  < "${DUMP}" >/dev/null \
  || fail "pg_restore into ${DRILL_DB} failed"

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

# --- 4b. Config lane: prove ADR-0041 Gap 5 is closed -------------------------
# A DB restore alone does not bring the stack up — compose refuses to start on
# the ADR-0012 `:?` guards without .env, and every issued JWT dies with the lost
# JWT_SECRET_KEY. Restoring .env and byte-comparing it against the live file is
# the assertion that proves the config lane is real and current.
CFG_SNAP="$(restic_do snapshots --tag config --latest 1 --json 2>/dev/null | jq -r '.[0].short_id // empty')"
[[ -n "${CFG_SNAP}" ]] || fail "no config-tagged snapshot found — ADR-0041 Gap 5 is NOT closed"
restic_do dump "${CFG_SNAP}" /config/.env > "${WORKDIR}/env.restored" \
  || fail "restic dump of ${CFG_SNAP}:/config/.env failed"
[[ -s "${WORKDIR}/env.restored" ]] || fail "restored .env is empty"
RESTORED_SHA="$(sha256sum "${WORKDIR}/env.restored" | cut -d' ' -f1)"
LIVE_SHA="$(sha256sum "${REPO_ROOT}/.env" | cut -d' ' -f1)"
[[ "${RESTORED_SHA}" == "${LIVE_SHA}" ]] \
  || fail "config lane .env sha256 mismatch: restored=${RESTORED_SHA:0:16} live=${LIVE_SHA:0:16} (backup is stale or truncated)"
echo "[restore-drill] config lane verified: .env sha256 matches live (${LIVE_SHA:0:16}...)"

# --- 4c. Files lane: the largest lane, previously never restore-tested -------
# Until 2026-08-17 this drill certified "the backup restores" while never
# reading the `files` lane at all — 657 MiB of flight logs, report deliverables
# and executed TOS PDFs. A file-count floor would not fix that: it stays green
# against a stale or truncated snapshot (the count is right, the bytes are not).
#
# Instead this is a CROSS-LANE assertion. tos_acceptances.signed_sha256 is the
# hash the application recorded when it generated the PDF, so the db lane is
# used to check the files lane. Those two lanes can only agree if both are
# current and intact; a stale files snapshot fails immediately.
# signed_pdf_path is stored as /data/uploads/... — already the snapshot path.
FILES_SNAP="$(restic_do snapshots --tag files --latest 1 --json 2>/dev/null | jq -r '.[0].short_id // empty')"
[[ -n "${FILES_SNAP}" ]] || fail "no files-tagged snapshot found — client deliverables are NOT backed up"
TOS_ROW="$(docker exec "${DB_CONTAINER}" psql -U "${DB_USER}" -d "${DRILL_DB}" -qAtF'|' -c \
  "SELECT signed_pdf_path, signed_sha256 FROM tos_acceptances
    WHERE signed_pdf_path IS NOT NULL AND signed_sha256 IS NOT NULL
    ORDER BY accepted_at DESC LIMIT 1")"
TOS_PATH="${TOS_ROW%%|*}"; TOS_SHA="${TOS_ROW##*|}"
[[ -n "${TOS_PATH}" && -n "${TOS_SHA}" && "${TOS_PATH}" != "${TOS_SHA}" ]] \
  || fail "no signed TOS row carrying a recorded sha256 in the restored DB — cannot verify the files lane"
restic_do dump "${FILES_SNAP}" "${TOS_PATH}" > "${WORKDIR}/tos.pdf" \
  || fail "restic dump of ${TOS_PATH} from the files lane failed"
[[ -s "${WORKDIR}/tos.pdf" ]] || fail "files lane returned 0 bytes for ${TOS_PATH} (wrong snapshot path?)"
FILE_SHA="$(sha256sum "${WORKDIR}/tos.pdf" | cut -d' ' -f1)"
[[ "${FILE_SHA}" == "${TOS_SHA}" ]] \
  || fail "files lane CORRUPT: ${TOS_PATH} restored sha256=${FILE_SHA:0:16} but the database recorded ${TOS_SHA:0:16}"
echo "[restore-drill] files lane verified: $(basename "${TOS_PATH}") matches db-recorded sha256 (${FILE_SHA:0:16}...)"

# --- 5. Success: stamp metric, quarterly OK note (cleanup trap drops the DB) --
emit_freshness_metric
notify default "[DroneOps Command] quarterly restore drill OK" \
  --tags "backup,white_check_mark" --dedup-key droneops-restore-drill-ok --cooldown 86400 \
  -- "Restored db snapshot ${SNAP_ID} (${AGE_H}h old) from the encrypted R2 repo into ${DRILL_DB}: flights=${DRILL_FLIGHTS}/${LIVE_FLIGHTS}, battery_logs=${DRILL_BATT}, tos_acceptances=${DRILL_TOS}. Config lane .env sha256 matches live. Files lane $(basename "${TOS_PATH}") matches db-recorded sha256. Scratch DB dropped."
echo "[restore-drill] done. snapshot=${SNAP_ID} flights=${DRILL_FLIGHTS}/${LIVE_FLIGHTS} config=verified files=verified"
