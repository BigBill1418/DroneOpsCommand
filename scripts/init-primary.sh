#!/bin/bash
# init-primary.sh — Runs inside the PostgreSQL primary container on startup.
# Creates the replicator role and ensures pg_hba.conf allows replication
# from the WireGuard mesh (10.99.0.0/24).
#
# Mounted into /docker-entrypoint-initdb.d/ so it runs once on first init.
# For existing volumes, we also call this from a post-start hook.

set -euo pipefail

REPLICATION_USER="replicator"
REPLICATION_PASSWORD="${REPLICATION_PASSWORD:-SecureDroneRepl2026}"
WIREGUARD_SUBNET="10.99.0.0/24"

# Create replicator role if it doesn't exist
psql -v ON_ERROR_STOP=0 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    DO \$\$
    BEGIN
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '${REPLICATION_USER}') THEN
            CREATE ROLE ${REPLICATION_USER} WITH REPLICATION LOGIN PASSWORD '${REPLICATION_PASSWORD}';
            RAISE NOTICE 'Created replication role: ${REPLICATION_USER}';
        ELSE
            ALTER ROLE ${REPLICATION_USER} WITH PASSWORD '${REPLICATION_PASSWORD}';
            RAISE NOTICE 'Replication role already exists, password updated: ${REPLICATION_USER}';
        END IF;
    END
    \$\$;

    -- Ensure WAL settings for replication
    ALTER SYSTEM SET wal_level = 'replica';
    ALTER SYSTEM SET max_wal_senders = 10;
    ALTER SYSTEM SET wal_keep_size = '512MB';
EOSQL

# Ensure pg_hba.conf has the replication entry for WireGuard subnet
HBA_FILE="${PGDATA}/pg_hba.conf"
REPL_LINE="host replication ${REPLICATION_USER} ${WIREGUARD_SUBNET} scram-sha-256"
DOCKER_LINE_1="host replication ${REPLICATION_USER} 172.16.0.0/12 md5"
DOCKER_LINE_2="host replication ${REPLICATION_USER} 192.168.0.0/16 md5"

if ! grep -qF "${REPL_LINE}" "${HBA_FILE}" 2>/dev/null; then
    echo "${REPL_LINE}" >> "${HBA_FILE}"
    echo "Added replication pg_hba entry for ${WIREGUARD_SUBNET}"
fi

# Docker NAT rewrites source IPs when ports are bound to specific interfaces.
# Allow replication from Docker bridge/overlay networks to support this.
if ! grep -qF "172.16.0.0/12" "${HBA_FILE}" 2>/dev/null; then
    echo "${DOCKER_LINE_1}" >> "${HBA_FILE}"
    echo "${DOCKER_LINE_2}" >> "${HBA_FILE}"
    echo "Added replication pg_hba entries for Docker NAT networks"
fi

echo "Primary replication init complete."
