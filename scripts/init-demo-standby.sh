#!/bin/bash
# init-demo-standby.sh — Initializes the DroneOps Demo PostgreSQL standby on HSH-HQ.
# Performs pg_basebackup from the CHAD-HQ primary and creates the
# standby.signal file required for streaming replication.
#
# Run this ONCE before starting the standby container.
# After this, docker-compose.demo-standby.yml handles ongoing replication.
#
# Usage: ./scripts/init-demo-standby.sh

set -euo pipefail

PRIMARY_HOST="10.99.0.2"
PRIMARY_PORT="5435"
REPL_USER="replicator"
REPL_PASSWORD="SecureDemoRepl2026"
VOLUME_NAME="droneops_demo_standby_pgdata"
COMPOSE_FILE="docker-compose.demo-standby.yml"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "=== DroneOps Demo Standby Initialization ==="
echo "Primary: ${PRIMARY_HOST}:${PRIMARY_PORT}"
echo ""

# Ensure the standby container is stopped
echo "[1/5] Stopping any existing standby container..."
cd "$PROJECT_DIR"
docker compose -f "$COMPOSE_FILE" down 2>/dev/null || true

# Remove old standby volume if it exists (fresh base backup)
if docker volume inspect "$VOLUME_NAME" >/dev/null 2>&1; then
    echo "[!] Existing standby volume found. Removing for fresh base backup..."
    docker volume rm "$VOLUME_NAME"
fi

# Create the volume
echo "[2/5] Creating standby volume..."
docker volume create "$VOLUME_NAME"

# Run pg_basebackup into the volume
echo "[3/5] Running pg_basebackup from primary..."
docker run --rm \
    -v "${VOLUME_NAME}:/var/lib/postgresql/data" \
    -e PGPASSWORD="$REPL_PASSWORD" \
    postgres:16-alpine \
    pg_basebackup \
        -h "$PRIMARY_HOST" \
        -p "$PRIMARY_PORT" \
        -U "$REPL_USER" \
        -D /var/lib/postgresql/data \
        -Fp -Xs -P -R \
        -S hsh_hq_demo_standby

echo "[4/5] Fixing data directory ownership (postgres uid=70 in alpine)..."
docker run --rm \
    -v "${VOLUME_NAME}:/var/lib/postgresql/data" \
    alpine \
    chown -R 70:70 /var/lib/postgresql/data

echo "[5/5] Verifying standby.signal exists..."
docker run --rm \
    -v "${VOLUME_NAME}:/var/lib/postgresql/data" \
    alpine \
    ls -la /var/lib/postgresql/data/standby.signal

echo ""
echo "=== Demo standby initialization complete ==="
echo "Start the standby with:"
echo "  cd $PROJECT_DIR && docker compose -f $COMPOSE_FILE up -d"
