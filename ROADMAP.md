# DroneOpsCommand — Roadmap

Forward-looking work items. Shipped scope is tracked in `CHANGELOG.md`;
in-flight scope is tracked in `PROGRESS.md`. This file holds only
not-yet-started work with a clear trigger, scope, and ADR/decision
reference where applicable.

## Observability + Fleet Hygiene (follow-ups from ADR-0002, 2026-04-24)

**Context.** ADR-0002 shipped the primary fix for the operator's DJI RC
Pro flight-record upload failure (HTTPS-only base URL + fresh Capacitor
APK + existing `X-Device-Api-Key` auth). The follow-ups below are
legitimately deferred — not load-bearing for the primary fix, but
necessary to prevent the class of failure from recurring silently on
a different controller.

### FU-1 — Fleet APK version audit — ⚠ DE-PRIORITIZED (2026-04-24, ADR-0002 §5)

- **Scope.** Enumerate every DJI RC Pro / DJI Pilot 2 / DJI Fly device
  that has ever paired with DroneOps. Cross-reference against
  `device_api_keys.last_used_at` and, where possible, any version/UA
  hint the server logged on upload.
- **Trigger.** Was: "before a fleet-wide OTA push can be planned".
  Now: **optional hygiene**. ADR-0002 §5's silence watchdog + layer-1
  banner make fleet-wide drift self-detecting (any controller that
  stops uploading for >48h pages Bill's Pushover, any controller with
  a cleared `Preferences` shows the red banner on next launch). The
  audit is still useful for proactive APK OTA planning but is no
  longer load-bearing against silent data loss.
- **Deliverable.** `scripts/audit-device-fleet.py` + a one-page
  operator report listing each device label, last-seen timestamp, last
  observed APK version (if known), and upgrade plan (OTA-capable vs
  sideload-required).
- **Owner.** TBD. Likely ~1 eng day.

### FU-2 — Unauthenticated `GET /health` shim — ✅ SHIPPED v2.63.4 (2026-04-24)

- Delivered as a plain JSON alias (same payload as `/api/health`).
  Reasoning for deviating from the spec'd "update-required" banner: a
  pre-v2.34 Gson client with `setLenient(false)` would choke on any
  payload that doesn't match its expected shape, so custom banner
  fields buy nothing on the failing client and are confusing to
  modern clients. The WARN log on auth-failure in
  `backend/app/auth/device.py` is the actual stale-client tripwire
  (key_prefix + IP + user-agent + path); FU-3's Grafana panel consumes
  that stream directly. FU-2 rate-limiting not needed since `/health`
  never triggers the WARN path.

### FU-3 — Grafana stale-client tripwire

- **Scope.** Add a panel on the DroneOps Grafana dashboard for
  "Device auth failures (24h)" sourced from the structured WARN log
  emitted by `validate_device_api_key` in
  `backend/app/auth/device.py` (fields: `key_prefix`, `ip`,
  `user_agent`, `path`). Pushover alert at ≥5 hits/24h from ≥2
  distinct IPs (filters out a single responder testing an old phone).
- **Trigger.** v2.63.4 is live. Panel can be shipped any time.
- **Deliverable.** Grafana JSON + alert rule in `~/noc-master` config.
- **Owner.** TBD. ~0.5 eng day.

### FU-4 — Device-key lifecycle policy

- **Scope.** Decide whether `device_api_keys` rows should have an
  automatic expiry/rotation (e.g., 90-day TTL with a 7-day grace
  window where both old+new keys are accepted), or stay indefinite
  revoke-on-demand. Current model is revoke-on-demand only.
- **Trigger.** Before the first real managed-tenant ships (managed
  operators are less likely to tolerate indefinite keys).
- **Deliverable.** One-page decision doc as ADR-0003 or ADR-0004
  (pick next free number), plus schema migration if rotation is
  adopted.
- **Owner.** TBD.

### FU-5 — Managed-tenant discovery (EyesOn ADR-0020 parity)

- **Scope.** When a first DroneOps managed customer is committed,
  port EyesOn's `GET /api/discovery/pair/:code` fan-out pattern +
  tenant-side `GET /api/companion/pair/:code/exists` boolean-only
  endpoint to DroneOps. Companion types a 6-digit code, hits
  discovery on the primary, adopts the tenant URL. No manual URL
  entry, matching `feedback_managed_customer_seamless.md`.
- **Trigger.** First DroneOps managed customer signed / deployment
  scheduled.
- **Deliverable.** Server endpoints + companion integration +
  `MANAGED_TENANT_URLS` env var plumbing on the primary instance's
  `docker-compose.yml`. Copy-paste-with-rename from
  `eyeson-managed`/`EyesOn` repos; estimated 1-2 eng days.
- **Owner.** TBD.

### FU-6 — End-to-end test for `device-upload` auth path

- **Scope.** Integration test that provisions a `DeviceApiKey`,
  hits `/api/flight-library/device-health` with the raw key, then
  `/api/flight-library/device-upload` with a sample DJI flight
  record fixture, and asserts the `Flight` row is created with the
  expected `source_file_hash`. Today the backend has unit coverage
  for the auth dependency; the full upload pipeline is untested
  end-to-end.
- **Trigger.** Any time; good hygiene regardless of ADR-0002's
  immediate fix.
- **Deliverable.** `backend/tests/test_device_upload.py` +
  fixture log file in `backend/tests/fixtures/flight-records/`.
- **Owner.** TBD. ~0.5 eng day.

---

## Older roadmap items

None yet captured here. When a new forward-looking plan is drafted,
append it under its own heading with the same Scope / Trigger /
Deliverable / Owner block structure.

## LLM-assisted report surface (follow-ups from ADR-0015, 2026-05-14)

**Context.** ADR-0015 pinned the contract: *mission reports are
client-facing artifacts; operator-facing coaching is a separate surface.*
The current LLM dispatch (`backend/app/services/llm_provider.py`) and
shared system prompt produce one artifact (the customer-facing report).
The audience-leak incident on 2026-05-14 exposed the gap; the follow-ups
below build out the operator-facing side of the contract and harden the
prompt path against future audience drift.

### FU-AI-1 — Operator retrospective surface (separate from client report)

- **Scope.** Add a second LLM-generated draft to the `MissionReportEdit`
  facet: an operator-facing retrospective ("what went well / what to do
  differently next time / equipment notes") that lives on its own field
  in the `Report` model and is **never** included in the customer PDF or
  email. Distinct system prompt; same dispatcher; same provider routing.
  Operator can opt to generate it, edit it, and keep it on the mission
  record. A future view aggregates retrospectives across missions for
  trend-spotting.
- **Trigger.** ADR-0015 lands (Proposed → Accepted) and the prompt-fix
  soak completes. No earlier — building the second surface before the
  first surface's audience is fixed would repeat the leak.
- **Deliverable.** Schema migration (one nullable text column on
  `Report`), new prompt in `llm_prompts.py`, dispatcher wiring, frontend
  facet addition under `MissionReportEdit`, ADR-0016 if the design
  diverges from ADR-0015's outline.
- **Owner.** TBD. ~1-2 eng days.

### FU-AI-2 — Prompt regression fixture (carried forward from the incident)

- **Scope.** A pytest fixture that exercises the LLM dispatcher with a
  deliberately operator-voiced mission narrative and asserts the output
  contains no second-person address to the operator and no
  operator-coaching prose. Deterministic provider (Ollama,
  `temperature=0`, small model) for CI stability. Optional
  network-marked Claude path the operator can run locally.
- **Trigger.** Lands with the prompt fix (aegis's patch for the
  2026-05-14 incident). Listed here for tracking only — not a deferred
  item.
- **Deliverable.** `backend/tests/test_llm_report_audience.py`.
- **Owner.** aegis.

### FU-AI-3 — Prompt source-of-truth relocation

- **Scope.** Move `SYSTEM_PROMPT_TEMPLATE` (and the future operator
  retrospective prompt) out of `backend/app/services/ollama.py` and into
  `backend/app/services/llm_prompts.py`. Update imports in `claude_llm.py`
  and `ollama.py`. The prompt is the cross-provider contract; it does
  not belong in one provider's adapter.
- **Trigger.** Lands with the prompt fix (aegis's patch for the
  2026-05-14 incident). Listed here so the architectural improvement is
  visible on the roadmap even though it ships in the same commit.
- **Deliverable.** Refactor commit + one-line import update in two
  files.
- **Owner.** aegis.

### FU-AI-4 — Per-tenant prompt override (managed-hosting only, optional)

- **Scope.** For managed-hosting tenants that want to brand the report
  voice differently ("warm and conversational" vs "technical and
  terse"), expose a tenant-scoped prompt-fragment override in
  `system_settings` (key e.g. `llm_prompt_tone_addendum`). The
  audience-separation invariant from ADR-0015 stays hard-coded and not
  overridable; only the tone-shaping addendum is tenant-tunable.
- **Trigger.** First managed-hosting customer asks for a different
  voice. Not before — premature flexibility.
- **Deliverable.** One new setting key, one prompt-construction site
  updated, one Settings-page UI element gated to `managed_instance=true`.
- **Owner.** TBD. ~0.5 eng day.

### FU-7 — Zero-touch device API key rotation — **CLOSED 2026-04-24** (v2.63.6 / DroneOpsSync v1.3.25)

- **Status.** PR open against `main` on this repo (`claude/zero-touch-key-rotation-backend`); paired DroneOpsSync PR open against `main` (`claude/auto-rotation-client`). Operator reviews + merges.
- **Shipped scope.** Backend grace-window dual-key auth + rotated-key hint in `/api/flight-library/device-health` response + Celery finalizer task (15-min beat) + Pushover FYI + admin endpoint `POST /api/admin/devices/{id}/rotate-key`. Bootstrapped `backend/tests/` infrastructure (15 tests, all green).
- **Trigger.** Bill rotated M4TD in-place 2026-04-24 AM; operator had to manually paste new key on RC Pro Settings. v1.3.24's preflight gate surfaced the invalid-key state correctly, but eliminating the paste step was the real goal.
- **Deliverable.** ADR-0003 (`docs/adr/0003-zero-touch-device-key-rotation.md`) + plan (`docs/plans/2026-04-24-zero-touch-key-rotation.md`) + migration + endpoint + Celery task + tests. Remote routine `trig_01KiBK88vqs6vtRf75rkxcw8` initially shipped an empty branch; aegis re-ran and produced both PRs.
- **Owner.** aegis (scaffold); Bill (review + merge).
