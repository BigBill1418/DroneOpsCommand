#!/bin/bash
set -e
cd /volume1/docker/droneops

# Use sudo for docker if not running as root and not in docker group
DOCKER="docker compose"
if [ "$(id -u)" -ne 0 ] && ! docker info >/dev/null 2>&1; then
  DOCKER="sudo docker compose"
fi

echo "=== Pulling latest changes ==="
git pull origin claude/drone-report-generator-qk9UM

# Figure out what changed
CHANGED=$(git diff --name-only HEAD@{1} HEAD 2>/dev/null || echo "all")

REBUILD_FRONTEND=false
REBUILD_BACKEND=false

if echo "$CHANGED" | grep -q "^frontend/"; then
  REBUILD_FRONTEND=true
fi
if echo "$CHANGED" | grep -q "^backend/"; then
  REBUILD_BACKEND=true
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

echo "=== Done ==="
$DOCKER ps
