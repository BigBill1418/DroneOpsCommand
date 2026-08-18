#!/usr/bin/env bash
# droneops-backup-cutover.sh — one-shot §5.7 cutover: retire the legacy backup
# lane once the ADR-0041 restic lane has its 3-green-day soak.
#
# Runs on droneops-server (HSH-HQ) — it needs ssh to BOS-HQ *and* push
# credentials for the repo, which is why it does not run on BOS itself.
# Scheduled via /etc/systemd/system/droneops-backup-cutover.timer for
# 2026-08-20 04:12 UTC (after that morning's 03:23 run). Operator-approved
# 2026-08-18 ("sure handle it now"). Cancel with:
#   sudo systemctl disable --now droneops-backup-cutover.timer
#
# Every failure path notifies ntfy at high and exits non-zero having changed
# as little as possible. Verification gates come BEFORE any mutation.
#
# Usage: droneops-backup-cutover.sh [--dry-run]   (--dry-run = gates only)

set -euo pipefail

BOS="10.99.0.4"
REPO="/home/bbarnard065/droneops"
NTFY="/home/bbarnard065/.local/bin/ntfy-publish.sh"
CLICK="https://noc-mastercontrol.barnardhq.com/status/droneops"
DRY_RUN=0
[ "${1:-}" = "--dry-run" ] && DRY_RUN=1

notify() { # <priority> <msg>
  [ "${DRY_RUN}" = 1 ] && { echo "(dry-run: suppressed ntfy '$2')"; return 0; }
  "${NTFY}" --topic infrawatch-alerts --priority "$1" \
    --title "[DroneOps Command] backup cutover: $2" \
    --tags "backup,deploy" --click "${CLICK}" \
    --dedup-key droneops-backup-cutover --cooldown 21600 -- "$3" || true
}

fail() {
  echo "CUTOVER ABORTED: $*" >&2
  notify high "ABORTED" "$* — legacy lane left untouched; run by hand per PROGRESS.md §5.7"
  exit 1
}

# ---------- Gate 1: soak criteria (PROGRESS.md 'Cutover criteria') ----------
done_count=$(ssh -o BatchMode=yes "${BOS}" \
  'journalctl -q -u droneops-backup.service --since "3 days ago" --no-pager | grep -c "done\."' || echo 0)
[ "${done_count}" -ge 6 ] || fail "only ${done_count}/6 completed runs in last 3 days"

metric=$(ssh -o BatchMode=yes "${BOS}" \
  "awk '/^droneops_backup_last_success_timestamp_seconds/ {print \$2}' /var/lib/node_exporter/textfile_collector/droneops_backup.prom")
now=$(date +%s)
age_h=$(( (now - ${metric%.*}) / 3600 ))
[ "${age_h}" -lt 13 ] || fail "freshness metric is ${age_h}h old (>13h) — last run did not succeed"

svc_result=$(ssh -o BatchMode=yes "${BOS}" 'systemctl show droneops-backup.service -p Result --value')
[ "${svc_result}" = "success" ] || fail "droneops-backup.service Result=${svc_result}"

new_snap_count=$(ssh -o BatchMode=yes "${BOS}" 'set -a; . ~/.droneops-secrets/restic-droneops.env; set +a;
  docker run --rm --network host \
    -e RESTIC_REPOSITORY="s3:${R2_ENDPOINT}/${R2_BUCKET}/restic" \
    -e RESTIC_PASSWORD -e AWS_ACCESS_KEY_ID="${R2_ACCESS_KEY_ID}" \
    -e AWS_SECRET_ACCESS_KEY="${R2_SECRET_ACCESS_KEY}" \
    restic/restic:0.17.3 snapshots --tag db --json' | python3 -c 'import json,sys; print(len(json.load(sys.stdin)))')
[ "${new_snap_count}" -ge 4 ] || fail "restic repo has only ${new_snap_count} db snapshots"

echo "Gates passed: ${done_count} completions / metric ${age_h}h old / Result=success / ${new_snap_count} db snapshots"
if [ "${DRY_RUN}" = 1 ]; then echo "DRY RUN — stopping before any mutation."; exit 0; fi

# ---------- Step 2: retire the legacy cron (leaves CallSign's line intact) ----------
ssh -o BatchMode=yes "${BOS}" \
  "crontab -l | grep -v 'droneops/scripts/snapshot.sh' | crontab -"
left=$(ssh -o BatchMode=yes "${BOS}" "crontab -l | grep -c 'droneops/scripts/snapshot.sh'" || true)
[ "${left}" = "0" ] || fail "cron line still present after removal"

# ---------- Step 3: delete the old PLAINTEXT R2 prefix ----------
ssh -o BatchMode=yes "${BOS}" 'set -a; . /opt/observability/.env; set +a;
  docker run --rm --network host \
    -e AWS_ACCESS_KEY_ID="${OBS_GLITCHTIP_BACKUPS_R2_ACCESS_KEY_ID}" \
    -e AWS_SECRET_ACCESS_KEY="${OBS_GLITCHTIP_BACKUPS_R2_SECRET_ACCESS_KEY}" \
    amazon/aws-cli --endpoint-url "${OBS_GLITCHTIP_BACKUPS_R2_ENDPOINT}" \
    s3 rm --recursive --quiet "s3://${OBS_GLITCHTIP_BACKUPS_R2_BUCKET}/droneops/"' \
  || fail "old R2 prefix deletion failed"
remaining=$(ssh -o BatchMode=yes "${BOS}" 'set -a; . /opt/observability/.env; set +a;
  docker run --rm --network host \
    -e AWS_ACCESS_KEY_ID="${OBS_GLITCHTIP_BACKUPS_R2_ACCESS_KEY_ID}" \
    -e AWS_SECRET_ACCESS_KEY="${OBS_GLITCHTIP_BACKUPS_R2_SECRET_ACCESS_KEY}" \
    amazon/aws-cli --endpoint-url "${OBS_GLITCHTIP_BACKUPS_R2_ENDPOINT}" \
    s3 ls "s3://${OBS_GLITCHTIP_BACKUPS_R2_BUCKET}/droneops/" 2>/dev/null | wc -l')
[ "${remaining}" = "0" ] || fail "old R2 prefix not empty after delete (${remaining} entries)"

# ---------- Step 4: repo — remove snapshot.sh, flip docs, push, sync BOS ----------
cd "${REPO}"
git pull -q origin main
git rm -q scripts/snapshot.sh
TODAY=$(date -u +%F)
sed -i 's/— LIVE, IN PARALLEL RUN — cutover pending/— LIVE — cutover executed '"${TODAY}"'/' PROGRESS.md
{
  echo ""
  echo "### Cutover executed ${TODAY} (automated)"
  echo ""
  echo "All gates passed (≥6 completions, metric fresh, Result=success, restic"
  echo "db snapshots present). Legacy cron removed (CallSign line untouched),"
  echo "plaintext \`s3://obs-glitchtip-backups/droneops/\` prefix deleted,"
  echo "\`scripts/snapshot.sh\` removed from the repo (history preserves it)."
  echo "Executed by \`scripts/droneops-backup-cutover.sh\` via systemd timer on"
  echo "droneops-server; this entry written by the same script."
} >> PROGRESS.md
git add PROGRESS.md
git commit -q -m "ops(backups): execute §5.7 cutover — legacy lane retired (ADR-0041) [skip-deploy]

Automated one-shot: gates verified, legacy cron removed, plaintext R2 prefix
deleted, snapshot.sh retired. See PROGRESS.md.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
git push -q origin main || fail "system cutover DONE but git push failed — commit locally at $(git rev-parse --short HEAD); push + BOS pull by hand"
ssh -o BatchMode=yes "${BOS}" "cd ~/droneops && git pull -q && test ! -f scripts/snapshot.sh" \
  || fail "system cutover DONE but BOS pull/verify failed — sync ~/droneops on BOS by hand"

notify default "complete" "Legacy backup lane retired: cron removed, plaintext R2 prefix deleted, snapshot.sh removed (commit $(git rev-parse --short HEAD)). Encrypted restic lane is now the sole backup."
echo "CUTOVER COMPLETE"
