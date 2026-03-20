#!/bin/bash
set -e

# ── DroneOpsCommand Update Script ─────────────────────────────────
#
# Usage:
#   ./update.sh dev          Pull claude/dev branch, rebuild & run (testing)
#   ./update.sh prod         Pull main branch, rebuild & run (production)
#   ./update.sh dev --clean  Full rebuild, no Docker cache
#   ./update.sh dev --all    Rebuild all services even if unchanged
#   ./update.sh              Defaults to "dev"
#
# ──────────────────────────────────────────────────────────────────

# ── Parse arguments ──────────────────────────────────────────────
MODE="${1:-dev}"
FLAG="${2:-}"

# Allow flag as first arg when no mode specified (backwards compat)
if [ "$MODE" = "--clean" ] || [ "$MODE" = "--all" ]; then
  FLAG="$MODE"
  MODE="dev"
fi

case "$MODE" in
  dev)   BRANCH="claude/dev" ;;
  prod)  BRANCH="main" ;;
  *)
    echo "Usage: $0 [dev|prod] [--clean|--all]"
    echo ""
    echo "  dev   — Pull from claude/dev (testing/development)"
    echo "  prod  — Pull from main (production)"
    echo "  --clean  Force full rebuild (no Docker cache)"
    echo "  --all    Rebuild all services even if unchanged"
    exit 1
    ;;
esac

# ── Setup ────────────────────────────────────────────────────────
INSTALL_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$INSTALL_DIR"

DOCKER="docker compose"
if [ "$(id -u)" -ne 0 ] && ! docker info >/dev/null 2>&1; then
  DOCKER="sudo docker compose"
fi

DEPLOY_MARKER=".last_deployed_commit"

echo ""
echo "╔══════════════════════════════════════════════════╗"
echo "║  DroneOpsCommand Update — $MODE                      ║"
echo "║  Branch: $BRANCH"
echo "╚══════════════════════════════════════════════════╝"
echo ""

# ── Fetch & sync ─────────────────────────────────────────────────
echo "=== Fetching $BRANCH ==="
if ! git fetch origin "$BRANCH"; then
  echo "ERROR: Failed to fetch '$BRANCH'. Check remote:"
  git remote -v
  echo ""
  echo "Fix with: git remote set-url origin https://github.com/BigBill1418/DroneOpsCommand.git"
  exit 1
fi

git checkout "$BRANCH" 2>/dev/null || git checkout -b "$BRANCH" "origin/$BRANCH"

PREV_COMMIT=""
if [ -f "$DEPLOY_MARKER" ]; then
  PREV_COMMIT=$(cat "$DEPLOY_MARKER")
fi

git reset --hard "origin/$BRANCH"
CURRENT_COMMIT=$(git rev-parse HEAD)

# ── Detect changes ───────────────────────────────────────────────
REBUILD_FRONTEND=false
REBUILD_BACKEND=false
REBUILD_PARSER=false

if [ -n "$PREV_COMMIT" ] && git cat-file -t "$PREV_COMMIT" >/dev/null 2>&1; then
  CHANGED=$(git diff --name-only "$PREV_COMMIT" "$CURRENT_COMMIT")
else
  CHANGED="all"
fi

if [ "$CHANGED" = "all" ]; then
  echo "=== First deploy or branch switch — rebuilding all ==="
  REBUILD_FRONTEND=true
  REBUILD_BACKEND=true
  REBUILD_PARSER=true
elif [ -z "$CHANGED" ]; then
  echo "=== Already at latest ($(echo "$CURRENT_COMMIT" | head -c 7)), nothing changed ==="
else
  echo "=== Changes: $(echo "${PREV_COMMIT:-none}" | head -c 7) → $(echo "$CURRENT_COMMIT" | head -c 7) ==="
  echo "$CHANGED" | grep -q "^frontend/" && REBUILD_FRONTEND=true
  echo "$CHANGED" | grep -q "^backend/" && REBUILD_BACKEND=true
  echo "$CHANGED" | grep -q "^flight-parser/" && REBUILD_PARSER=true

  $REBUILD_FRONTEND && echo "  → frontend"
  $REBUILD_BACKEND  && echo "  → backend"
  $REBUILD_PARSER   && echo "  → flight-parser"

  if ! $REBUILD_FRONTEND && ! $REBUILD_BACKEND && ! $REBUILD_PARSER; then
    echo "  → non-service files only (no rebuild needed)"
  fi
fi

# ── Handle flags ─────────────────────────────────────────────────
CACHE_FLAG=""
if [ "$FLAG" = "--clean" ]; then
  echo "=== Clean build requested (no Docker cache) ==="
  CACHE_FLAG="--no-cache"
  REBUILD_FRONTEND=true
  REBUILD_BACKEND=true
  REBUILD_PARSER=true
fi

if [ "$FLAG" = "--all" ]; then
  echo "=== Rebuilding all services ==="
  REBUILD_FRONTEND=true
  REBUILD_BACKEND=true
  REBUILD_PARSER=true
fi

# ── Build & deploy ───────────────────────────────────────────────
$REBUILD_FRONTEND && echo "=== Building frontend ===" && $DOCKER build $CACHE_FLAG frontend
$REBUILD_BACKEND  && echo "=== Building backend + worker ===" && $DOCKER build $CACHE_FLAG backend worker
$REBUILD_PARSER   && echo "=== Building flight-parser ===" && $DOCKER build $CACHE_FLAG flight-parser

if $REBUILD_FRONTEND || $REBUILD_BACKEND || $REBUILD_PARSER; then
  echo "=== Restarting services ==="
  $DOCKER up -d
else
  echo "=== No services to rebuild ==="
fi

# ── Save state ───────────────────────────────────────────────────
echo "$CURRENT_COMMIT" > "$DEPLOY_MARKER"

echo ""
echo "=== Done [$MODE] — $(echo "$CURRENT_COMMIT" | head -c 7) ==="
$DOCKER ps
