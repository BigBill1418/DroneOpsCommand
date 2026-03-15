#!/bin/bash
cd /volume1/docker/droneops
echo "=== Pulling latest changes ==="
git pull origin claude/drone-report-generator-qk9UM
echo "=== Rebuilding & restarting ==="
if [ "$1" = "--clean" ]; then
  echo "(clean build — no cache)"
  sudo docker-compose build --no-cache
  sudo docker-compose up -d
else
  sudo docker-compose up -d --build
fi
echo "=== Done! Checking health ==="
sleep 5
sudo docker-compose ps
