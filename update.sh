#!/bin/bash
set -e

# ── Configuration ──────────────────────────────────────────────────
# Override these with environment variables or edit for your setup.
INSTALL_DIR="${DRONEOPS_DIR:-$(cd "$(dirname "$0")" && pwd)}"
BRANCH="${DRONEOPS_BRANCH:-claude/drone-report-generator-qk9UM}"
# ───────────────────────────────────────────────────────────────────

cd "$INSTALL_DIR"

# Use sudo for docker if not running as root and not in docker group
DOCKER="docker compose"
if [ "$(id -u)" -ne 0 ] && ! docker info >/dev/null 2>&1; then
  DOCKER="sudo docker compose"
fi

# Track last deployed commit AND branch so we catch branch switches
DEPLOY_MARKER=".last_deployed_commit"
BRANCH_MARKER=".last_deployed_branch"
PREV_COMMIT=""
PREV_BRANCH=""

if [ -f "$DEPLOY_MARKER" ]; then
  PREV_COMMIT=$(cat "$DEPLOY_MARKER")
fi
if [ -f "$BRANCH_MARKER" ]; then
  PREV_BRANCH=$(cat "$BRANCH_MARKER")
fi

echo "=== Fetching latest from $BRANCH ==="
if ! git fetch origin "$BRANCH"; then
  echo "ERROR: git fetch failed. Check your remote URL:"
  git remote -v
  echo ""
  echo "If the repo was renamed, fix it with:"
  echo "  git remote set-url origin https://github.com/BigBill1418/DroneOpsCommand.git"
  exit 1
fi

echo "=== Syncing to $BRANCH ==="
git checkout "$BRANCH" 2>/dev/null || git checkout -b "$BRANCH" "origin/$BRANCH"
git reset --hard "origin/$BRANCH"
CURRENT_COMMIT=$(git rev-parse HEAD)

# Detect branch switch — force full rebuild
if [ "$PREV_BRANCH" != "$BRANCH" ]; then
  echo "=== Branch changed from '${PREV_BRANCH:-none}' to '$BRANCH' — full rebuild ==="
  PREV_COMMIT=""
fi

# Figure out what changed since last deploy
if [ -n "$PREV_COMMIT" ] && git cat-file -t "$PREV_COMMIT" >/dev/null 2>&1; then
  CHANGED=$(git diff --name-only "$PREV_COMMIT" "$CURRENT_COMMIT")
  if [ -z "$CHANGED" ] && [ "$PREV_COMMIT" = "$CURRENT_COMMIT" ]; then
    echo "=== Already at latest commit ($(echo "$CURRENT_COMMIT" | head -c 7)) ==="
  fi
else
  # First run, marker invalid, or branch switch — rebuild everything
  CHANGED="all"
fi

REBUILD_FRONTEND=false
REBUILD_BACKEND=false
REBUILD_PARSER=false

if [ "$CHANGED" = "all" ]; then
  REBUILD_FRONTEND=true
  REBUILD_BACKEND=true
  REBUILD_PARSER=true
else
  echo "$CHANGED" | grep -q "^frontend/" && REBUILD_FRONTEND=true
  echo "$CHANGED" | grep -q "^backend/" && REBUILD_BACKEND=true
  echo "$CHANGED" | grep -q "^flight-parser/" && REBUILD_PARSER=true
fi

# --clean flag forces full rebuild (no cache)
CACHE_FLAG=""
if [ "$1" = "--clean" ]; then
  CACHE_FLAG="--no-cache"
  REBUILD_FRONTEND=true
  REBUILD_BACKEND=true
  REBUILD_PARSER=true
fi

# --all flag rebuilds everything
if [ "$1" = "--all" ]; then
  REBUILD_FRONTEND=true
  REBUILD_BACKEND=true
  REBUILD_PARSER=true
fi

if $REBUILD_FRONTEND; then
  echo "=== Rebuilding frontend ==="
  $DOCKER build $CACHE_FLAG frontend
fi

if $REBUILD_BACKEND; then
  echo "=== Rebuilding backend + worker ==="
  $DOCKER build $CACHE_FLAG backend worker
fi

if $REBUILD_PARSER; then
  echo "=== Rebuilding flight-parser ==="
  $DOCKER build $CACHE_FLAG flight-parser
fi

if $REBUILD_FRONTEND || $REBUILD_BACKEND || $REBUILD_PARSER; then
  echo "=== Restarting changed services ==="
  $DOCKER up -d
else
  echo "=== No app changes detected, nothing to rebuild ==="
fi

# Save current commit and branch as deployed
echo "$CURRENT_COMMIT" > "$DEPLOY_MARKER"
echo "$BRANCH" > "$BRANCH_MARKER"

echo "=== Done ($(echo "$CURRENT_COMMIT" | head -c 7)) ==="
$DOCKER ps
