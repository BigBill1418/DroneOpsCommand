# DroneOps Command — Managed Hosting

DroneOps Command Hosted is a fully managed instance operated by BarnardHQ.

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

## Behavioral Differences

When `MANAGED_INSTANCE=true`:

1. **Setup wizard is skipped** — admin user auto-created from env vars on first boot
2. **LLM provider locked to Claude** — Ollama settings hidden, provider selection disabled
3. **Health endpoint** includes `managed: true` and `client_id` for monitoring
