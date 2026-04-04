#!/bin/bash
# primary-entrypoint.sh — Wraps the default PostgreSQL entrypoint.
# Ensures pg_hba.conf has replication entries even on existing volumes
# (where /docker-entrypoint-initdb.d/ scripts do NOT run).

set -euo pipefail

REPLICATION_USER="replicator"
WIREGUARD_SUBNET="10.99.0.0/24"
REPL_LINE="host replication ${REPLICATION_USER} ${WIREGUARD_SUBNET} scram-sha-256"

# If PGDATA already exists (not a fresh init), patch pg_hba.conf before PG starts
if [ -f "${PGDATA}/pg_hba.conf" ]; then
    if ! grep -qF "${REPL_LINE}" "${PGDATA}/pg_hba.conf"; then
        echo "${REPL_LINE}" >> "${PGDATA}/pg_hba.conf"
        echo "[primary-entrypoint] Added replication pg_hba entry for ${WIREGUARD_SUBNET}"
    fi
fi

# Hand off to the standard PostgreSQL entrypoint
exec docker-entrypoint.sh "$@"
