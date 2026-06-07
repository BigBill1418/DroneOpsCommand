# Incident: CF `droneops-api-health` flap — 2026-06-05 (external, NOT a DroneOps defect)

**Author:** Terry (documentation lane)
**Status:** Closed. Root cause was external host-infrastructure maintenance on BOS-HQ; the DroneOps application had **no defect** and required **no code change**.
**Severity:** Customer-visible origin unavailability ~10 min, but the cause and fix live entirely outside this repo. Logged here only so a future session that sees the alert in history does not chase a phantom DroneOps backend bug.

## 1. Symptom

The Cloudflare healthcheck `droneops-api-health`
(`https://droneops.barnardhq.com/api/health`, id `0c00350dc5dcbfacc62bcff445b04bbd`)
flipped **Unhealthy for ~10 min on 2026-06-05 08:52–09:02 PDT (15:52–16:02 UTC)**
and emailed `bill@barnardhq.com`.

## 2. Root cause (external)

Self-inflicted host-firewall maintenance on **BOS-HQ** (the host fronting this
origin), unrelated to DroneOps code. A first attempt at the red-team "no host
firewall" remediation applied `DOCKER-USER` iptables rules **live** with
iterative `iptables -I/-D` churn **plus `systemctl reload docker`**. The reload
rebuilt docker's NAT/FORWARD/DOCKER chains and momentarily severed the
`droneops-cloudflared` → DroneOps backend origin path. Cloudflare's edge
reported `origin=530` ("origin unreachable" — an **edge-side** verdict, **not** a
backend 503 or any DroneOps error). The DroneOps app was healthy throughout.

## 3. Why this is not a DroneOps issue

- No DroneOps process crashed, errored, or returned 5xx; `/api/health` itself
  never failed at the app layer — the request never reached it during the blip.
- No DroneOps code, config, or deploy was involved or changed.
- The fix is a host-infrastructure change on BOS-HQ, not in this repo.

## 4. Durable fix + full RCA (other repo)

The BOS-HQ firewall was made declarative, atomic (`iptables-restore --noflush`),
and persistent across dockerd restart + reboot, with a safe-change runbook. Full
incident RCA, decision, and alternatives:

- `infrastructure-hardening` **ADR-0001 — BOS DOCKER-USER firewall atomic persistence**
  (`BigBill1418/infrastructure-hardening`, `docs/adr/0001-bos-docker-user-firewall-atomic-persistence.md`)
- Safe-change runbook: `infrastructure-hardening` `docs/runbooks/bos-hq-host-firewall-safe-change.md`

## 5. DroneOps-side action

None required. The `droneops-api-health` healthcheck behaved **correctly** — it
fired on a real, customer-visible origin outage. Do not desensitize it. The only
gap was maintenance-window context, which the BOS runbook addresses (suspend the
CF healthcheck before planned host-network maintenance).
