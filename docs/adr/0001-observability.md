# ADR-0001 — Observability: structured JSON logging + Sentry/GlitchTip + OpenTelemetry

- **Status:** Accepted
- **Date:** 2026-04-18
- **Phase:** 5 (DroneOps) of the BarnardHQ observability rollout
- **Related commits:** `6b7e626` (JSON logging), `d4df8e7` (SDKs + compose labels)

## Context

DroneOpsCommand runs in two topologies inside the BarnardHQ mesh:

1. **Prod** — `~/droneops/` on HSH-HQ, served at
   `https://command.barnardhq.com` and `https://droneops.barnardhq.com`.
2. **Demo** — same repo + `docker-compose.demo.yml` override on CHAD-HQ,
   served at `https://command-demo.barnardhq.com`. The memory record
   `reference_droneops_topology.md` mandates verifying BOTH after every
   deploy.

Before Phase 5, observability was:

- Plain `logging.basicConfig("[%(levelname)s] ...")` — prose, not JSON.
  Alloy could tail the streams but Grafana queries had to regex-extract
  fields.
- No error capture — a 500 from a Celery task or FastAPI handler was
  visible only if someone was tailing `docker logs` at the moment.
- No distributed tracing — a slow `/api/reports` call could not be
  correlated across the backend, the worker, and Ollama.
- No frontend crash reporting — JS errors at the customer side were
  invisible.

The BarnardHQ central plane (GlitchTip + Loki + Tempo + Alloy) had
shipped in Phases 1-4 (HSH-HQ standup, CHAD-HQ Alloy bootstrap, Helix-Hub,
CallVault, EyesOn). Phase 5's job was to bring DroneOps onto it without
risking the tier-1 first-responder workload.

## Decision

Three additive changes, staged in two commits (plus this ADR as the third):

### 1. Structured JSON logging (pre-req)

Replaced `logging.basicConfig` in `backend/app/main.py` with a
`python-json-logger` `JsonFormatter` wired on the root logger. The Celery
worker swaps its handler formatters via the `after_setup_logger` and
`after_setup_task_logger` signals so the API and worker streams land in
Loki with the same shape.

Fields (renamed for Grafana convenience):

```json
{"timestamp": "...", "level": "INFO", "name": "doc", "message": "..."}
```

### 2. Sentry/GlitchTip + OpenTelemetry SDKs

- Backend: `backend/app/observability/{sentry,otel,pii}.py`.
- Frontend: `frontend/src/lib/sentry.ts` invoked from `main.tsx`.
- DSN/endpoint gated — unset env = no-op. Self-hosted single-tenant
  DroneOps installs keep working without the central plane.
- PII scrubbing in the backend `before_send` hook — redacts emails and
  loose phone numbers from free-text. **Fail-closed**: if the scrubber
  raises, the event is dropped rather than risk leaking.
- Every init path is wrapped — a failed SDK import, a broken DSN, a
  double-instrument attempt, or an unreachable collector logs a WARNING
  and continues. Observability must never fail app startup.

### 3. Compose labels + json-file logging driver

- `com.barnardhq.{project,env,tenant,stack,service}` labels on every
  service in `docker-compose.yml` via YAML anchors.
- `docker-compose.demo.yml` parallel anchors with `env=demo`,
  `stack=droneops-demo`, `SENTRY_ENVIRONMENT=demo`,
  `OTEL_EXPORTER_OTLP_ENDPOINT=http://10.99.0.2:4317` (CHAD-HQ Alloy).
- Shared `json-file` logging driver config with 50m × 5 rotation —
  matches the Helix-Hub / CallVault / EyesOn pattern.

### Endpoint routing

| Host    | Alloy OTLP         | GlitchTip project  | `env` label |
| ------- | ------------------ | ------------------ | ----------- |
| HSH-HQ  | `10.99.0.1:4317`   | `droneops` (10)    | `prod`      |
| CHAD-HQ | `10.99.0.2:4317`   | `droneops` (10) /  | `demo`      |
|         |                    | `droneops-frontend`|             |
|         |                    | (11)               |             |

DSN values are pinned in `/home/bbarnard065/.secrets/observability-dsns.env`
and injected into `~/droneops/.env` + `~/droneops/.env.demo` out-of-band.
That file has mode 0600 and is never committed.

## PII posture

DroneOps stores customer names, recipient emails, phone numbers (intake
forms), mission details (location, narrative), and flight logs. None of
it is PHI. The only reportable PII classes that can leak into Sentry
events are emails and phone numbers embedded in free-text strings — for
example, from SMTP send failures, Stripe webhook handlers, or exception
messages that happen to quote a customer field.

Unlike CallVault (which has a locked `PII_KEYS` list because voicemails
and transcripts cannot leak even as key:value pairs), DroneOps uses
only the free-text regex redaction path in
`app/observability/pii.py`. The Sentry `before_send` hook runs the
scrubber on every event; a failure in the scrubber drops the event.

## Resilience

DroneOps is first-responder tier-1. Zero tolerance for downtime from
observability changes. The safety measures:

1. **DSN/endpoint gating** — unset env = no-op at every entry point.
2. **Import guards** — a missing SDK wheel logs a warning, returns False.
3. **Init wraps** — `sentry_sdk.init` and `TracerProvider` construction
   are in try/except; on failure, the function returns False and the
   app boots normally.
4. **Instrumentation is best-effort** — each auto-instrumentor is
   wrapped individually so a single bad hook doesn't take down the rest.
5. **Front-end SDK init is also wrapped** — `initFrontendSentry()`
   returns false on any throw; React still mounts.

## Log-shape change — operator notice

Any downstream consumer that greps plaintext `[INFO]` / `[WARNING]`
level prefixes in DroneOps container logs must migrate to JSON parsing
(`.level`, `.message`, `.name`, `.timestamp`). Alloy already parses
JSON at ingest — Grafana dashboards queries against `droneops-api` /
`droneops-worker` streams benefit immediately. No operational runbooks
reference the old format.

## Alternatives considered

- **structlog for backend logging (as Helix-Hub uses).** Rejected for
  DroneOps because the existing code uses stdlib `logging.getLogger`
  everywhere. `python-json-logger` formats the output of that same API
  as JSON without requiring every caller to switch. `structlog` remains
  a better fit when new projects start fresh.
- **Per-project demo GlitchTip project.** The playbook allows a separate
  `DRONEOPS_DEMO_API_SENTRY_DSN`; we use the shared `DRONEOPS_API_SENTRY_DSN`
  because the `env=demo` tag disambiguates and single-project-with-tag is
  the locked decision (`reference_observability_decisions.md`).
- **Always-on tracing.** Sample rate is 0.05 per the observability
  plane's throughput budget; overridable via `SENTRY_TRACES_SAMPLE_RATE`
  when a deeper investigation is running.

## Consequences

- Grafana gets three new streams: `droneops-api`, `droneops-worker`,
  `droneops-frontend` (prod + demo via the `env` label).
- GlitchTip project `droneops` (10) captures backend errors + Celery
  task failures. Project `droneops-frontend` (11) captures JS errors.
- Tempo gets trace graphs for `droneops-api`, `droneops-worker`, and
  any OTel-instrumented downstream (SQLAlchemy, httpx, Celery).
- `/api/health` traces are the smoke test — a hit on the prod URL must
  produce a trace in Tempo within ~10s of the request.

## References

- `/home/bbarnard065/docs/plans/2026-04-18-observability-phases-2-7-session-playbook.md` §5 (Aegis-D)
- `reference_observability_decisions.md` (locked 2026-04-17)
- `reference_droneops_topology.md` (verify BOTH hosts after every deploy)
- `feedback_droneops_companion_apk.md` (companion APK instrumentation follow-up)
- Helix-Hub ADR-0007-observability (sibling implementation)
- CallVault ADR-0003-observability (sibling implementation, PII-heavy variant)
