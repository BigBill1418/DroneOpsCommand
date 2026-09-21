#!/usr/bin/env bash
# droneops-backup-cutover.sh — one-shot §5.7 cutover: retire the legacy backup
# lane once the ADR-0041 restic lane has its 3-green-day soak.
#
# SPENT — EXECUTED 2026-09-21 ~14:55 PDT. Kept for the record and because the
# gate rewrite below is the durable lesson. It is idempotent-ish but NOT a no-op:
# a re-run would re-attempt the deletes and its PROGRESS.md `sed` would silently
# match nothing (that heading has already been flipped). Do not re-run.
#
# Runs on droneops-server (HSH-HQ) — it needs ssh to BOS-HQ *and* push
# credentials for the repo, which is why it does not run on BOS itself.
# Scheduled via a **user-scope** unit,
# ~/.config/systemd/user/droneops-backup-cutover.timer, for 2026-08-20 04:12 UTC
# (after that morning's 03:23 run). Operator-approved 2026-08-18 ("sure handle
# it now"). **DISABLED 2026-09-21** (verified `systemctl --user is-enabled` →
# `disabled`, 0 timers listed). Cancel/inspect with:
#   systemctl --user disable --now droneops-backup-cutover.timer
# NOT `sudo systemctl ...` — this header said /etc/systemd/system until
# 2026-09-21 and it was never there; the sudo form returns "Unit not found",
# which reads as "already gone" when it means "you looked in the wrong scope."
#
# 2026-08-28: the timer fired and ABORTED on Gate 1 ("only 5/6 completed runs
# in last 3 days"). The lane was green twice daily throughout — the gate read
# journald, and journald on BOS-HQ retains under three days, so the oldest
# completion had rotated out. Gate 1 now counts the lane's own restic snapshots
# instead. Full record: ADR-0041 Amendment 2.
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
# 2026-09-21: the journal count was the wrong source of truth. The 2026-08-28 run
# aborted "5/6" while the lane was green twice a day — journald on BOS-HQ retains
# under three days, so the oldest completion had simply rotated out. Count the
# lane's own output instead: restic `db` snapshots in the repository whose
# timestamp is within the last 72 h. Retention (`forget --keep-daily`) collapses
# the two daily runs to one kept `db` snapshot per day, so three snapshots in
# 72 h IS the "three consecutive green days" criterion. The journal figure is
# kept for the log line only.
journal_done=$(ssh -o BatchMode=yes "${BOS}" \
  'journalctl -q -u droneops-backup.service --since "3 days ago" --no-pager | grep -c "done\."' || echo 0)
snap_json=$(ssh -o BatchMode=yes "${BOS}" 'set -a; . ~/.droneops-secrets/restic-droneops.env; set +a;
  docker run --rm --network host \
    -e RESTIC_REPOSITORY="s3:${R2_ENDPOINT}/${R2_BUCKET}/restic" \
    -e RESTIC_PASSWORD -e AWS_ACCESS_KEY_ID="${R2_ACCESS_KEY_ID}" \
    -e AWS_SECRET_ACCESS_KEY="${R2_SECRET_ACCESS_KEY}" \
    restic/restic:0.17.3 snapshots --tag db --json')
done_count=$(printf '%s' "${snap_json}" | python3 -c '
import json, sys, datetime as dt
snaps = json.load(sys.stdin)
cut = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=72)
def ts(s):
    t = s["time"]
    if "." in t:  # restic emits nanoseconds; Python parses at most microseconds
        head, tail = t.split(".", 1)
        frac = "".join(c for c in tail if c.isdigit())[:6]
        zone = tail[len("".join(c for c in tail if c.isdigit())):]
        t = f"{head}.{frac}{zone}"
    return dt.datetime.fromisoformat(t.replace("Z", "+00:00"))
print(sum(1 for s in snaps if ts(s) >= cut))')
[ "${done_count}" -ge 3 ] || fail "only ${done_count}/3 daily restic db snapshots in the last 72h (journal shows ${journal_done} completions)"

metric=$(ssh -o BatchMode=yes "${BOS}" \
  "awk '/^droneops_backup_last_success_timestamp_seconds/ {print \$2}' /var/lib/node_exporter/textfile_collector/droneops_backup.prom")
now=$(date +%s)
age_h=$(( (now - ${metric%.*}) / 3600 ))
[ "${age_h}" -lt 13 ] || fail "freshness metric is ${age_h}h old (>13h) — last run did not succeed"

svc_result=$(ssh -o BatchMode=yes "${BOS}" 'systemctl show droneops-backup.service -p Result --value')
[ "${svc_result}" = "success" ] || fail "droneops-backup.service Result=${svc_result}"

new_snap_count=$(printf '%s' "${snap_json}" | python3 -c 'import json,sys; print(len(json.load(sys.stdin)))')
[ "${new_snap_count}" -ge 4 ] || fail "restic repo has only ${new_snap_count} db snapshots"

echo "Gates passed: ${done_count} db snapshots in 72h (journal ${journal_done}) / metric ${age_h}h old / Result=success / ${new_snap_count} db snapshots"
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
  echo "All gates passed (≥3 daily db snapshots in 72h, metric fresh, Result=success, restic"
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
