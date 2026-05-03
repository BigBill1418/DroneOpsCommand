# ADR-0013 — Customer-flow contract tests + 4xx-burst alerting

**Status:** Accepted (2026-05-03, post-incident)
**Date:** 2026-05-03
**Triggered by:** TOS-accept 422 incident (v2.66.0/0.1 → hotfix v2.66.2)

## Context — the incident this is responding to

Five hours after shipping v2.66.0 + v2.66.1 (and roughly 24 hours after the deposit + AcroForm-TOS subsystems went live in v2.65.0), the **first real paying customer** opened an intake link, filled out the TOS form, and clicked Accept & Sign. They saw a generic "acceptance failed" toast. They retried six times in five minutes. Operator (Bill) heard about it from the customer.

Backend logs showed every attempt returning **HTTP 422 in <2 ms**. The application code never executed — Pydantic validation rejected the request body before the route handler ran.

### Root cause

A `from __future__ import annotations` directive at the top of `backend/app/routers/tos.py` (PEP 563 — postponed annotation evaluation) string-ified the `payload: TosAcceptanceRequest` parameter annotation. FastAPI couldn't introspect the annotation as a `BaseModel` subclass at decoration time, fell through to the default `Query()` parameter handling, and treated the JSON body as an empty query-string object. Every POST therefore failed with `loc=['query','payload']` Pydantic 422.

### Why the existing tests didn't catch it

- **Unit tests for the helper module (`tos_acceptance.py`) pass** — they test the pure function, not the route.
- **Unit tests for the route via `_mk_payload(SimpleNamespace(...))` pass** — they bypass FastAPI's parameter parsing entirely by passing the model object directly to a wrapper.
- **No test exists that exercises the route through `httpx.AsyncClient` / FastAPI `TestClient` with a real JSON body** matching the frontend's payload shape.
- **No end-to-end live smoke** was run between push and customer-arrival.

The contract between the frontend's actual JSON payload and the backend's actual route was never tested.

### Why monitoring didn't surface it sooner

- **Sentry/GlitchTip** receives 5xx exceptions, not 4xx validation rejections — 422 from Pydantic doesn't reach Sentry unless explicitly forwarded.
- **CF Healthcheck** polls `/api/health`, not `/api/tos/accept` — the public POST endpoint had no out-of-band canary.
- **ntfy alerts** are wired for webhook signature failures, deposit/balance paid, and operator-relevant events — but not for "high rate of 4xx on a public endpoint."
- **The operator found out from the customer.** That's the failure mode this ADR closes.

## Decision

Three structural changes go in alongside the v2.66.2 hotfix:

### 1. Frontend↔backend contract tests for every public customer endpoint

For each `/api/tos/*`, `/api/intake/*`, `/api/client/*`, `/api/webhooks/stripe`, and `/api/health` route, a test exists in `backend/tests/contract/` that:

- Loads the **exact JSON payload shape** the frontend sends (sourced from the TS API client interface — `frontend/src/api/*.ts` — copied verbatim into the test as a fixture).
- POSTs/GETs through `httpx.AsyncClient(app=app, base_url=...)` against the real route definition.
- Asserts on status code + response shape.

The pattern `_mk_payload(SimpleNamespace(...))` is **explicitly forbidden** in any contract test. The test must hit the route the same way an HTTP client would.

The `backend/tests/test_tos_accept_route_body.py` file added in v2.66.2 is the prototype. New routes get a parallel test file before merge.

### 2. 4xx-burst alerting on public endpoints

A new alerting rule is added to the fleet's Loki + Alloy pipeline:

> If any of the routes below sees **≥3 4xx responses from ≥1 distinct client IP within 5 minutes**, page the operator via ntfy (priority `high`, topic `droneops-customer-flow-alerts`):
>
> - `POST /api/tos/accept`
> - `POST /api/intake/initiate`
> - `POST /api/intake/submit/*`
> - `POST /api/client/missions/*/invoice/pay/*`
> - `POST /api/webhooks/stripe`

This catches three customer attempts hitting the same broken endpoint in the same five minutes — exactly the signal we missed today. The rule lives in InfraWatch's Grafana alert configuration; topic registration in `~/noc-master/data/service-registry.json` + ntfy fallback registry. Click URL: `https://noc-mastercontrol.barnardhq.com/status/droneops`.

For Sentry: enable the `ASGI`/`FastAPI` integration's `failed_request_status_codes` to forward 4xx (specifically 422 + 4xx-on-customer-routes) to GlitchTip so the operator sees them in the error dashboard alongside 5xx exceptions.

### 3. Pre-deployment customer-flow smoke checklist (mandatory)

Every release that touches a customer-facing surface (`/api/tos/*`, `/api/intake/*`, `/api/client/*`, `/api/webhooks/stripe`, `/tos/accept`, `/client/*`) MUST execute the following BEFORE "shipped" is declared in CHANGELOG:

1. Generate a fresh intake token via `POST /api/intake/initiate`.
2. Open `/tos/accept?token=…&customer_id=…` in a real browser.
3. Fill the form, click Accept.
4. Confirm `tos_acceptances` row created + signed PDF written + customer email synced.
5. Generate a portal magic link, open in a private window, visit the mission page.
6. (If invoice exists) Click Pay Deposit → Stripe Checkout → cancel out (no need to actually charge).
7. Verify the canceled payment fires the `?payment=cancel` toast correctly.

Checklist captured in `docs/runbooks/2026-05-03-customer-flow-smoke.md` with copy-pasteable commands. Future releases reference this runbook in their CHANGELOG entry.

## Consequences

- **Slower releases** — every customer-flow change now requires the contract test exists before merge + the smoke runbook executed before declared-shipped. Trade-off: this incident took 6+ failed customer attempts to surface. The runbook is 5 minutes; six failed attempts is unbounded reputational damage.
- **More monitoring noise potential** — the 4xx-burst threshold (≥3 in 5 min from ≥1 IP) may fire on legitimate operator testing or determined customer retries on a known-correct payload. Tune in v2.66.3+ once we have baseline traffic.
- **PEP 563 (`from __future__ import annotations`) is now banned in router files.** Documented as an inline NOTE comment in `backend/app/routers/tos.py`. Other routers don't currently use it; an audit confirmed.

## Alternatives considered

- **Generated TypeScript client from OpenAPI spec.** Would fully eliminate frontend↔backend drift. Rejected as v0 because: (a) requires a build-step toolchain we don't have; (b) the contract tests give 80% of the value at 10% of the effort; (c) we can revisit after onboarding the third frontend developer (which is "never" for this solo-operator instance).
- **Pre-merge synthetic customer flow run via Playwright/Cypress.** Heavier infrastructure than warranted today. Manual runbook is the v0; automated browser test is a v1 item if we ever scale beyond one operator + a handful of customers.
- **Rolling deploy with canary traffic.** Doesn't help here because there's only one customer instance — every customer goes to the same release.

## What this ADR does NOT do

- It does not retroactively rewrite history or notify the affected customer. Bill needs to manually reach out to customer `824d055a-…` with apology + a fresh working link. Surfaced separately.
- It does not introduce a generated client or a heavy test framework. Lightweight contract tests + manual smoke + 4xx alerting only.
- It does not address the broader "what should already be here" gaps from the v2.66.0 audit (those have their own ADRs / follow-ups).

## Implementation status

- **v2.66.2 (already shipped 2026-05-03 ~16:31 UTC)** — the contract test prototype `test_tos_accept_route_body.py` exists; the 4-test suite catches the regression.
- **v2.66.3 (next round)** — port the contract-test pattern to the other 4 customer-facing endpoints listed above; add the 4xx-burst alert rule.
- **v2.66.4 or later** — write `docs/runbooks/2026-05-03-customer-flow-smoke.md` and reference it from `CLAUDE.md`'s release-checklist section.
