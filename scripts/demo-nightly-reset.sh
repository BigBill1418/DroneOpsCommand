#!/usr/bin/env bash
# Nightly demo reset — wipe the demo DB to a pristine fictional seed.
#
# The demo instance (command-demo.barnardhq.com) is a public trial: visitors
# create junk data (uploads, contacts, test rows) that accumulates until it
# looks unprofessional and, worse, exposes one visitor's data to the next.
# DEMO_RESET_INTERVAL_HOURS exists in the demo env but nothing in the app
# implements it (candidate follow-up: an in-backend task — celery beat is NOT
# an option because the demo worker/beat must stay stopped, dunning hazard).
# Until then this script IS the reset: drop the demo schema and restart the
# backend, whose startup path rebuilds it (alembic upgrade head + demo seed).
#
# Runs on BOS-HQ from the operator crontab. Demo-stack-only by construction:
# every docker target is the droneops-demo-* container set.
set -euo pipefail

DB_CONTAINER="droneops-demo-db-1"
BACKEND_CONTAINER="droneops-demo-backend-1"
DB_USER="doc_demo"
DB_NAME="doc_demo"
DEMO_URL="https://command-demo.barnardhq.com"
NTFY_HELPER="${HOME}/.local/bin/ntfy-publish.sh"

fail() {
  echo "demo-nightly-reset: FAIL — $1" >&2
  if [[ -x "$NTFY_HELPER" ]]; then
    "$NTFY_HELPER" --topic "droneops-demo-reset" \
      --title "[DroneOpsCommand Demo] nightly reset FAILED" \
      --priority high --tags "warning,droneops,demo" \
      --click "$DEMO_URL" \
      "$1 — demo may be down or serving stale/junk data. Runbook: scripts/demo-nightly-reset.sh" || true
  fi
  exit 1
}

docker exec "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -q \
  -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public; GRANT ALL ON SCHEMA public TO ${DB_USER};" \
  || fail "schema wipe errored"

docker restart "$BACKEND_CONTAINER" >/dev/null || fail "backend restart errored"

# Startup runs alembic upgrade head + demo seed; wait for container-healthy.
for _ in $(seq 1 30); do
  state="$(docker inspect "$BACKEND_CONTAINER" --format '{{.State.Health.Status}}' 2>/dev/null || echo unknown)"
  [[ "$state" == "healthy" ]] && break
  sleep 10
done
[[ "$state" == "healthy" ]] || fail "backend not healthy 300s after reset (state=$state)"

# Verify the trial actually works: login must return 200 with a token.
code="$(curl -s -o /tmp/demo-reset-login.json -w '%{http_code}' --max-time 30 \
  -X POST "${DEMO_URL}/api/auth/login" \
  -H 'Content-Type: application/json' \
  -d '{"username":"demo","password":"demo123"}')" || fail "login probe curl errored"
[[ "$code" == "200" ]] || fail "post-reset demo login returned HTTP $code"
grep -q access_token /tmp/demo-reset-login.json || fail "post-reset login response missing access_token"
rm -f /tmp/demo-reset-login.json

echo "demo-nightly-reset: OK — demo reseeded and login verified"
