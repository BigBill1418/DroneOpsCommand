#!/bin/bash
# Polls the remote branch for new commits and auto-deploys.
# Run via cron: */5 * * * * /volume1/docker/droneops/autopull.sh
#
# Logs to /volume1/docker/droneops/autopull.log

set -e
DIR="/volume1/docker/droneops"
BRANCH="claude/drone-report-generator-qk9UM"
LOGFILE="$DIR/autopull.log"
LOCKFILE="$DIR/.autopull.lock"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOGFILE"; }

# Prevent overlapping runs
if [ -f "$LOCKFILE" ]; then
  # Stale lock? Remove if older than 10 minutes
  if [ "$(find "$LOCKFILE" -mmin +10 2>/dev/null)" ]; then
    rm -f "$LOCKFILE"
  else
    exit 0
  fi
fi
trap 'rm -f "$LOCKFILE"' EXIT
touch "$LOCKFILE"

cd "$DIR"

# Fetch latest from remote (quiet)
git fetch origin "$BRANCH" --quiet 2>/dev/null || { log "WARN: git fetch failed"; exit 1; }

LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse "origin/$BRANCH")

if [ "$LOCAL" = "$REMOTE" ]; then
  exit 0  # Nothing new — silent exit
fi

log "New commits detected: $(git log --oneline "$LOCAL".."$REMOTE" | wc -l) commit(s)"
log "Deploying $REMOTE..."

# Run the update script
if bash "$DIR/update.sh" >> "$LOGFILE" 2>&1; then
  log "Deploy complete ✓"
else
  log "ERROR: Deploy failed (exit $?)"
fi
