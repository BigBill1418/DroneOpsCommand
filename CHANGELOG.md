> **Maintained automatically by NOC doc-autogen.** This file is refreshed twice daily (04:00 + 16:00 UTC) by `~/noc-master/scripts/doc-autogen.py`, which summarizes recent commits via Claude Haiku 4.5 and commits with a `[skip-deploy]` trailer so no container rebuilds are triggered. See [NOC-Master ADR-0013](https://github.com/BigBill1418/NOC-Master-Control-SWARM/blob/main/docs/decisions/ADR-0013-docs-only-deploy-skip.md). Manual edits are preserved — the generator diffs against existing content before writing.

# Changelog

Notable changes to DroneOpsCommand. Dates are absolute (YYYY-MM-DD, UTC).

## [unreleased] — 2026-05-18 — feat(reviews): Google review CTA across invoice + checkout surfaces

Five customer-facing touchpoints now surface a Google review prompt
keyed off a single `GOOGLE_REVIEW_URL` env var (defaults to the
BarnardHQ business profile short link). Asking at the moment of
highest goodwill — once the invoice clears — is the cheapest review
acquisition surface we have, and the PDF invoice doubles as a
permanent ask that lives in the customer's inbox.

### Added

- **`backend/app/config.py`** — `google_review_url` setting (default
  `https://g.page/r/Cbblmcdaz3GfEBM/review`; override via
  `GOOGLE_REVIEW_URL`). Empty value hides every CTA — all templates
  gate on truthiness.
- **`backend/app/templates/report_pdf.html`** — review line beneath
  the invoice notes block, visible in every emailed/downloaded
  invoice PDF.
- **`backend/app/templates/email_body.html`** — yellow "leave a
  Google review" CTA card injected into the report-delivery email
  (the message that carries the invoice PDF).
- **`backend/app/templates/payment_received_email.html`** — gold-on-
  dark CTA card directly under the "INVOICE PAID IN FULL" success
  badge in the Stripe receipt email.
- **`backend/app/templates/report_ready_email.html`** — light-theme
  CTA card under the "Next Steps" block in the report-ready
  notification.
- **`frontend/src/pages/client/ClientMissionDetail.tsx`** — gold
  review card surfaces in the client portal when
  `invoice.paid_in_full === true`. This is the page customers land on
  after the Stripe `?payment=success` redirect, so the ask hits at
  the exact moment of confirmed payment.
- **`backend/app/routers/client_portal.py`** + **`schemas/client_portal.py`** —
  `ClientInvoiceResponse` now carries `google_review_url` so the
  frontend doesn't have to ship its own copy of the URL.
- **`backend/app/services/email_service.py`** — `_get_branding()`
  injects `google_review_url` into every template render context, so
  every existing and future email template gets the variable
  automatically.
- **`backend/app/services/pdf_generator.py`** — passes
  `google_review_url` into the PDF Jinja context.
- **`backend/tests/test_google_review_cta.py`** — 8 template-render
  tests covering the present/absent matrix across all four Jinja
  templates.
- **`.env.example`** — documents `GOOGLE_REVIEW_URL` next to the
  Stripe block.

## [unreleased] — 2026-05-17 — fix(llm): bump Claude model + stop blaming Ollama in error toasts

Report generation was silently failing on Claude-configured instances
because `backend/app/services/claude_llm.py:10` hardcoded the Sonnet
model snapshot `claude-sonnet-4-20250514` (May 2025). That snapshot is
retired; Anthropic returns model-not-found, the celery task retries
3x then dies, and the frontend's catch-all toast was hardcoded to
**"Could not generate report. Is Ollama running?"** — making a Claude
failure look like an Ollama outage even when Ollama wasn't in the
picture at all.

### Changed

- **`backend/app/config.py`** — added `claude_model` setting (default
  `claude-sonnet-4-6`, override via `CLAUDE_MODEL` env var). The
  rationale comment in-file warns future readers not to hardcode model
  IDs in service modules.
- **`backend/app/services/claude_llm.py`** — read model from
  `settings.claude_model` instead of a module-level constant.
- **`backend/app/routers/llm.py`** — `/api/llm/status` now reports the
  configured model from settings rather than a stale hardcoded string,
  so the Settings page's "configured model" display stays accurate.
- **`frontend/src/pages/MissionReportEdit.tsx`** + **`MissionWizardLegacy.tsx`** —
  generation-failure toast no longer blames Ollama. The new fallback
  reads "Check Settings → AI for provider status." The backend's
  `err.response.data.detail` is still preferred when present.

## [unreleased] — 2026-05-16 — feat(missions): derive aircraft from flight log on attach

The mission editor no longer asks the operator to pick which drones flew a
mission, or to assign each attached flight to an aircraft. Both pieces of
information already live on the flight log (matched to the fleet at upload
time by serial/model), so the editor was just making the operator key in
data the system already had — slowly, manually, and in a way that could
disagree with the underlying flight record.

### Changed

- **`backend/app/routers/missions.py:381-442`** (`add_flight`) — derives
  `MissionFlight.aircraft_id` from `Flight.aircraft_id` when the attach
  references a native flight, and fleet-matches by serial/model from the
  cache for legacy ODL rows. Any client-sent `aircraft_id` in the request
  body is ignored — the flight log is the single source of truth.
- **`frontend/src/pages/MissionWizardLegacy.tsx`** (`/missions/:id/edit-legacy`) —
  same cleanup applied to the soak fallback so the bad UX is unreachable
  from any route: removed the AIRCRAFT USED checkbox group on Step 2,
  collapsed the DRONE + ASSIGN AIRCRAFT columns into a single read-only
  AIRCRAFT cell (driven by `flightAircraftLabel(flight, fleet)`), removed
  `handleAssignAircraft` and its PATCH call, removed `missionAircraft`
  state, and made Step 6's aircraft-card row derive from
  `selectedFlights[]._aircraftId` instead of operator picks.
- **`frontend/src/pages/MissionFlightsEdit.tsx`** — full rewrite:
  - Removed the **AIRCRAFT USED** checkbox card (the per-mission drone
    multi-select). Distinct aircraft are now derived from attached flights
    and shown as read-only badges in the ATTACHED FLIGHTS header. Unmatched
    flight-log drones show as a yellow `(unmatched)` badge so the operator
    can fix the source flight on the Flights page.
  - Removed the **ASSIGN AIRCRAFT** column from the attached table.
    Replaced with a read-only AIRCRAFT column showing the fleet aircraft
    name (or the cached drone model when unmatched).
  - Removed the aircraft-list fetch, the `missionAircraft` state and its
    baseline, the dirty-guard hook, and the `UnsavedChangesModal` — none
    of the remaining controls have unsaved state (attach/detach are
    immediate writes).
  - Removed the call to `PATCH /api/missions/{id}/flights/{flight_id}/aircraft`.
    The backend endpoint is left in place for `MissionWizardLegacy.tsx`
    (the `/missions/:id/edit-legacy` soak fallback) but the Hub editor
    never fires it.

### Tests

- **`backend/tests/test_mission_flight_attach_derives_aircraft.py`** —
  new. Three contract tests through the FastAPI ASGI stack:
  (1) omitting `aircraft_id` in the body → server fills it from the
  Flight row; (2) sending a stale `aircraft_id` → server overrides it
  with the flight log's value; (3) unmatched flight log → stored as
  `null`, never the client's guess. 3/3 pass.
- **`frontend/src/pages/__tests__/MissionFlightsEdit.test.tsx`** —
  tightened the Add-flight contract assertion: the POST body must NOT
  carry `aircraft_id`, and the page must NEVER fire the legacy PATCH
  `/aircraft` endpoint. Updated handler tracks both invariants. 3/3 pass.

## [unreleased] — 2026-05-14 — fix(reports): clear stale draft on Generate Report click

Small UX tweak on the Mission Report Edit page. Clicking **Generate Report**
now clears the FINAL REPORT editor and resets the audience-leak banner state
**immediately** instead of leaving the previous draft visible until the
poll completes. Operator-reported confusion ("did it hear me?") — the
prior behavior left stale text on screen during the 10–30s generation
window with no visual indication the new request was accepted.

### Changed

- **`frontend/src/pages/MissionReportEdit.tsx:262-272`** — `handleGenerate`
  now calls `setReportContent('')`, `setHasAudienceLeak(false)`, and
  `setAudienceLeakDetails([])` before firing the POST. The conditional
  render on `reportContent` (line ~625) unmounts the FINAL REPORT block
  entirely while empty; the `GENERATING...` button label is the in-flight
  signal. PDF + Send buttons naturally disable during the in-flight
  window because their existing guards already include `!reportContent`.
  Re-entrant clicks on Generate are already a no-op via the existing
  `disabled={generating || !narrative}` — no new pattern introduced.
  Failed/cancelled generation: cleared state stays cleared by design
  (operator can hit Generate again or type manually). Banner re-appears
  on next successful generation when the backend re-flags.

### Tests

- **`frontend/src/pages/__tests__/MissionReportEdit.test.tsx`** — added
  `Generate Report clears the existing draft content immediately` locking
  the new behavior (asserts the mocked RTE testid unmounts after click).
  Reordered the CONTRACT test so Generate runs LAST since clicking it
  now disables PDF + Send via the `!reportContent` guards; load-bearing
  assertion (`POST /missions count = 0`) is unaffected. 8/8 pass.

## [unreleased] — 2026-05-14 — feat(reports): ADR-0015 runtime audience-leak gate (soft block)

Wires `report_audience.detect_audience_leaks` into the LLM report-generation
pipeline as a **soft-block** runtime gate. Generation always completes; the
gate's job is to flag suspect output so the editorial review step catches
it. Personal-instance only; no deploy (`.deployer-disabled` per fleet
convention). Operator review before deploy.

### Added

- **`backend/app/models/report.py`** — two new columns on `Report`:
  - `has_audience_leak BOOLEAN NOT NULL DEFAULT FALSE` — fast filter for
    the editorial-gate banner.
  - `audience_leak_details JSONB NOT NULL DEFAULT '[]'::jsonb` — list of
    `{rule, snippet, start, end}` records, one per matched phrase, so the
    UI can render exactly what tripped.
- **`backend/app/main.py:114-122`** — idempotent ALTER migrations under the
  existing `_add_missing_columns` path (the repo convention; no Alembic in
  this repo). Defaults are `false` / `[]` so legacy rows (pre-runtime-gate)
  and any path that writes a Report without going through generation read
  cleanly. Failover-safe per CLAUDE.md §Failover Guard (additive only, no
  PK/FK/index changes).
- **`backend/app/tasks/celery_tasks.py:150-189`** — new module-level helper
  `_apply_audience_findings(report, llm_content)` runs the detector after
  every LLM generation and persists findings on the row. Wired into both
  branches of the persist site (line 268). The helper **never raises** —
  detector failure logs and leaves flags at their defaults so generation
  never 500s on a regex regression. **No regen loop** — detection +
  surfacing only (decision per operator soft-block directive).
- **`backend/app/schemas/report.py`** — new `AudienceLeakDetail` Pydantic
  model + `ReportResponse.has_audience_leak` + `ReportResponse.audience_leak_details`
  so the API surface carries the findings to the frontend.
- **`frontend/src/api/types.ts`** — `AudienceLeakDetail` interface +
  optional `has_audience_leak` / `audience_leak_details` on `Report`.
- **`frontend/src/pages/MissionReportEdit.tsx`** — yellow `IconAlertTriangle`
  Mantine `Alert` banner above the `FINAL REPORT` editor when
  `has_audience_leak === true`, listing each matched phrase with its rule
  name. Save Draft / Generate PDF / Send remain enabled — the operator's
  editorial review IS the gate. Generation-complete toast switches to a
  yellow "Audience Leak Flagged" variant when findings are non-empty.
- **`backend/tests/services/test_audience_leak_persistence.py`** — 10 new
  hermetic tests covering:
  - Clean LLM output → `has_audience_leak=False`, empty list.
  - Verbatim 2026-05-14 incident-shape input → `has_audience_leak=True`,
    ≥3 distinct rule categories, JSONB shape contract honored.
  - Single-rule smoke check.
  - Empty input / `None` input → clean defaults.
  - **ADR-0015 contract:** detector failure (patched to raise) does NOT
    propagate; report saves with clean defaults.
  - Helper preserves one-to-one match cardinality with the detector
    (no dedup/aggregation drift).
  - `ReportResponse` Pydantic round-trip — both clean and leaky shapes
    serialize through the typed `AudienceLeakDetail` model.
  - Soft-block doc-string lock (CI signal if someone later rewrites the
    helper into a regen-loop pattern).

### Verification

- `pytest tests/services/test_audience_leak_persistence.py -v` —
  **10/10 passing** in 1.89s.
- `pytest tests/services/test_report_audience_guard.py -v` —
  **17/17 passing** (no regression in the existing audience suite).
- `pytest` (full backend suite) — **240 passed, 1 skipped, 2 failed**.
  The 2 failures (`test_health_stripe_db_lookup.py::test_stripe_probe_falls_back_to_env_when_db_empty`
  and `::test_stripe_probe_db_lookup_failure_falls_back_to_env`) are the
  same pre-existing failures called out in the prior audience-fix entry —
  unrelated to this change, out of scope.

### Not done (deliberate)

- **No regen loop.** Operator directive: detection + surfacing only. A
  retry-until-clean loop would burn API credits, mask prompt regressions,
  and is not what was agreed. The doc-string lock test (`test_no_regen_loop_contract_is_documented`)
  trips CI if a future edit drifts toward that pattern.
- **No operator-debrief surface.** Dropped per operator decision; ADR-0015
  follow-up under Terry's ROADMAP edit.

## [unreleased] — 2026-05-14 — docs(reports): operator close-out decisions on mission-report audience leak

Documentation close-out for the audience-leak quality defect, separate
from aegis's code patch (commit `22469ed`, see entry below). Two
operator decisions resolved the open architectural questions aegis
flagged as "out of scope (operator decision)" at the bottom of his
CHANGELOG entry.

### Decided

- **Operator-facing debrief surface — DROPPED, not deferred.** Operator
  confirmed at close-out: "drop it — i don't need a operator debrief —
  never asked for that." DroneOpsCommand produces exactly one
  LLM-generated artifact and it is unambiguously client-facing. The
  earlier "client surface today, operator surface tomorrow" framing in
  the Proposed draft of ADR-0015 was Terry-supplied scaffolding, not
  a product requirement. Rejected at close-out.
- **Runtime audience-leak soft-block gate — APPROVED.** Aegis's
  detector module (`backend/app/services/report_audience.py`, shipped
  at `22469ed` as a stable callable) gets wired as a post-generation
  gate on every LLM-produced draft. Soft-block: on detection, the draft
  is flagged and the offending phrasings surface in the
  `MissionReportEdit` editorial banner; operator can still override and
  ship. Wire-in commit hash TBD (aegis implementing in parallel).

### Docs updated

- `docs/adr/0015-mission-report-audience-separation.md` — flipped from
  Proposed → Accepted with the stronger decision ("operator-facing
  coaching is explicitly out of scope"), added §"Rejected alternative:
  operator-facing debrief surface" with the rationale, and added
  decision #5 for the runtime soft-block gate.
- `docs/incidents/2026-05-14-mission-report-audience-leak.md` — open
  questions §9 reconciled against aegis's findings (Q1 closed at
  `22469ed`, Q2/Q3 marked operator-action, Q4 deferred low-priority),
  plus new §10 "Decisions made post-RCA" capturing both operator
  decisions verbatim.
- `ROADMAP.md` — FU-AI-1 (operator retrospective surface) removed
  entirely (dropped, not deferred). FU-AI-2 (regression fixture)
  marked SHIPPED at `22469ed`. FU-AI-3 (prompt module relocation)
  marked DE-PRIORITIZED (still stands on its own, no longer urgent
  after aegis deliberately scoped it out of the audience fix). FU-AI-4
  (per-tenant tone override) kept — it stands alone on its
  managed-tenant branding rationale, not tied to the dropped surface.
  New FU-AI-RUNTIME-GATE item added covering the soft-block wire-in.
- `PROGRESS.md` — close-out narrative for the incident.

No code changes in this docs commit. Aegis owns the runtime-gate
wire-in code; this entry exists separately because the *decisions*
that authorized that wire-in (and that explicitly rejected the
operator-surface alternative) are the load-bearing post-RCA narrative
and belong in the ledger on their own.

## [unreleased] — 2026-05-14 — fix(reports): mission-report audience leak — aegis patch landed

Code-side close-out of the audience-leak quality defect documented in the
incident narrative below. Personal-instance only; no customer-facing report
ever shipped with the defect. No deploy yet — operator review before deploy
per the close-out-thoroughness workflow. Repo carries `.deployer-disabled`
so SwarmPilot will not auto-pick this up; deploy is manual.

### Fixed

- **`backend/app/services/ollama.py:9-38`** — `SYSTEM_PROMPT_TEMPLATE`
  rewritten. Now explicitly names the CLIENT as the reader, names the
  operator as upstream author (not reader), forbids second-person address
  to the pilot, calls out the four most common leak phrases as
  FORBIDDEN, and reframes Section 5 from "Recommendations - Follow-up
  actions or suggestions for the client" (which the LLM consistently
  read as a pilot-coaching slot) to "Client Follow-Up Items" with an
  explicit ban on pilot/aircraft/flight-technique recommendations and
  an explicit "OMIT this section entirely" fallback when no client
  action is warranted.
- **`backend/app/services/ollama.py:75-89`** and
  **`backend/app/services/claude_llm.py:52-66`** — user-prompt template
  in both providers now labels `Operator Notes:` as `CONTEXT ONLY` with
  an inline instruction to translate them into third-person narrative,
  and the trailing instruction is now "Generate the client-facing
  after-action report" with the audience constraint repeated. Belt-and-
  suspenders against system-prompt drift on long contexts.
- Note: `SYSTEM_PROMPT_TEMPLATE` continues to live in
  `app/services/ollama.py` and is imported by `claude_llm.py` (current
  pattern). The proposal in the incident narrative to relocate to a
  dedicated `llm_prompts.py` module is left for a follow-up PR — it is
  a refactor, not part of the audience-fix surface, and bundling it
  would expand the change footprint without adding behavioral coverage.

### Added

- **`backend/app/services/report_audience.py`** — deterministic regex-
  based audience-leak detector (`detect_audience_leaks`,
  `has_audience_leak`, `AudienceLeak` dataclass). Nine rule categories
  covering second-person operator address, first-person-plural pilot
  advice, coaching framing ("next time consider…"), and operator/pilot
  self-critique. Currently consumed by the regression test; exposed as
  a stable callable so it can be wired into a post-generation runtime
  gate in a follow-up without re-implementing the rules.
- **`backend/tests/services/test_report_audience_guard.py`** — 17-test
  regression suite. Layer 1 (4 tests) locks structural guarantees of
  the system prompt: audience pin, operator-address ban, Section-5
  reframe, operator-notes framing. Layer 2 (13 tests) exercises the
  detector against nine representative bad phrasings, a known-clean
  third-person sample, empty input, the diagnostic snippet shape, and
  the verbatim shape of the operator-reported leak from this incident.
  Fully hermetic — no network, no LLM, no DB. Runs in ~1.8s.

### Verification

- `pytest tests/services/test_report_audience_guard.py -v` — 17/17
  passing on Python 3.12.3.
- Full backend suite: 230 passed, 1 skipped, 2 failed. The 2 failures
  (`test_health_stripe_db_lookup.py`) are pre-existing and reproduce
  against pristine `main` HEAD without these changes — unrelated to
  the audience fix and out of scope.

### Out of scope (flagged for operator decision)

- Whether to keep an operator-facing debrief surface at all (and if
  so, where). Today the system has exactly one report and it is now
  unambiguously client-facing. If pilot coaching is wanted as a
  separate deliverable, it needs a new endpoint + new prompt + UI
  exposure — not a knob on this report.
- Whether to wire `report_audience.has_audience_leak()` as a hard
  runtime gate on `generate_report_task` (regenerate or flag the
  report when a leak is detected). The detector is ready; the policy
  decision (block vs. flag + UI banner) is operator-owned.

See `docs/incidents/2026-05-14-mission-report-audience-leak.md` and
`docs/adr/0015-mission-report-audience-separation.md` (terry-research-
architect) for full RCA, ADR, and prevention frame.

## [unreleased] — 2026-05-14 — fix(reports): mission-report audience leak — pending aegis patch

Quality defect, not an outage. Operator-only catch on the personal instance;
no customer-facing report shipped with the defect. Use the "close-out
thoroughness" workflow per operator preference — full doc + ADR + fixture,
no hotfix shortcut.

- **Symptom.** LLM-generated mission report `final_content` contained
  recommendations addressed to the *operator* (how to fly future missions,
  gear suggestions, technique advice) inside a section that is supposed to
  be customer-facing follow-up. Operator caught it in the
  `MissionReportEdit` editorial gate before send.
- **Scope.** Both LLM providers — Claude (`claude-sonnet-4-20250514`,
  `backend/app/services/claude_llm.py`) and Ollama
  (`llama3.1:8b-instruct-q4_K_M`, `backend/app/services/ollama.py`) share
  the same `SYSTEM_PROMPT_TEMPLATE`, so the defect is provider-agnostic.
  Affects personal instance, demo instance, and any managed-hosting
  tenant on this image. Not a regression — defect has been present since
  the LLM dispatch system was introduced.
- **Root cause (preliminary, pending aegis RCA).** The shared system
  prompt declares the artifact "client-facing" but (a) does not name the
  customer as the reader, (b) does not name the operator as upstream
  author rather than reader, (c) Section 5 "Recommendations" has no
  audience pin, (d) no negative instruction prohibiting operator-coaching
  prose. The LLM resolves the audience toward whoever is closest in the
  conversation context — the operator, via the labeled `Operator Notes:`
  block in the user prompt.
- **Fix (pending aegis patch).** Rewrite the prompt to (1) name audience
  + author explicitly, (2) reframe Section 5 to require customer-targeted
  follow-up actions or be empty, (3) forbid operator-coaching prose, (4)
  relocate `SYSTEM_PROMPT_TEMPLATE` from `ollama.py` to
  `backend/app/services/llm_prompts.py` so both providers import from a
  shared module, (5) pin the contract with a regression fixture
  (`backend/tests/test_llm_report_audience.py`).
- **Verification gate.** Personal-instance soak 24h with a real report
  generated and reviewed, then propagate to managed tenants. No same-day
  fan-out.
- **Below ntfy threshold (ADR-0037).** No customer impact, no actionable
  5-minute window. Dashboard / log only; no publish.

Docs landed alongside this entry:

- `docs/incidents/2026-05-14-mission-report-audience-leak.md` —
  full incident record with AI-backend confirmation, scope, root
  cause analysis, fix plan, prevention.
- `docs/adr/0015-mission-report-audience-separation.md` — durable
  decision: mission reports are client-facing artifacts; operator-facing
  coaching is a separate (future) surface. Proposed → Accepted on the
  same commit that lands the prompt fix.

CHANGELOG line for the patch itself will be appended by aegis as a
separate `[unreleased]` Fixed entry when the prompt rewrite + fixture
land, or rolled into a version bump entry on the next release cut.

## [unreleased] — 2026-05-13 — security: tighten CF Access on droneops.barnardhq.com to bill-only via Entra (personal instance only)

Hostname-scoped Cloudflare Access change. **No repo code, no compose, no
image rebuild** — control-plane API change only.

- **Scope:** `droneops.barnardhq.com` (personal instance) ONLY. The public
  demo at `command-demo.barnardhq.com` is **explicitly UNAFFECTED** — it
  continues to run intentionally open with the `demo/demo123` 24h-reset
  flow. Verified zero Access apps reference the demo hostname.
- **Before:** `DroneOps Admin` app (`d226f352…`) consumed reusable policy
  `NOC Auth Access` (`3aba4b97…`) whose includes were
  `bbarnard065@gmail.com` OR `email_domain: barnardhq.com` — broader than
  the locked single-operator policy. `allowed_idps: []`,
  `auto_redirect_to_identity: false`.
- **After:** App-scoped non-reusable policy `DroneOps Admin - Bill Only`
  (`1511226a…`) with `include: [{email: bill@barnardhq.com}]`. App
  pinned to Entra IdP `429ee672-d15a-4c16-b5d7-814c1d465e4a` with
  `auto_redirect_to_identity: true` for parity with TitanForge.
- **InfraWatch preserved.** The reusable `NOC Auth Access` policy is also
  attached to `Private Services` (`noc.barnardhq.com`). Rather than narrow
  the shared policy and bleed change into NOC, we detached it from
  `d226f352…` and replaced with a dedicated app-scoped policy. Reusable
  policy contents restored to pre-task state; `app_count` now `1`.
- **Customer-facing paths preserved.** Existing bypass apps
  `DroneOps Public (Intake + …)` (`9d27b534…`,
  `droneops.barnardhq.com/intake/*` + assets + api/intake + flight-library
  device-link + health) and `DroneOps Public (Customer Portal + Stripe
  Webhook)` (`e2d36c3f…`, `client/*`, `api/client/*`, `tos/*`,
  `api/webhooks/stripe`) were not touched — `updated_at` unchanged.
  Anonymous `GET /intake/bogus-token` returns HTTP 200 (no SSO redirect).
- **HSH IP-bypass preserved.** Operator workstation
  (`69.9.133.92/32`, `HSH-IP-Bypass`) still bypasses for break-glass.

Per fleet ADR — TitanForge / DroneOps personal-instance lockdown.

## [2.67.6] — 2026-05-11 — security: patch CVE-2026-7482 (Ollama "Bleeding Llama") + jinja2 + python-jose; pin all `:latest` images

Security release. Three independent CVE clusters resolved in one bundle,
plus supply-chain hygiene on previously-unpinned images. No functional
changes; no schema changes; no runtime behavior changes other than the
upgraded library versions.

### Security fixes

- **Ollama → `0.23.2` (was `:latest`).** CVE-2026-7482 "Bleeding Llama"
  (CVSS 9.1) — unauthenticated memory disclosure via crafted GGUF model
  upload allows extraction of in-flight prompts, system instructions,
  API keys, and process env vars in three API calls. Disclosed by Cyera
  early May 2026; ~300k public Ollama servers affected. Fixed upstream
  in 0.17.1; we pin to current stable `0.23.2` (released 2026-05-07).
  Container is internal-only (no host port exposed; only `backend` and
  `worker` reach it via the compose network), so blast radius is limited
  to a backend RCE chain — but the upgrade closes the hole regardless.
  Companion Windows-auto-updater CVEs (2026-42248/42249) do not apply
  to Docker deployments.
- **python-jose → `3.4.0` (was `3.3.0`).** Two CVEs:
  - CVE-2024-33663 — algorithm confusion when an OpenSSH ECDSA / other
    asymmetric key is used to verify a JWT signed with HS256; 3.4.0
    forbids signing JWTs with public keys.
  - CVE-2024-33664 — "JWT bomb" DoS via a JWE token with a high
    compression ratio; 3.4.0 caps JWE input at 250 KiB.
  Used by `app/auth/*` (access + refresh token verification). Pre-fix
  the JWE-bomb path was reachable by any unauthenticated client posting
  to `/api/auth/refresh` with a crafted token.
- **jinja2 → `3.1.6` (was `3.1.4`).** Sandbox-escape trio:
  - CVE-2024-56201 (CVSS 8.8) — attacker-controlled template content +
    filename yields arbitrary Python execution, sandboxed or not.
  - CVE-2024-56326 — `str.format` sandbox bypass.
  - CVE-2025-27516 — `|attr` filter sandbox bypass.
  Used by WeasyPrint PDF rendering (`app/services/pdf_service.py`) and
  email template rendering (`app/services/email_service.py`). Templates
  are repo-controlled today, but the sandbox-escape vectors meaningfully
  raise blast radius if any future feature ever takes user input into a
  template path or content (e.g. customer-branded report templates).

### Supply-chain hygiene — unpinned `:latest` images pinned

These had no known active CVEs but `:latest` is non-reproducible and
silently rolls a new image in on every `docker compose pull`, giving
the upstream maintainer an implicit RCE channel onto every fleet host.
ADR-0027 / fleet-wide convention is explicit pins.

- `containrrr/watchtower:latest` → `1.7.1` (last stable; project is
  semi-dormant — `:latest` and `:1.7.1` resolve to the same digest
  today, but the pin protects against a future surprise).
- `cloudflare/cloudflared:latest` → `2026.3.0` (released 2026-03-09).
- `curlimages/curl:latest` → `8.20.0` (~2 weeks old at release).

### Changed

- `docker-compose.yml` line 64 — `ollama/ollama:latest` →
  `ollama/ollama:0.23.2` + 5-line CVE comment.
- `docker-compose.yml` line 92 — `curlimages/curl:latest` → `8.20.0`.
- `docker-compose.yml` line 114 — `containrrr/watchtower:latest` → `1.7.1`.
- `docker-compose.yml` line 374 — `cloudflare/cloudflared:latest` → `2026.3.0`.
- `backend/requirements.txt` — `python-jose[cryptography]==3.3.0` →
  `==3.4.0`; `jinja2==3.1.4` → `==3.1.6`. Both pins gain inline CVE
  references so the next maintainer doesn't roll them back.
- `docker-compose.demo.yml` — no edits required. The override disables
  `ollama` / `watchtower` via `replicas: 0` and inherits the (now-pinned)
  image from the base compose. cloudflared in the demo stack also
  inherits the pinned base image.

### Version bump (per CLAUDE.md "Version Bumping" + fleet rule)

- `frontend/package.json`: 2.67.5 → 2.67.6
- `backend/app/main.py`: FastAPI `version=` 2.67.5 → 2.67.6
- `README.md`: `Version 2.67.5` → `Version 2.67.6`
- `frontend/src/components/Layout/AppShell.tsx`: navbar footer `v2.67.5` → `v2.67.6` (×2)

### Operator notes

- **Deploy is not a no-op.** `docker compose pull && docker compose up -d`
  on the prod host will pull `ollama:0.23.2` (~1.5 GB image) and rebuild
  the backend / worker / beat containers (requirements.txt change). The
  `ollama_data` volume is preserved, so the cached Llama 3.1 8B model
  does not re-download (~5 GB). Expect ~60-120s of API downtime during
  the backend rebuild.
- **NTFY:** no separate alert needed; this rides the standard NOC
  deploy-watcher post-deploy notification on the `noc-deploys` topic.
- **CHAD-HQ demo:** auto-pulls within 30s. Demo stack does not run
  Ollama, so the only impact is the backend/worker rebuild for the
  jinja2 + python-jose pickup. Same ~60s blip on `command-demo.barnardhq.com`.
- **Pre-existing stale `APP_VERSION:-2.67.3` defaults in compose** (lines
  176, 267, 304, 352 — see 2.67.5 operator-notes) are STILL untouched in
  this release; live `.env` overrides them. Fleet-wide cleanup remains
  open.

## [2.67.5] — 2026-05-09 — chore(obs-migration): repoint OTLP defaults to alloy.barnardhq.com per ADR-0050

PR draft, awaiting cutover-window merge at 2026-05-09 00:00 PDT. The
application-observability stack relocates from HSH-HQ to BOS-HQ per
[noc-master ADR-0050](https://github.com/BigBill1418/NOC-Master-Control-SWARM/blob/main/docs/adr/0050-obs-stack-bos-migration-v2.md).
DroneOpsCommand flips its OTLP defaults to the new stable hostname
(`alloy.barnardhq.com:4317`, resolves to 10.99.0.4 / BOS-HQ over WG)
so the next host-shift is a DNS edit rather than a code edit. Both
prod and demo overlays now point at the same central Alloy.

### Changed

- `backend/app/observability/otel.py` — `_DEFAULT_ENDPOINT`
  `http://10.99.0.1:4317` → `http://alloy.barnardhq.com:4317`;
  module docstring updated to describe post-cutover topology.
- `docker-compose.demo.yml` line 46 — `OTEL_EXPORTER_OTLP_ENDPOINT`
  default `http://10.99.0.2:4317` → `http://alloy.barnardhq.com:4317`;
  comment refresh.
- `.env.example` lines 92-99 — comment block describing prod + demo
  default endpoints rewritten to reflect post-migration stable-DNS.

### Version bump (per CLAUDE.md "Version Bumping" + fleet rule)

- `frontend/package.json`: 2.67.4 → 2.67.5
- `backend/app/main.py`: FastAPI `version=` 2.67.4 → 2.67.5
- `README.md`: `Version 2.67.4` → `Version 2.67.5`
- `frontend/src/components/Layout/AppShell.tsx`: navbar footer `v2.67.4` → `v2.67.5` (×2)

### Operator notes

- This PR is **draft** and **must not be merged before the
  2026-05-09 00:00 PDT cutover window**. NOC deployer auto-pulls
  within 30s; merging before BOS Alloy is up will degrade trace
  capture until the cutover completes (Sentry path independent;
  errors still flow to GlitchTip).
- Branch name (`obs-migration-bos-v2`) deliberately avoids the
  `claude/*` auto-merge path per noc-master/CLAUDE.md.
- The pre-existing stale `APP_VERSION:-2.67.3` defaults in
  `docker-compose.yml` (lines 176, 267, 304, 352) and
  `docker-compose.demo.yml` (line 86) are left untouched in this PR
  (separate fleet-wide cleanup needed; live `.env` overrides them).

## [2.67.4] — 2026-05-04 — fix: ops hygiene cluster (Tier 2 A5/A6/A7/A8)

Four small Tier-2 items shipped as one consolidated release. Operator
explicitly held A2 (host-side superuser password rotation) and A4
(Guardian Graph mailer env vars on CHAD-HQ) for another day.

**A5 — `droneops-deposits` ntfy topic registered in NOC fallback.**
`~/noc-master/data/ntfy-fallback-topics.yml` gains an explicit dedicated
fallback `barnardhq-fleet-droneops-deposits-7d14048450682062899dddfad10bd1fa`
for the publisher-side helper to fall through to if `ntfy.barnardhq.com`
is unreachable. The `droneops` prefix in `service-registry.json`
already covered `droneops-deposits` server-side ACL — this just gives
it its own fallback row for traceability.

**A6 — stale `APP_VERSION=2.63.5` in compose replaced with env-overridable defaults.**
`docker-compose.yml` (3 backend places + 1 frontend build arg) and
`docker-compose.demo.yml` (1 frontend build arg) all now read
`${APP_VERSION:-2.67.3}` (default tracks the at-time-of-edit current
release). Operator's BOS-HQ `~/droneops/.env` `APP_VERSION` value
also bumped to `2.67.4`. The proper long-term fix (NOC deployer
auto-bumping `APP_VERSION` env from `backend/app/main.py`'s FastAPI
`version=` on each deploy) is queued as Tier 3.

**A7 — `/api/health` Stripe probe now reads from `system_settings` first, env as fallback.**
`backend/app/main.py:_probe_stripe_cached` previously checked only
`settings.stripe_secret_key` (env-only). Since the Stripe key is
actually stored in the `system_settings` DB table (set live via the
Settings UI / direct `INSERT`), the probe was reporting
`"stripe": "unconfigured"` even on instances with a working live
Stripe integration — actively misleading anyone debugging. The probe
now: 1. tries `get_stripe_settings(db)` first (canonical DB source);
2. falls back to env if the DB row is empty; 3. falls back to env if
the DB lookup itself raises (defensive). 4 hermetic tests in
`test_health_stripe_db_lookup.py` pin the DB-first contract +
env-fallback + DB-failure-fallback + neither-configured paths.

**A8 — frontend Sentry release tag now propagates correctly.**
`frontend/src/lib/sentry.ts:33` already read `VITE_APP_VERSION` at
init and used it as Sentry's `release` field — but the value was
hardcoded `"2.63.5"` in `docker-compose.yml:352` build args, so every
Sentry event since 2026-04 was tagged `release=droneops@2.63.5`.
Fixed by A6's compose change. Going forward, Sentry events from the
SPA correctly tag the deployed semver (`droneops@2.67.4` after this
ship) so error grouping + regression detection in GlitchTip work
properly.

## [2.67.3] — 2026-05-04 — feat: unsaved-changes guard on facet editors + Stripe pay-link in PDF + demo refresh (Tier 2 partial)

Three Tier-2 items shipped together (the rest deferred per operator):
1. Unsaved-changes guard across all 5 Mission Hub facet editors (B1)
2. Stripe pay-link in emailed PDF invoice (B2)
3. Demo instance refresh from v2.63.3 → v2.67.2 (12-release jump). NOC config corrected: `services[DroneOps Demo].repo` was pointing at the non-existent `BigBill1418/DroneOpsDemo` repo; corrected to `DroneOpsCommand` (the demo's local clone has always tracked that repo). 8 demo missions + 4 demo customers preserved; schema migrations (deposit columns + tos_acceptances) applied; demo serving 200.

### B1 — facet editors unsaved-changes guard

Adds a confirm-before-discard prompt to every facet editor so an operator who has typed into a field and then clicks Cancel / the back arrow / closes the tab gets a confirmation step instead of silently losing the edit.

**New shared pieces:**
- `frontend/src/hooks/useDirtyGuard.ts` — drop-in hook taking `{ isDirty, navigate }`. Returns `{ showConfirm, setShowConfirm, guardedNavigate, confirmAndNavigate }`. Editors call `guardedNavigate(target)` instead of `navigate(target)` directly; the hook stashes the target and surfaces `showConfirm=true` when dirty. Also registers a `beforeunload` listener while dirty so tab close / hard refresh / browser back surfaces the native "Leave site?" prompt.
- `frontend/src/components/shared/UnsavedChangesModal.tsx` — operator-brand Mantine modal: cyan KEEP-EDITING (default, autoFocus) + red DISCARD-CHANGES. Editors can override body copy.

**Per-editor wiring:**
- **MissionDetailsEdit** — Mantine `useForm` covers 7 fields; the 3 UNAS fields are plain useState tracked via baseline snapshot. `form.resetDirty(loaded)` after initial setValues so a freshly loaded form isn't false-positive dirty. Save handler re-baselines via `form.resetDirty()` + UNAS-snapshot reset.
- **MissionReportEdit** — plain useState for narrative + reportContent + includeDownloadLink; baseline snapshot taken on load + re-baselined after Save Draft, AI generate (sync + async-poll paths), and Generate PDF.
- **MissionInvoiceEdit** — 6 operator-editable fields (lineItems array + 5 scalars) tracked via JSON-serialized snapshot with explicit key order; both back paths (top arrow + bottom Cancel) routed through `guardedNavigate`. depositPaid excluded from the snapshot (server-driven).
- **MissionFlightsEdit** — Add/Remove/Assign-aircraft persist immediately so they're never "unsaved"; only the AIRCRAFT-USED checkbox group lives purely client-side. Tracked via order-insensitive set diff against the baseline.
- **MissionImagesEdit** — uploads + deletes persist immediately; the dirty signal is the dropzone's `uploading` flag plus any row in 'uploading' status. Custom modal copy explains pending uploads will be aborted on discard.

**Architectural note** — react-router-dom@6.28 with `BrowserRouter` (not the data router) does not expose `useBlocker`. So clicking a sidebar nav-link while a facet editor is dirty bypasses Layer 1; Layer 2's `beforeunload` doesn't fire on intra-SPA navigation either. Covered surface: editor's own Cancel/back buttons + tab close + hard refresh + external link. Documented inline in `useDirtyGuard.ts` header.

**Tests** — 49 passing (was 34, +15 new):
- `useDirtyGuard.test.ts` (6 tests) — hook unit tests covering navigate-immediately when clean, modal-stash-and-suppress when dirty, confirmAndNavigate flow, KEEP-EDITING flow, beforeunload listener add/remove on dirty toggle + unmount, and beforeunload handler preventDefault + returnValue.
- `MissionDetailsEdit.test.tsx` (4 new tests) — Cancel after edit shows modal / Keep Editing closes without nav / Discard navigates without write / Cancel after Save does NOT prompt (dirty cleared).
- `MissionInvoiceEdit.test.tsx` (5 new tests, file is new) — clean Cancel doesn't prompt / dirty Cancel shows modal / Keep Editing closes / Discard navigates without write / Cancel after Save doesn't prompt. Includes the load-bearing CONTRACT TRIPWIRE that POST `/api/missions` is never fired from this page.

No backend changes. No ADR (UX polish, not architecture).

### B2 — Stripe pay-link in emailed PDF

The emailed PDF invoice has carried PayPal + Venmo links since v2.65.0 but no Stripe equivalent. Customers who wanted to pay by card / Apple Pay / ACH had to dig back through the original portal email to find their magic link. This adds a "Pay online (credit/debit/ACH)" row at the top of the PAYMENT OPTIONS block that drops the customer onto their existing client portal page (`${frontend_url}/client/<jwt>`), where the Pay Deposit / Pay Balance buttons (Stripe Checkout, ADR-0009) take it from there.

The URL is minted via a new helper `get_or_mint_active_client_link(db, mission_id, days=30)` extracted from the two existing `/api/missions/{id}/client-link` endpoints. Idempotency contract (per ADR-0011 spirit, applied to portal tokens): if a non-revoked, non-expired ClientAccessToken row already covers this (customer, mission), do NOT insert a duplicate row — re-mint a JWT whose `exp` matches the existing row's `expires_at` and update the row's `token_hash` to point at the new JWT. Three PDF renders in a row produce three valid magic-link URLs, all bound to the same registry row, all with the same expiry window.

URL is omitted (None) when `mission.is_billable` is False, no Invoice exists, `paid_in_full` is True, `total` is 0, mission has no customer (fail-soft, log + skip), or the helper raises (fail-soft, log + skip; PDF still renders). Brand color is the customer-facing TOS PDF cyan `#189cc6`, NOT the operator dark-theme `#00d4ff`. The legacy PayPal/Venmo block is preserved unchanged below the Stripe row.

Failover guard: pure additive logic in the router + a single helper call. No PG schema changes, no replication impact, no swap-flow effect, no failover-engine interaction.

Files: `backend/app/routers/client_portal.py` (helper extraction + idempotent operator endpoints), `backend/app/routers/reports.py` (mint URL into PDF context), `backend/app/services/pdf_generator.py` (`stripe_pay_url` kwarg), `backend/app/templates/report_pdf.html` (render row above PayPal/Venmo). 17 hermetic tests in `backend/tests/test_pdf_invoice_pay_link.py` covering helper idempotency, template render edge cases, and route-layer context threading. No new ADR required.

## [2.67.2] — 2026-05-04 — fix(spa): graceful handling of stale-bundle errors after deploy

After v2.67.1 deployed, operator's already-loaded browser tab (still holding the v2.67.0 `index.html` in memory) tried to dynamic-import `Settings-FvnyORN8.js` — a chunk hash that no longer existed on the new build. Vite emits new content-hashed filenames every build; the old hash 404s. The pre-existing `ErrorBoundary` showed the generic "Something went wrong" message, leaving the operator confused about what happened or how to recover.

`index.html` cache headers ARE correct (`no-cache, no-store, must-revalidate`) — the issue is long-lived browser tabs that hold the old HTML in memory and never re-fetch it on navigation, only on a full reload.

**Fix:** `frontend/src/components/ErrorBoundary.tsx` now:
1. Detects stale-bundle errors via 4 regex patterns (Chrome / Vite / Safari / Firefox phrasing for "dynamic import failed"
