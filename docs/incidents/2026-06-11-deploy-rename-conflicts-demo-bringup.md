# 2026-06-11 — Prod deploys falsely reported "failed" (container-rename conflicts) + demo bring-up to v2.70.1

**Status:** resolved same night. No customer impact (prod health stayed green throughout; demo was degraded ~1 h during bring-up).

## Prod: deployer "failed" while the deploy actually succeeded

The NOC deployer reported both the v2.70.0 (`8193ad1`) and v2.70.1 (`5ffcadc`)
remote compose deploys to BOS-HQ as `status:failed`, then logged
"Auto-healed stale deploy status — service is healthy" once `/api/health`
answered. Verification showed the deploys **had succeeded**: backend/frontend
images were built 04:41 UTC and the containers started 04:42–04:43 UTC from
those digests.

**Mechanism:** `docker compose up -d` recreates a running container by first
renaming it to `<hex>_<name>` and creating the replacement. The v2.70.0 run hit
a daemon error removing the old `droneops-beat-1`; the v2.70.1 run then tripped
over the leftovers. Compose exited non-zero after the services were already up
on new images, and two containers were left holding rename-prefixed names
(`<hex>_droneops-worker-1`, `<hex>_droneops-flight-parser-1`) — healthy, fresh
images, wrong names — primed to re-conflict the *next* deploy.

**Fix:** `docker rename` both back to canonical names. Verified healthy;
`https://droneops.barnardhq.com` → 200.

**Lesson:** the deployer's health-check auto-heal masks compose exit-code
noise *and* rename debris equally. After any "Remote docker compose up failed"
on this repo, check `docker ps` on BOS for `<hex>_`-prefixed droneops
containers before the next deploy, and confirm the running image build
timestamps rather than trusting either the "failed" or the "auto-healed"
status.

## Demo: three stacked blockers on the v2.68.4 → v2.70.1 update

The demo stack (`~/droneops-demo` on BOS-HQ, not deployer-managed) was 8
commits behind. The update hit, in order:

1. **Invalid compose project** — `docker-compose.demo.yml` still overrode the
   `watchtower` service that v2.70.x deleted from the base compose, leaving an
   image-less orphan in the merged project. Fixed in-repo: `81df94c`.
2. **Container name conflict** — the running `droneops-demo-backend-1` had no
   compose labels (created outside compose at some point), so compose tried to
   `Create` rather than `Recreate` and collided on the name, aborting the up
   mid-way (db/redis left in `Created`). Fixed by `docker rm -f` of the
   label-less container + rename debris, then a clean `up -d`.
3. **Stale tunnel token** — recreating the cloudflared sidecar re-read
   `.env.demo`, whose `CLOUDFLARE_TUNNEL_TOKEN` no longer matched the tunnel
   secret ("Unauthorized: Invalid tunnel secret", public 530). The prior
   container had run 5 days on a valid token frozen in its env at create time.
   Fixed by fetching the current token from the Cloudflare API and rewriting
   `.env.demo` (backup: `.env.demo.bak-tunnel-20260611`). Procedure now in
   `docs/cloudflare-tunnel-setup.md` troubleshooting.

**End state:** all 6 demo services healthy at v2.70.1; Alembic brownfield
stamp + upgrade ran per ADR-0022 with demo data preserved; tunnel registered
with 4 connections; `https://command-demo.barnardhq.com` → 200.

**Lesson:** any long-lived sidecar that authenticates with a secret from
`.env` can silently go un-recreatable as on-disk env drifts from the env
frozen in the running container. A green "container running" check does not
prove the on-disk config can still bring it back.
