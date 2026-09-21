# DroneOps Command — Managed Hosting

DroneOps Command Hosted is a fully managed instance operated by BarnardHQ.

> **Operator note (2026-09-21).** The pricing and inclusions below are the
> commercial offer and are not verifiable from this repo — confirm against the
> current published offer before quoting them. Everything from
> **Environment Variables** down *is* verifiable and was re-checked against the
> running estate on 2026-09-21 (BOS-HQ `droneops-managed-gateway`,
> `docker network inspect droneops-managed` → `172.29.0.0/16`,
> `backend/app/config.py`).

## What's Included

- All features: missions, AI reports (Claude-powered), Client Portal, pilot management, fleet tracking, flight logs, financial engine
- Unlimited missions, storage, and users — no caps
- Custom subdomain: `yourname.droneops.barnardhq.com`
- Automated backups and health monitoring
- Email support (support@barnardhq.com)

## Pricing

| Plan | Price | Setup |
|------|-------|-------|
| Monthly | $79/mo | $200 one-time |
| Annual | $790/yr (2 months free) | $200 one-time |

## What's Different from Self-Hosted

| Feature | Self-Hosted | Managed |
|---------|------------|---------|
| AI Reports | Ollama (local) or Claude API | Claude API (included) |
| Hosting | Your server | BarnardHQ infrastructure |
| Updates | Manual | Managed by BarnardHQ |
| Setup | Docker Compose | Automatic provisioning |
| LLM Settings | Configurable | Locked to Claude |
| Setup Wizard | Interactive | Auto-provisioned |

## Environment Variables

Managed instances use these additional env vars:

| Variable | Description |
|----------|-------------|
| `MANAGED_INSTANCE` | `true` — enables all managed gates |
| `CLIENT_ID` | Unique client identifier |
| `ADMIN_USERNAME` | Auto-provisioned admin username |
| `ADMIN_PASSWORD` | Auto-provisioned admin password |
| `LLM_PROVIDER` | Forced to `claude` on managed |
| `ANTHROPIC_API_KEY` | BarnardHQ's API key |
| `TRUSTED_PROXY_HOSTNAME` | **Required, operator action** — set to `caddy` (see below) |
| `FORWARDED_ALLOW_IPS` | **Required for accurate rate-limit keys** — see below |

### `TRUSTED_PROXY_HOSTNAME` / `FORWARDED_ALLOW_IPS` — required for managed tenants (2026-09-21)

Every rate limiter and the login lockout resolve "the real caller" via
`backend/app/utils/client_ip.py`. That module's default
(`TRUSTED_PROXY_HOSTNAME=frontend,cloudflared`) matches the **primary**
single-tenant compose topology (`docker-compose.yml`), where nginx
(`frontend`) always proxies to uvicorn. **Managed tenants use a different
topology and that default does not apply:**
`droneops-managed/templates/Caddyfile.client`'s `handle /api/*` block
reverse-proxies straight from that tenant's own `caddy` sidecar to
`backend:8000`, bypassing the tenant's `frontend` (nginx) entirely — so
uvicorn's direct peer for every customer API call is the tenant's `caddy`
container, not `frontend`.

**This repo cannot set that for you** — `docker-compose.managed.yml` /
`Caddyfile.client` live outside this repo (BOS-HQ, operator-managed
templates), so provisioning them is an operator action, not something this
branch changes:

- `TRUSTED_PROXY_HOSTNAME=caddy` — resolves via Docker's embedded DNS
  scoped to that one tenant's own `droneops-internal` network (the compose
  service is literally named `caddy` in `docker-compose.managed.yml`); this
  is unambiguous per tenant, since each tenant's network is isolated.
- If the shared `droneops-managed-gateway` sits in front of each tenant's
  `caddy` (it does, per the current template's two-level routing — a
  top-level shared gateway that forwards by hostname to
  `droneops-<client>-caddy`), that hop's IP also needs trusting for the
  resolved identity to be the real caller rather than the gateway. A
  tenant's own network **cannot** resolve `droneops-managed-gateway` by
  hostname (it lives on the separate `droneops-managed` Docker network) —
  set `FORWARDED_ALLOW_IPS` to that network's CIDR instead (verify live via
  `docker network inspect droneops-managed` on BOS-HQ; at time of writing
  this is `172.29.0.0/16`, but Docker does not guarantee a bridge network's
  subnet is stable across a network recreate — reconfirm before relying on
  the literal).

**Until this is set, a managed tenant fails closed, not open**: an
unrecognized peer means `X-Forwarded-For` is ignored entirely and the raw
peer (`caddy`'s own IP, constant for that one tenant) is returned — the
same "one shared bucket" limitation the primary topology had before Phase 7,
scoped to that single tenant's own customers, not a new or spoofable defect
introduced by this fix. See `backend/app/utils/client_ip.py`'s module
docstring and ADR-0045's 2026-09-21 correction entry for the full
reasoning.

## Behavioral Differences

When `MANAGED_INSTANCE=true`:

1. **Setup wizard is skipped** — admin user auto-created from env vars on first boot
2. **LLM provider locked to Claude** — Ollama settings hidden, provider selection disabled
3. **Health endpoint** includes `managed: true` and `client_id` for monitoring
