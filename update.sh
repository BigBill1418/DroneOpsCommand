#!/bin/bash
set -e

# ── DroneOps Updater ──────────────────────────────────────────────
REPO="https://github.com/BigBill1418/DroneOpsCommand.git"
BRANCH="dev"
INSTALL_DIR="$(cd "$(dirname "$0")" && pwd)"
DEPLOY_MARKER="$INSTALL_DIR/.last_deployed_commit"
# ──────────────────────────────────────────────────────────────────

cd "$INSTALL_DIR"

# Docker compose — use sudo if needed
DOCKER="docker compose"
if [ "$(id -u)" -ne 0 ] && ! docker info >/dev/null 2>&1; then
  DOCKER="sudo docker compose"
fi

# ── Self-update: refresh this script from remote before doing anything ──
echo ""
echo "  Checking for script updates..."
REMOTE_SCRIPT=$(curl -fsSL "https://raw.githubusercontent.com/BigBill1418/DroneOpsCommand/$BRANCH/update.sh" 2>/dev/null || true)
if [ -n "$REMOTE_SCRIPT" ]; then
  LOCAL_HASH=$(md5sum "$0" | cut -d' ' -f1)
  REMOTE_HASH=$(echo "$REMOTE_SCRIPT" | md5sum | cut -d' ' -f1)
  if [ "$LOCAL_HASH" != "$REMOTE_HASH" ]; then
    echo "  update.sh has changed — refreshing..."
    echo "$REMOTE_SCRIPT" > "$0"
    chmod +x "$0"
    echo "  Restarting with updated script..."
    echo ""
    exec "$0" "$@"
  fi
fi

# ── Fix remote URL if repo was renamed ──
CURRENT_URL=$(git remote get-url origin 2>/dev/null || true)
if [ -n "$CURRENT_URL" ] && echo "$CURRENT_URL" | grep -qi "DroneOpsReport"; then
  echo "  Fixing remote URL (repo was renamed)..."
  git remote set-url origin "$REPO"
fi

# ── Fetch & sync ──
echo ""
echo "  Pulling latest from $BRANCH..."
if ! git fetch origin "$BRANCH" 2>/dev/null; then
  echo ""
  echo "  ERROR: Could not reach the repo. Check your internet connection."
  echo "  Remote: $(git remote get-url origin)"
  exit 1
fi

git checkout "$BRANCH" 2>/dev/null || git checkout -b "$BRANCH" "origin/$BRANCH"

PREV_COMMIT=""
[ -f "$DEPLOY_MARKER" ] && PREV_COMMIT=$(cat "$DEPLOY_MARKER")

git reset --hard "origin/$BRANCH" >/dev/null
CURRENT_COMMIT=$(git rev-parse HEAD)
SHORT_COMMIT=$(echo "$CURRENT_COMMIT" | head -c 7)

# ── Detect what changed ──
REBUILD_FRONTEND=false
REBUILD_BACKEND=false
REBUILD_PARSER=false

if [ -n "$PREV_COMMIT" ] && git cat-file -t "$PREV_COMMIT" >/dev/null 2>&1; then
  CHANGED=$(git diff --name-only "$PREV_COMMIT" "$CURRENT_COMMIT")
  if [ -z "$CHANGED" ]; then
    echo "  Already up to date ($SHORT_COMMIT). Nothing to do."
    echo ""
    $DOCKER ps --format "table {{.Names}}\t{{.Status}}" 2>/dev/null || true
    exit 0
  fi
  echo "$CHANGED" | grep -q "^frontend/" && REBUILD_FRONTEND=true
  echo "$CHANGED" | grep -q "^backend/" && REBUILD_BACKEND=true
  echo "$CHANGED" | grep -q "^flight-parser/" && REBUILD_PARSER=true
else
  # First run — rebuild everything
  REBUILD_FRONTEND=true
  REBUILD_BACKEND=true
  REBUILD_PARSER=true
fi

# ── Flags ──
if [ "$1" = "--clean" ] || [ "$1" = "--all" ]; then
  REBUILD_FRONTEND=true
  REBUILD_BACKEND=true
  REBUILD_PARSER=true
fi
CACHE_FLAG=""
[ "$1" = "--clean" ] && CACHE_FLAG="--no-cache"

# ── Build only what changed ──
SERVICES=""
$REBUILD_FRONTEND && SERVICES="$SERVICES frontend" && echo "  Rebuilding frontend..."
$REBUILD_BACKEND && SERVICES="$SERVICES backend worker" && echo "  Rebuilding backend..."
$REBUILD_PARSER && SERVICES="$SERVICES flight-parser" && echo "  Rebuilding flight-parser..."

if [ -z "$SERVICES" ]; then
  echo "  Only docs/config changed — no rebuild needed."
else
  echo ""
  $DOCKER build $CACHE_FLAG $SERVICES
  $DOCKER up -d
fi

# ── Save deploy marker ──
echo "$CURRENT_COMMIT" > "$DEPLOY_MARKER"

echo ""
echo "  Updated to $SHORT_COMMIT"
echo ""
$DOCKER ps --format "table {{.Names}}\t{{.Status}}" 2>/dev/null || true
echo ""
