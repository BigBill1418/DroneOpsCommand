#!/bin/bash
cd /volume1/docker/droneops
echo "=== Pulling latest changes ==="
git pull origin claude/drone-report-generator-qk9UM
echo "=== Rebuilding & restarting ==="
docker-compose up -d --build
echo "=== Done! Checking health ==="
sleep 5
docker-compose ps
