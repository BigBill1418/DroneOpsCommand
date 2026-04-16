#!/usr/bin/env bash
# bootstrap.sh — refuses to start droneops-demo without required env config.
#
# Mitigation for 2026-04-16 incident: plain `docker compose up -d` silently
# fell back to default credentials (POSTGRES_USER=doc, POSTGRES_PASSWORD=
# changeme_in_production) because .env was missing and the `--env-file
# .env.demo` flag was not passed. Backend crash-looped on DB auth for
# 6h 26m before anyone noticed.
#
# This script fails loud instead of failing silent. Run it in place of a
# bare `docker compose up -d` on the demo host:
#
#   ./bootstrap.sh
#   ./bootstrap.sh --clean   # rebuild without docker cache
#

set -euo pipefail

cd "$(dirname "$0")"

if [[ ! -f .env.demo ]]; then
  echo "ERROR: .env.demo not found in $(pwd)" >&2
  echo "This file is required for demo deployment." >&2
  echo "It should be present on the demo host (CHAD-HQ:~/droneops-demo/.env.demo)." >&2
  exit 1
fi

if [[ ! -L .env && ! -f .env ]]; then
  echo "NOTICE: .env missing — linking to .env.demo so compose loads demo config even without --env-file flag"
  ln -s .env.demo .env
fi

REQUIRED_VARS=(
  POSTGRES_USER
  POSTGRES_PASSWORD
  POSTGRES_DB
  DATABASE_URL
  JWT_SECRET_KEY
  DEMO_ADMIN_USERNAME
  DEMO_ADMIN_PASSWORD
  CLOUDFLARE_TUNNEL_TOKEN
)

for var in "${REQUIRED_VARS[@]}"; do
  if ! grep -qE "^${var}=." .env.demo; then
    echo "ERROR: .env.demo missing required variable ${var} (empty or absent)" >&2
    echo "Verify the file is intact before redeploying." >&2
    exit 1
  fi
done

BUILD_FLAGS=()
if [[ "${1:-}" == "--clean" ]]; then
  BUILD_FLAGS+=(--build --no-cache)
  shift
fi

echo "Config verified. Starting droneops-demo stack..."
exec docker compose \
  -p droneops-demo \
  -f docker-compose.yml \
  -f docker-compose.demo.yml \
  --env-file .env.demo \
  up -d "${BUILD_FLAGS[@]}" "$@"
