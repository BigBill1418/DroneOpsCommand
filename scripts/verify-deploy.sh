#!/usr/bin/env bash
# verify-deploy.sh — Post-deploy health verification for DroneOps
# Run after `docker compose up -d` to confirm all services are healthy.
# Exits 0 if all healthy, 1 if any service is stuck/unhealthy after timeout.

set -euo pipefail

TIMEOUT="${1:-120}"
INTERVAL=5
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml}"
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

cd "$PROJECT_DIR"

# Services that must be healthy (excludes one-shot containers like ollama-setup)
REQUIRED_SERVICES=$(docker compose -f "$COMPOSE_FILE" config --services 2>/dev/null | grep -v -E '^(ollama-setup|cloudflared)$')

echo "=== DroneOps Deploy Verification ==="
echo "Timeout: ${TIMEOUT}s | Checking every ${INTERVAL}s"
echo "Required services: $(echo $REQUIRED_SERVICES | tr '\n' ' ')"
echo ""

elapsed=0
while [ "$elapsed" -lt "$TIMEOUT" ]; do
    all_healthy=true
    status_lines=""

    for svc in $REQUIRED_SERVICES; do
        # Get container health status
        health=$(docker compose -f "$COMPOSE_FILE" ps --format json "$svc" 2>/dev/null | \
            python3 -c "import sys,json; data=json.load(sys.stdin); print(data.get('Health','unknown'))" 2>/dev/null || echo "missing")

        state=$(docker compose -f "$COMPOSE_FILE" ps --format json "$svc" 2>/dev/null | \
            python3 -c "import sys,json; data=json.load(sys.stdin); print(data.get('State','unknown'))" 2>/dev/null || echo "missing")

        if [ "$health" = "healthy" ]; then
            marker="OK"
        elif [ "$state" = "running" ] && [ "$health" = "" ]; then
            # Running but no healthcheck defined — treat as OK
            marker="OK (no healthcheck)"
        else
            marker="WAITING ($state/$health)"
            all_healthy=false
        fi

        status_lines="${status_lines}  ${svc}: ${marker}\n"
    done

    if [ "$all_healthy" = true ]; then
        echo "All services healthy after ${elapsed}s:"
        echo ""
        printf "$status_lines"
        echo ""
        echo "=== Deploy verified ==="
        exit 0
    fi

    # Show progress every 15 seconds
    if [ $((elapsed % 15)) -eq 0 ] && [ "$elapsed" -gt 0 ]; then
        echo "[${elapsed}s] Still waiting..."
        printf "$status_lines"
        echo ""
    fi

    sleep "$INTERVAL"
    elapsed=$((elapsed + INTERVAL))
done

# Timeout — report final state
echo "=== TIMEOUT after ${TIMEOUT}s ==="
echo ""
echo "Final service status:"
for svc in $REQUIRED_SERVICES; do
    health=$(docker compose -f "$COMPOSE_FILE" ps --format json "$svc" 2>/dev/null | \
        python3 -c "import sys,json; data=json.load(sys.stdin); print(data.get('Health','unknown'))" 2>/dev/null || echo "missing")

    state=$(docker compose -f "$COMPOSE_FILE" ps --format json "$svc" 2>/dev/null | \
        python3 -c "import sys,json; data=json.load(sys.stdin); print(data.get('State','unknown'))" 2>/dev/null || echo "missing")

    if [ "$health" = "healthy" ]; then
        echo "  [OK]     $svc"
    elif [ "$state" = "running" ] && [ "$health" = "" ]; then
        echo "  [OK]     $svc (no healthcheck)"
    else
        echo "  [FAIL]   $svc ($state/$health)"
        # Show last 5 log lines for failed services
        echo "           Last logs:"
        docker compose -f "$COMPOSE_FILE" logs --tail=5 --no-log-prefix "$svc" 2>/dev/null | sed 's/^/           /'
    fi
done
echo ""
echo "=== Deploy verification FAILED ==="
exit 1
