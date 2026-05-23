> **Maintained automatically by NOC doc-autogen.** This file is refreshed twice daily (04:00 + 16:00 UTC) by `~/noc-master/scripts/doc-autogen.py`, which summarizes recent commits via Claude Haiku 4.5 and commits with a `[skip-deploy]` trailer so no container rebuilds are triggered. See [NOC-Master ADR-0013](https://github.com/BigBill1418/NOC-Master-Control-SWARM/blob/main/docs/decisions/ADR-0013-docs-only-deploy-skip.md). Manual edits are preserved — the generator diffs against existing content before writing.

# Changelog

Notable changes to DroneOpsCommand. Dates are absolute (YYYY-MM-DD, UTC).

## [unreleased] — 2026-05-23 — feat(invoices): "Require 50% deposit" now always tracks 50% of the live total

**Incident:** Operator reported the 50% deposit "not adding up" — an
invoice totalling $844.30 (mileage + rapid deployment + night ops +
hourly) showed a $272.15 deposit, which is 50% of only the first three
items ($544.30). The last-added line item wasn't reflected in the deposit.

**Root cause:** "auto-fill 50%" was a one-shot calculation. On first save
the backend computed 50% of the then-current total and stored it as a
plain number. On reload the app couldn't distinguish that value from an
operator-typed override, so it froze. `_recalculate_invoice` only ever
clamped the deposit *down*, never re-derived it, so adding line items grew
the total but left the deposit stale.

**Decision (operator, 2026-05-23):** "Require 50% deposit" means the
deposit is *always* exactly 50% of the current total — no manual override.

### Changed

- **`backend/app/routers/invoices.py`** — `_recalculate_invoice` is now the
  single authoritative source for the deposit: when required and not yet
  paid, `deposit_amount = 50% of total`, re-derived on every recalc (which
  runs after every line-item add/edit/delete and every PUT) so it tracks
  changes automatically. `deposit_required=False` → 0; total=0 → deferred;
  a *paid* deposit is never recomputed (locked at the collected amount).
  `update_invoice` no longer accepts a manual deposit amount (ignored if
  sent).
- **`frontend/.../MissionInvoiceEdit.tsx`** — the editable "Deposit Amount"
  field is replaced by a read-only live display of 50% of the total that
  updates as line items change; saves send `deposit_amount: null` (auto).
- **`backend/tests/test_deposit_pricing.py`** — tests for: deposit tracks
  added items ($844.30 → $422.15), always 50% regardless of prior value,
  zeroed when not required, untouched when paid.

## [unreleased] — 2026-05-23 — fix(invoices): defer deposit when total hits 0 on recalc — stop line items being silently dropped on edit

**Incident (follow-on to the create-time fix below):** Operator reported
an invoice's line items "not adding up." The frontend save replaces line
items by deleting all then re-adding (`MissionInvoiceEdit` /
`MissionWizardLegacy`). Deleting the **last** line item drops the total
to 0; `_recalculate_invoice` clamped `deposit_amount` to 0 but left
`deposit_required=True` — the same impossible state as the create bug —
so the DELETE raised a 500 (`deposit_required_consistent`), aborted the
save, and the re-add loop never ran. Net effect: existing line items were
deleted but the operator's edited set was never written, leaving one
stray item and a wrong total.

### Fixed

- **`backend/app/routers/invoices.py`** — `_recalculate_invoice` now
  defers the deposit (`deposit_required=False, deposit_amount=0`) when the
  recalculated total is ≤ 0, instead of clamping to a constraint-violating
  `deposit_required=True, deposit_amount=0`. Same contract as
  `_create_time_deposit_state`; the deposit is restored on the next PUT
  once line items push total > 0. Removes the 500 on the delete-then-
  recreate save path.
- **`backend/tests/test_deposit_pricing.py`** — regression tests: recalc
  defers deposit at total=0, and still clamps (not defers) for a lowered
  positive total.

**Note:** the underlying delete-then-recreate item replacement is
non-transactional — a mid-sequence failure (like this one) deletes items
without re-adding them. The 500 is fixed, but making the replacement
atomic is tracked as a follow-up hardening item.

## [unreleased] — 2026-05-23 — fix(invoices): defer deposit at create — stop "failing to save invoice" 500

**Incident:** Operator reported "failing to save invoice." Live BOS-HQ
backend was raising a raw 500 on `POST /api/missions/{id}/invoice` with
`CheckViolationError: deposit_required_consistent`.

**Root cause:** A new invoice has no line items, so `total` is always 0
at create time. At total=0 the two DB CHECK constraints make a required
deposit structurally impossible — `deposit_required_consistent` needs
`deposit_amount > 0` while `deposit_amount_le_total` needs
`deposit_amount <= total` (i.e. ≤ 0). `create_invoice` nonetheless
persisted `deposit_required=True` with a 50%-of-0 = 0 deposit (the
default `InvoiceCreate.deposit_required=True` + auto-fill amount path),
so Postgres rejected the INSERT. The frontend *intends* to set the
deposit in a follow-up PUT once line items exist, but the create
crashed first. Latent since ADR-0009; tripped now by a new invoice
created with the default deposit-on + auto-amount.

### Fixed

- **`backend/app/routers/invoices.py`** — `create_invoice` now defers
  the deposit unconditionally at create (new `_create_time_deposit_state`
  helper → `deposit_required=False, deposit_amount=0`), the only
  constraint-safe state at total=0. Authoritative server-side guard for
  all clients. The deposit is applied by the first `PUT /invoice` once
  line items push total > 0.
- **`frontend/src/pages/MissionInvoiceEdit.tsx`** + **`MissionWizardLegacy.tsx`**
  — the post-line-items deposit re-PUT now fires for *explicit* deposit
  amounts too, not only the auto-fill (`null`) case. Without this, the
  backend deferral would silently drop a typed deposit.
- **`backend/tests/test_deposit_pricing.py`** — regression test pinning
  that the create-time deposit state satisfies both CHECK constraints at
  total=0.

### Known issues (pre-existing, unrelated)

- `backend/tests/test_health_stripe_db_lookup.py` — 2 Stripe health-probe
  env-fallback tests fail on a clean checkout (not introduced by this
  change). Tracked separately.

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
  (`test_health_stripe_db_lookup.py::test_stripe_probe_falls_back_to_env_when_db_empty`
  and `::test_stripe_probe_db_lookup_failure_falls_back_to_env`) are
  pre-existing and unrelated to this change.
