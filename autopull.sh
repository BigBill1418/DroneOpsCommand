#!/bin/bash
# ── DroneOpsCommand Auto-Deploy ──────────────────────────────────
#
# Polls the remote branch for new commits and auto-deploys.
# Designed to run unattended via systemd timer or cron.
#
# Config (env vars or defaults):
#   DOC_DIR      Install directory (default: script's own directory)
#   DOC_BRANCH   Git branch to track (default: claude/dev)
#
# Cron example (every minute):
#   * * * * * /home/user/droneops/autopull.sh
#
# Systemd timer: see droneops-autopull.timer
#
# Logs: $DOC_DIR/autopull.log (auto-rotated at 1MB)
# ──────────────────────────────────────────────────────────────────

set -euo pipefail

DIR="${DOC_DIR:-$(cd "$(dirname "$0")" && pwd)}"
BRANCH="${DOC_BRANCH:-claude/dev}"
LOGFILE="$DIR/autopull.log"
LOCKFILE="$DIR/.autopull.lock"
MAX_LOG_SIZE=1048576  # 1MB

# ── Logging ──────────────────────────────────────────────────────
log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOGFILE"; }

# Rotate log if it gets too large
if [ -f "$LOGFILE" ] && [ "$(stat -f%z "$LOGFILE" 2>/dev/null || stat -c%s "$LOGFILE" 2>/dev/null || echo 0)" -gt "$MAX_LOG_SIZE" ]; then
  mv "$LOGFILE" "${LOGFILE}.1"
fi

# ── Lock — prevent overlapping runs ─────────────────────────────
if [ -f "$LOCKFILE" ]; then
  # Stale lock? Remove if older than 10 minutes
  if find "$LOCKFILE" -mmin +10 2>/dev/null | grep -q .; then
    log "WARN: Removing stale lock (>10min old)"
    rm -f "$LOCKFILE"
  else
    exit 0
  fi
fi
trap 'rm -f "$LOCKFILE"' EXIT
touch "$LOCKFILE"

cd "$DIR"

# ── Determine deploy mode for update.sh ─────────────────────────
# update.sh expects "dev" or "prod" — map branch name to mode
case "$BRANCH" in
  main|master)    MODE="prod" ;;
  claude/dev|*)   MODE="dev" ;;
esac

# ── Fetch and compare ───────────────────────────────────────────
DOCKER="docker compose"
if [ "$(id -u)" -ne 0 ] && ! docker info >/dev/null 2>&1; then
  DOCKER="sudo docker compose"
fi

# Retry git fetch up to 3 times (network can be flaky)
fetched=false
for attempt in 1 2 3; do
  if git fetch origin "$BRANCH" --quiet 2>/dev/null; then
    fetched=true
    break
  fi
  log "WARN: git fetch failed (attempt $attempt/3)"
  sleep $((attempt * 2))
done

if [ "$fetched" = false ]; then
  log "ERROR: git fetch failed after 3 attempts — skipping this cycle"
  exit 1
fi

LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse "origin/$BRANCH")

if [ "$LOCAL" = "$REMOTE" ]; then
  exit 0  # Nothing new — silent exit
fi

# ── Deploy ───────────────────────────────────────────────────────
COMMIT_COUNT=$(git log --oneline "$LOCAL".."$REMOTE" | wc -l)
log "══════════════════════════════════════════════════"
log "New commits detected: $COMMIT_COUNT commit(s)"
log "$(echo "$LOCAL" | head -c 7) → $(echo "$REMOTE" | head -c 7)"
git log --oneline "$LOCAL".."$REMOTE" >> "$LOGFILE" 2>&1
log "Deploying via: update.sh $MODE"

if bash "$DIR/update.sh" "$MODE" >> "$LOGFILE" 2>&1; then
  log "Deploy complete — now at $(git rev-parse HEAD | head -c 7)"

  # Verify services are healthy after deploy
  sleep 10
  unhealthy=$($DOCKER ps --filter "health=unhealthy" --format "{{.Names}}" 2>/dev/null || true)
  if [ -n "$unhealthy" ]; then
    log "WARN: Unhealthy services after deploy: $unhealthy"
  else
    healthy_count=$($DOCKER ps --filter "health=healthy" --format "{{.Names}}" 2>/dev/null | wc -l || echo 0)
    log "All services healthy ($healthy_count containers)"
  fi
else
  EXIT_CODE=$?
  log "ERROR: Deploy failed (exit $EXIT_CODE)"
  log "Services status:"
  $DOCKER ps >> "$LOGFILE" 2>&1 || true
fi
log "══════════════════════════════════════════════════"
