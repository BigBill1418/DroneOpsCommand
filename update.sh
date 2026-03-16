#!/bin/bash
set -e
cd /volume1/docker/droneops

# Use sudo for docker if not running as root and not in docker group
DOCKER="docker compose"
if [ "$(id -u)" -ne 0 ] && ! docker info >/dev/null 2>&1; then
  DOCKER="sudo docker compose"
fi

# Track last deployed commit so we catch ALL changes, not just the latest pull
DEPLOY_MARKER=".last_deployed_commit"
PREV_COMMIT=""
if [ -f "$DEPLOY_MARKER" ]; then
  PREV_COMMIT=$(cat "$DEPLOY_MARKER")
fi

BRANCH="claude/drone-report-generator-qk9UM"

echo "=== Fetching latest from $BRANCH ==="
git fetch origin "$BRANCH"

echo "=== Syncing main to $BRANCH ==="
git checkout main 2>/dev/null || git checkout -b main origin/main
# Feature branch is source of truth — force main to match it
git reset --hard "origin/$BRANCH"
git push origin main --force 2>/dev/null || true
CURRENT_COMMIT=$(git rev-parse HEAD)

# Figure out what changed since last deploy
if [ -n "$PREV_COMMIT" ] && git cat-file -t "$PREV_COMMIT" >/dev/null 2>&1; then
  CHANGED=$(git diff --name-only "$PREV_COMMIT" "$CURRENT_COMMIT")
else
  # First run or marker invalid — rebuild everything
  CHANGED="all"
fi

REBUILD_FRONTEND=false
REBUILD_BACKEND=false

if [ "$CHANGED" = "all" ]; then
  REBUILD_FRONTEND=true
  REBUILD_BACKEND=true
else
  echo "$CHANGED" | grep -q "^frontend/" && REBUILD_FRONTEND=true
  echo "$CHANGED" | grep -q "^backend/" && REBUILD_BACKEND=true
fi

# --clean flag forces full rebuild (no cache)
CACHE_FLAG=""
if [ "$1" = "--clean" ]; then
  CACHE_FLAG="--no-cache"
  REBUILD_FRONTEND=true
  REBUILD_BACKEND=true
fi

# --all flag rebuilds everything
if [ "$1" = "--all" ]; then
  REBUILD_FRONTEND=true
  REBUILD_BACKEND=true
fi

if $REBUILD_FRONTEND; then
  echo "=== Rebuilding frontend ==="
  $DOCKER build $CACHE_FLAG frontend
fi

if $REBUILD_BACKEND; then
  echo "=== Rebuilding backend + worker ==="
  $DOCKER build $CACHE_FLAG backend worker
fi

if $REBUILD_FRONTEND || $REBUILD_BACKEND; then
  echo "=== Restarting changed services ==="
  $DOCKER up -d
else
  echo "=== No app changes detected, nothing to rebuild ==="
fi

# Save current commit as deployed
echo "$CURRENT_COMMIT" > "$DEPLOY_MARKER"

echo "=== Done ($(echo "$CURRENT_COMMIT" | head -c 7)) ==="
$DOCKER ps
