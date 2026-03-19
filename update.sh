#!/bin/bash
set -e

# ── Configuration ──────────────────────────────────────────────────
# Override these with environment variables or edit for your setup.
INSTALL_DIR="${DRONEOPS_DIR:-$(cd "$(dirname "$0")" && pwd)}"
BRANCH="${DRONEOPS_BRANCH:-main}"
# ───────────────────────────────────────────────────────────────────

cd "$INSTALL_DIR"

# Use sudo for docker if not running as root and not in docker group
DOCKER="docker compose"
if [ "$(id -u)" -ne 0 ] && ! docker info >/dev/null 2>&1; then
  DOCKER="sudo docker compose"
fi

# Track last deployed commit and branch
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
  echo "  git remote set-url origin https://github.com/BigBill1418/DroneOpsReport.git"
  exit 1
fi

echo "=== Syncing to $BRANCH ==="
git checkout "$BRANCH" 2>/dev/null || git checkout -b "$BRANCH" "origin/$BRANCH"

# Capture state before reset
BEFORE_COMMIT=$(git rev-parse HEAD)
git reset --hard "origin/$BRANCH"
CURRENT_COMMIT=$(git rev-parse HEAD)

# Determine what changed
REBUILD_FRONTEND=false
REBUILD_BACKEND=false
REBUILD_PARSER=false

if [ "$PREV_BRANCH" != "$BRANCH" ] && [ -n "$PREV_BRANCH" ]; then
  # Branch switch — compare current tree against what docker is running
  # Use the PREV_COMMIT as the base if it exists in history
  echo "=== Branch changed: $PREV_BRANCH -> $BRANCH ==="
  if [ -n "$PREV_COMMIT" ] && git cat-file -t "$PREV_COMMIT" >/dev/null 2>&1; then
    CHANGED=$(git diff --name-only "$PREV_COMMIT" "$CURRENT_COMMIT")
  else
    # Can't find old commit — check what services have code changes vs running images
    CHANGED="all"
  fi
elif [ -n "$PREV_COMMIT" ] && git cat-file -t "$PREV_COMMIT" >/dev/null 2>&1; then
  CHANGED=$(git diff --name-only "$PREV_COMMIT" "$CURRENT_COMMIT")
else
  # First run or marker invalid — rebuild everything
  CHANGED="all"
fi

if [ "$CHANGED" = "all" ]; then
  echo "=== First deploy or marker invalid — rebuilding all services ==="
  REBUILD_FRONTEND=true
  REBUILD_BACKEND=true
  REBUILD_PARSER=true
elif [ -z "$CHANGED" ]; then
  echo "=== Already at latest commit ($(echo "$CURRENT_COMMIT" | head -c 7)), no file changes ==="
else
  echo "=== Changes detected between $(echo "${PREV_COMMIT:-none}" | head -c 7) and $(echo "$CURRENT_COMMIT" | head -c 7) ==="
  echo "$CHANGED" | grep -q "^frontend/" && REBUILD_FRONTEND=true
  echo "$CHANGED" | grep -q "^backend/" && REBUILD_BACKEND=true
  echo "$CHANGED" | grep -q "^flight-parser/" && REBUILD_PARSER=true

  # Show what's being rebuilt
  $REBUILD_FRONTEND && echo "  -> frontend changed"
  $REBUILD_BACKEND && echo "  -> backend changed"
  $REBUILD_PARSER && echo "  -> flight-parser changed"

  # If only non-service files changed (README, docs, etc.), nothing to rebuild
  if ! $REBUILD_FRONTEND && ! $REBUILD_BACKEND && ! $REBUILD_PARSER; then
    echo "  -> changes are in non-service files only (no rebuild needed)"
  fi
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
  echo "=== No service changes detected, nothing to rebuild ==="
fi

# Save current commit and branch as deployed
echo "$CURRENT_COMMIT" > "$DEPLOY_MARKER"
echo "$BRANCH" > "$BRANCH_MARKER"

echo "=== Done ($(echo "$CURRENT_COMMIT" | head -c 7)) ==="
$DOCKER ps
