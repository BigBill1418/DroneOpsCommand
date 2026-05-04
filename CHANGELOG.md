> **Maintained automatically by NOC doc-autogen.** This file is refreshed twice daily (04:00 + 16:00 UTC) by `~/noc-master/scripts/doc-autogen.py`, which summarizes recent commits via Claude Haiku 4.5 and commits with a `[skip-deploy]` trailer so no container rebuilds are triggered. See [NOC-Master ADR-0013](https://github.com/BigBill1418/NOC-Master-Control-SWARM/blob/main/docs/decisions/ADR-0013-docs-only-deploy-skip.md). Manual edits are preserved — the generator diffs against existing content before writing.

# Changelog

Notable changes to DroneOpsCommand. Dates are absolute (YYYY-MM-DD, UTC).

## [Unreleased] — feat(pdf): Stripe pay-link in emailed invoice PDF

The emailed PDF invoice has carried PayPal + Venmo links since v2.65.0 but no Stripe equivalent. Customers who wanted to pay by card / Apple Pay / ACH had to dig back through the original portal email to find their magic link. This adds a "Pay online (credit/debit/ACH)" row at the top of the PAYMENT OPTIONS block that drops the customer onto their existing client portal page (`${frontend_url}/client/<jwt>`), where the Pay Deposit / Pay Balance buttons (Stripe Checkout, ADR-0009) take it from there.

The URL is minted via a new helper `get_or_mint_active_client_link(db, mission_id, days=30)` extracted from the two existing `/api/missions/{id}/client-link` endpoints. Idempotency contract (per ADR-0011 spirit, applied to portal tokens): if a non-revoked, non-expired ClientAccessToken row already covers this (customer, mission), do NOT insert a duplicate row — re-mint a JWT whose `exp` matches the existing row's `expires_at` and update the row's `token_hash` to point at the new JWT. Three PDF renders in a row produce three valid magic-link URLs, all bound to the same registry row, all with the same expiry window.

URL is omitted (None) when `mission.is_billable` is False, no Invoice exists, `paid_in_full` is True, `total` is 0, mission has no customer (fail-soft, log + skip), or the helper raises (fail-soft, log + skip; PDF still renders). Brand color is the customer-facing TOS PDF cyan `#189cc6`, NOT the operator dark-theme `#00d4ff`. The legacy PayPal/Venmo block is preserved unchanged below the Stripe row.

Failover guard: pure additive logic in the router + a single helper call. No PG schema changes, no replication impact, no swap-flow effect, no failover-engine interaction.

Files: `backend/app/routers/client_portal.py` (helper extraction + idempotent operator endpoints), `backend/app/routers/reports.py` (mint URL into PDF context), `backend/app/services/pdf_generator.py` (`stripe_pay_url` kwarg), `backend/app/templates/report_pdf.html` (render row above PayPal/Venmo). 17 hermetic tests in `backend/tests/test_pdf_invoice_pay_link.py` covering helper idempotency, template render edge cases, and route-layer context threading. Version bump deferred to consolidated v2.67.3 release; no new ADR required.

## [2.67.2] — 2026-05-04 — fix(spa): graceful handling of stale-bundle errors after deploy

After v2.67.1 deployed, operator's already-loaded browser tab (still holding the v2.67.0 `index.html` in memory) tried to dynamic-import `Settings-FvnyORN8.js` — a chunk hash that no longer existed on the new build. Vite emits new content-hashed filenames every build; the old hash 404s. The pre-existing `ErrorBoundary` showed the generic "Something went wrong" message, leaving the operator confused about what happened or how to recover.

`index.html` cache headers ARE correct (`no-cache, no-store, must-revalidate`) — the issue is long-lived browser tabs that hold the old HTML in memory and never re-fetch it on navigation, only on a full reload.

**Fix:** `frontend/src/components/ErrorBoundary.tsx` now:
1. Detects stale-bundle errors via 4 regex patterns (Chrome / Vite / Safari / Firefox phrasing for "dynamic import failed" / "loading chunk failed" / "module script failed").
2. Listens to `window.unhandledrejection` and `window.error` so async dynamic-import rejections that bypass React's boundary are still caught.
3. **Auto-reloads** with a 250ms delay on first detection — operator tab silently picks up the new version.
4. Uses `sessionStorage` flag with 60s window to **prevent infinite reload loops**: if a stale-bundle error fires again within 60s of the first auto-reload, falls through to the manual fallback UI (so a genuinely-broken deploy doesn't ping-pong).
5. Stale-bundle fallback UI shows a different message than generic errors: "NEW VERSION AVAILABLE — A new version of D.O.C was deployed. Reload to pick it up — your work isn't lost." with a primary "RELOAD NOW" button + keyboard hint "or press Cmd/Ctrl + Shift + R".
6. Hard reload uses `window.location.href = '/?_=${Date.now()}'` to bust any intermediary HTTP cache that doesn't honor the no-cache headers.

Generic-error path unchanged — only stale-bundle errors get the new treatment.

## [2.67.1] — 2026-05-04 — fix(missions): legacy missionstatus enum mixed-case + Hub auto-refresh + Refresh button

Closes Tier 1 punch list from v2.67.0 ship report.

**Backend — `missionstatus` enum mixed-case fix.** Legacy PG `missionstatus` enum has uppercase labels for `DRAFT`/`COMPLETED`/`SENT` and lowercase for the other 5 values. SA was writing the Python enum NAME (uppercase), so PATCH to `in_progress`/`scheduled`/`processing`/`review`/`delivered` would 500 with `invalid input value for enum missionstatus: "IN_PROGRESS"`.

Two-part fix:
1. **Data migration on prod (idempotent):** `UPDATE missions SET status = lower(status::text)::missionstatus WHERE status::text IN ('DRAFT','COMPLETED','SENT')` — normalized 4 mission rows. Cast works because both cases are valid PG enum labels. Dry-run inside a rolled-back transaction first.
2. **Code change:** `Mission.status` mapping gains `Enum(MissionStatus, values_callable=lambda enum: [e.value for e in enum])` so SA writes the lowercase VALUE instead of the uppercase NAME.

**Backend — new contract test.** `test_missions_patch_status_all_values.py` parametrized over all 8 `MissionStatus` values; every PATCH must return 200 and round-trip the requested value. Plus 422 for invalid status strings AND for uppercase-NAME-as-value (fail-fast).

**Frontend — Hub auto-refresh + manual Refresh button.** v2.67.0 left the Hub stale after deposit payment. v2.67.1 adds:
- Auto-poll every 30s while Hub is rendered AND tab visible AND status `<` SENT. Stops on SENT. Pauses when tab hidden.
- Manual `IconRefresh` button in Hub header for force-refresh.

**Safety:** zero schema changes; existing mission data preserved (4 rows pre = 4 rows post, IDs identical); legacy `MissionWizardLegacy.tsx` still mounts at `/missions/:id/edit-legacy`.

## [2.67.0] — 2026-05-03 — feat: Mission Hub redesign (ADR-0014)

Replaces the linear `MissionNew/Edit` 5-step wizard with a **Mission
Hub + Facet pattern**. Triggered by the duplicate-mission incident on
2026-05-03 18:46/18:49 UTC where editing an existing mission silently
created a new one because the shared `MissionNew.tsx` component
fell through its `isEditing && missionId` guard to `POST /missions`.

The new shape: `/missions/:id` is a read-only Hub with five facet
cards (Details, Flights, Images, Report, Invoice). Each `[Edit]`
button routes to a focused per-facet editor (its own URL, its own
component). **No facet editor shares the `POST /api/missions` code
path**; that route is reserved exclusively for the slim
`MissionCreateModal` mounted on the Missions list and Dashboard.
The duplicate-mission bug class is now physically impossible.

Spec: `docs/superpowers/specs/2026-05-03-mission-hub-redesign-design.md`.
Plan: `docs/superpowers/plans/2026-05-03-v2.67.0-mission-hub-orchestration-plan.md`.
ADR: `docs/adr/0014-mission-hub-redesign.md` — references ADRs
0008-0013 in its Consequences section so the historical
deposit/TOS/idempotency/secret-hygiene/contract-test context is
preserved in one place.

**Constraint compliance:** zero schema migrations, zero data backfill,
every existing mission row remains readable + editable through the new
flow without conversion. The legacy 1484-LOC wizard is renamed to
`MissionWizardLegacy.tsx` and mounted at the hidden
`/missions/:id/edit-legacy` route as a soak-window fallback.

### Agent A — Hub + slim create + status transitions (`feat/mission-hub`)

**Backend (`backend/app/routers/missions.py`):**
- `POST /api/missions` rejects bodies that include an `id` field with
  HTTP 400 (defensive guard from spec §4 — makes the
  duplicate-mission class physically impossible at the API boundary).
- `POST /api/missions` logs `[MISSION-POST-DUP]` WARNING when the same
  `(customer_id, title, mission_date)` triple was POSTed in the last
  5 minutes (operator override allowed — log only, no reject).
- `PATCH /api/missions/{id}` (NEW) accepts `{status: <enum>}` for
  Mission Hub Mark COMPLETED / Mark SENT / Reopen Mission buttons.
  Logs `[MISSION-STATUS] from=X to=Y mission_id=Z user=U` on every
  transition. Per spec §8.5 lockdown, SENT → anything-other-than-COMPLETED
  is rejected (400) unless caller passes `?reopen=true`, in which case
  the call additionally emits `[MISSION-REOPEN]` WARNING with
  previous_status + operator id for the audit trail.

**Frontend:**
- `frontend/src/components/MissionStatusBadge.tsx` — shared status pill
  component, lock icon when status is SENT.
- `frontend/src/components/MissionFacetCard.tsx` — shared "card with
  title + summary + Edit button (+ optional `extraActions`)" used on
  the Hub.
- `frontend/src/components/MissionCreateModal.tsx` — slim create modal
  (title + customer + type + optional date). POSTs without `id` and
  navigates to `/missions/{id}` (the Hub) on success.
- `frontend/src/pages/MissionDetail.tsx` — heavily refactored to be
  the Hub: header row with status badge + lifecycle controls, 5 facet
  cards, all Edit buttons disabled with "Mission sent — locked"
  tooltip when status is SENT, Reopen button visible in SENT state.
  Invoice card surfaces Issue Portal Link + Send Email + Copy Link
  actions (spec §8.6) as `extraActions` so the deposit-billing-to-
  client workflow is one-click from the Hub.
- `frontend/src/pages/Missions.tsx` — the "+ NEW MISSION" button now
  opens `MissionCreateModal` inline instead of routing to the legacy
  wizard. List rows still navigate to `/missions/{id}`.

**Tests:** 16 backend tests across
`test_missions_post_rejects_id_in_body.py` (5),
`test_missions_post_logs_dup_warning.py` (2),
`test_missions_patch_status.py` (7),
`test_mission_reopen_logs_audit.py` (2). 9 frontend tests across
`MissionCreateModal.test.tsx` (2, msw — proves POST body NEVER
includes `id`) and `MissionDetail.hub.test.tsx` (7, msw — facet
cards render, lockdown semantics per §8.5, Invoice card
extraActions per §8.6).

**Test infra:** added Vitest + @testing-library/react + msw + jsdom
devDependencies to `frontend/package.json`. The repo had no frontend
test runner configured before; `npm test` / `pnpm test` runs the
full Mission Hub suite.

### Agent B — Details + Flights + Images facet editors (`feat/mission-facets-1`)

- `frontend/src/pages/MissionDetailsEdit.tsx` — focused editor for
  title, customer, type, date, location (with Nominatim search),
  description, billable flag, and UNAS download fields. Mounted at
  `/missions/:id/details/edit`. PUT-only against the existing
  `/api/missions/{id}` endpoint; the file contains no `POST
  /api/missions` code path.
- `frontend/src/pages/MissionFlightsEdit.tsx` — focused editor for
  attaching/detaching flights and assigning aircraft. Mounted at
  `/missions/:id/flights/edit`. POST/DELETE on
  `/api/missions/{id}/flights`; PATCH on
  `/api/missions/{id}/flights/{flight_id}/aircraft`. Zero `POST
  /api/missions`.
- `frontend/src/pages/MissionImagesEdit.tsx` — focused editor for
  image upload + removal. Mounted at `/missions/:id/images/edit`.
  POST/DELETE on `/api/missions/{id}/images`. Zero `POST /api/missions`.

**Tests (per ADR-0013 — every test ships the load-bearing
`POST /api/missions = 0` tripwire):** 9 tests across
`MissionDetailsEdit.test.tsx` (3), `MissionFlightsEdit.test.tsx` (3),
`MissionImagesEdit.test.tsx` (3).

### Agent C — Report facet editor (`feat/mission-facet-report`)

- `frontend/src/pages/MissionReportEdit.tsx` — focused editor for
  the most complex facet. Extracted verbatim from `MissionNew.tsx`
  Step 4 (narrative + AI generate/poll + draft save) plus Step 6's
  Generate PDF + Send-to-Customer actions which logically belong
  with the report. Mounted at `/missions/:id/report/edit`.
- API surface preserved exactly: `GET /missions/{id}`,
  `GET/PUT /missions/{id}/report`,
  `POST /missions/{id}/report/generate`,
  `GET /missions/{id}/report/status/{task_id}`,
  `POST /missions/{id}/report/pdf` (blob, 120s timeout),
  `POST /missions/{id}/report/send`.
- AI-generation polling preserved verbatim (3s cadence, task-id
  status loop, finished-fetch + notification cascade).
- Same `RichTextEditor` (Mantine Tiptap) and `PdfViewer` components
  as the legacy wizard — no library swap.
- Inline `Last saved {N min ago}` / `Last sent {N min ago}` /
  `AI generated {N min ago}` indicators derived from response
  timestamps.
- Cancel routes back to `/missions/:id` (the Hub).

**Tests:** 7 tests in `MissionReportEdit.test.tsx` — initial GET
hydration, Save Draft body, Generate AI fire, Generate PDF fire,
Send fire, Cancel navigation, and the cross-action
`POST /api/missions = 0` contract verified across all four write
paths.

### Agent D — Routing + legacy preservation + cross-cutting tests + ADR-0014 (`feat/mission-routing-tests`)

- `frontend/src/pages/MissionNew.tsx` → `MissionWizardLegacy.tsx`
  (rename via `git mv`, no code change inside). Preserved as the
  soak-window fallback per spec §3 + ADR-0014.
- `frontend/src/App.tsx` — route table updated per spec §3:
  - `/missions/new` → `MissionsNewLegacyRedirect` (redirect to
    `/missions` list with a Mantine notification — stale bookmarks
    degrade gracefully).
  - `/missions/:id/edit` → `MissionEditLegacyRedirect` (Navigate
    to `/missions/:id` — preserves operator bookmarks).
  - `/missions/:id/edit-legacy` → `MissionWizardLegacy` (the
    soak-window fallback).
  - `/missions/:id/details/edit`, `.../flights/edit`,
    `.../images/edit`, `.../report/edit` → Agents B/C facet
    editors.
  - `/missions/:id/invoice/edit` → unchanged
    `MissionInvoiceEdit` from v2.66.0.
  - `/missions/:id` → `MissionDetail` (the new Hub).
- `frontend/src/pages/Dashboard.tsx` — "+ NEW MISSION" button now
  opens the same `MissionCreateModal` inline rather than navigating
  to `/missions/new`. Single source of truth for the create UX
  across Dashboard + Missions list.
- `docs/adr/0014-mission-hub-redesign.md` (NEW) — full ADR with
  Context (the duplicate-mission incident), Decision (Hub + Facet),
  Consequences (every shipped 2026-05-02/03 feature integrated per
  spec §9.5; bug class physically impossible; legacy wizard
  preserved), Alternatives Considered (defensive patch on shared
  component / full rewrite without fallback / chosen Hub + Facet
  with fallback), Deletion Criteria for `MissionWizardLegacy.tsx`
  (≥1wk operator-confirmed prod use + zero
  `/missions/:id/edit-legacy` hits in nginx logs over 7-day window
  + operator explicit OK).
- `frontend/src/__tests__/missions.routes.test.tsx` (NEW) —
  cross-cutting routes contract test (9 tests). Each new route
  mounts the right component or redirects correctly. `afterEach()`
  asserts the load-bearing tripwire `postMissionsCallCount === 0`
  across every routed page.
- `frontend/vite.config.ts` — `test.fileParallelism=false` to
  eliminate cross-file `vi.mock('react-router-dom', ...)` bleed
  that intermittently timed out the MissionDetailsEdit Save test.
  Documented; reversible once the facet tests refactor their mocks
  into beforeEach.
- **4 pre-existing test failures repaired** (landed with v2.66.3 /
  v2.66.4, NOT introduced by today's redesign):
  - `test_tos_accept_flips_tos_signed_to_true` had a stale
    assertion contradicting the v2.66.4 hotfix
    (asserted `tzinfo is not None`, hotfix strips tzinfo). Updated
    to the v2.66.4-aligned contract.
  - `test_tos_customer_tz_naive_sync.py` SQLite fixture called
    `Base.metadata.create_all`, crashing on the PG-only `INET`
    type in `tos_acceptances.client_ip`. Switched to
    per-table `Customer.__table__.create` (the test only writes
    through Customer).
  - `test_customer_tos_signed_at_must_be_tz_naive` marked
    `@pytest.mark.skip` with documented reason — the assertion is
    a Postgres-only contract that SQLite cannot prove. The v2.66.4
    fix itself is still proved by `test_v2664_fix_strip_tzinfo_works`
    in the same file. Follow-up: replace SQLite fixture with a
    Postgres testcontainer.

### Done definition (spec §11) — orchestrator runs post-deploy

15-checkbox done definition is verified by the orchestration plan's
Task 8 after the consolidated v2.67.0 bump lands:

- [ ] All four agent branches merged to `main`
- [ ] Backend contract tests: 100% pass (184 passed, 1 skipped at
      D's worktree time)
- [ ] Frontend contract tests: 100% pass (34 passed at D's
      worktree time)
- [ ] Existing test suites: 100% pass (no regressions)
- [ ] Manual E2E: create + edit details + edit flights + edit
      images + edit report + edit invoice + mark COMPLETED + mark
      SENT — all observed working
- [ ] DB row-count assertion (no duplicates created during testing)
- [ ] Legacy wizard at `/missions/:id/edit-legacy` still loads and
      saves correctly
- [ ] Version bumped to v2.67.0 in 4 files (orchestrator's commit)
- [ ] CHANGELOG entry appended (this entry)
- [ ] ADR-0014 written + committed (this slice)
- [ ] Live deploy verified: `app.version` reads `2.67.0`
- [ ] External smoke probes return expected status codes
- [ ] §8.5 lock-down semantics verified end-to-end
- [ ] §8.6 deposit-billing flow verified end-to-end
- [ ] §9.5 integration audit table — every row verified

### Integrated 2026-05-02/03 features (spec §9.5)

The Hub redesign preserves and surfaces every feature shipped in the
preceding 48 hours:

- **ADR-0008** invoice gated on mission status — Hub Invoice card
  surfaces visibility state.
- **ADR-0009** deposit feature (7 invoice columns + payment_phase +
  pay/deposit + pay/balance routes) — Hub Invoice card §8.6 surfaces
  deposit state + Issue Portal Link + Send Email + Copy Link as
  first-class operator actions.
- **ADR-0010** AcroForm TOS rebuild — Customer card on the Hub
  links to `/tos-acceptances?customer_id=…`; Issue-Portal-Link
  routes the customer through the existing `/tos/accept` AcroForm
  gate.
- **ADR-0011** payment idempotency + sequential invoice numbering —
  Hub Invoice card displays `BARNARDHQ-YYYY-NNNN` prominently;
  Issue-Portal-Link is idempotent.
- **ADR-0012** secret hygiene — gitleaks pre-commit + CI gate
  active across all four agent branches; no new secrets introduced.
- **ADR-0013** contract tests + 4xx burst alerting — every new
  Hub backend route ships an `httpx.AsyncClient` test (no
  `_mk_payload(SimpleNamespace)` bypass); every new Hub frontend
  page ships a Vitest+msw test with the load-bearing
  `POST /api/missions = 0` tripwire.

### Legacy wizard deletion criteria

`frontend/src/pages/MissionWizardLegacy.tsx` (and the
`/missions/:id/edit-legacy` route, and this CHANGELOG note's
"preserved for soak" caveat) get retired in a single commit when
**all three** of the following are true:

1. ≥ 1 week of operator-confirmed production use of the new Hub
   flow.
2. **Zero** `/missions/:id/edit-legacy` route hits in nginx access
   logs across a rolling 7-day window.
3. Operator explicit OK to delete.

A follow-up ADR (likely ADR-0015 or higher) closes out the migration
once the criteria are met.

**Failover/resilience guard:** zero schema changes; the only backend
additions are an additive PATCH route, an additive 400 guard on
POST, and a structured WARN log. PostgreSQL streaming replication,
blue-green deploy, and the failover engine see no behavioral change.

## [2.66.4] — 2026-05-03 — fix(tos): strip tzinfo when syncing customers.tos_signed_at (P0 hotfix)

**P0 hotfix for the v2.66.3 sync code.** `customer.tos_signed_at = ctx.accepted_at`
crashed because `ctx.accepted_at` is timezone-aware UTC but the
`customers.tos_signed_at` column is mapped naive (`DateTime` /
`TIMESTAMP WITHOUT TIME ZONE`). SQLAlchemy's dirty-tracking comparison
raised `can't subtract offset-naive and offset-aware datetimes` AFTER
the audit row had already been persisted — customer saw HTTP 500 and
the signed PDF was orphaned on disk.

Fix: `ctx.accepted_at.replace(tzinfo=None)`. Same UTC moment, naive
form. `tos_acceptances.accepted_at` (the audit table) stays tz-aware
because its column IS tz-aware in its model.

Why W2's tests passed: the existing `test_tos_customer_sync.py`
fixtures used `_mk_payload(SimpleNamespace(...))` which bypassed the
real ORM column write — the exact anti-pattern ADR-0013 forbids for
HTTP routes. Per ADR-0013, these fixtures should be retroactively
replaced with real `httpx.AsyncClient` contract tests; this hotfix
adds a real-ORM-path regression test
(`test_tos_customer_tz_naive_sync.py`) as a down payment.

## [2.66.3] — 2026-05-03 — fix: Customers page reflects new AcroForm TOS flow

After v2.65.0 + v2.66.0 (ADR-0010 AcroForm pipeline + customer
name/email sync), every TOS submission via `/tos/accept` wrote a
`tos_acceptances` audit row but never flipped the per-customer
`customers.tos_signed` boolean. Result: the operator Customers page
kept showing newly-signed customers as "TOS not signed" forever, and
the in-row "Signed TOS Viewer" modal could not find the PDF
(legacy `tos_pdf_path` was null for these customers).

**Backend (3 changes):**

1. `app/routers/tos.py:accept_terms` — also sets
   `customer.tos_signed=True` + `customer.tos_signed_at` to the
   `AcceptanceContext.accepted_at` timestamp on every customer-bound
   accept. The legacy `tos_pdf_path` column is left untouched (the
   new flow's PDF lives at `tos_acceptances.signed_pdf_path` and is
   served via the existing operator-JWT-gated `/api/tos/signed/{id}`
   route).
2. `app/schemas/customer.py` — `CustomerResponse` now exposes
   `latest_tos_audit_id`, `latest_tos_signed_sha`,
   `latest_tos_template_version`. All three are null for legacy
   canvas-signed customers.
3. `app/routers/customers.py` — list/get/update endpoints populate
   the new fields by joining the most-recent `tos_acceptances` row
   per customer. List endpoint uses `DISTINCT ON (customer_id)`
   ordered by `accepted_at DESC` for one-query bulk fetch (no N+1);
   detail/update endpoints use a simple `LIMIT 1` subselect.

**Frontend (`frontend/src/pages/Customers.tsx`):**

- Badge label is now `TOS SIGNED · DOC-001/TOS/REV3` (Share Tech Mono,
  dimmed) for AcroForm-signed customers; legacy customers keep the
  bare `TOS SIGNED` badge.
- Signed-TOS viewer modal switches its fetch URL based on
  `latest_tos_audit_id`: present → `/api/tos/signed/{audit_id}` (new
  AcroForm PDF), null → `/api/intake/{id}/signed-tos` (legacy canvas
  composite). Failure path now `console.error`s the source path +
  HTTP status for next-class-of-failure triage.
- Modal header carries the template-version chip, first-12-char
  truncated SHA (full hash on hover) and an `AUDIT HISTORY` button
  linking to `/tos-acceptances?customer_id=…`.
- New per-row `IconHistory` ActionIcon next to the badge — direct
  link to per-customer audit history.

**Tests:**

- 6 new test cases in `test_tos_customer_sync.py` /
  `test_customer_response_tos_audit.py` /
  `test_customers_router_tos_audit.py`. All 166 backend tests pass.
- Existing `test_tos_customer_sync.py` updated: `tos_signed` flip
  now fires on every customer-bound accept, so the previously
  "single-commit no-op" branch now commits twice.
- Slowapi rate-limit cap (5/minute keyed on client IP) was hitting
  the 6th hermetic test in the file — added a per-test IP counter
  in the request-stub helper to keep tests independent.

**Schema/migration impact:** none. The three new fields are computed
from JOIN — no new columns. Failover/replication unaffected.

## [2.66.2] — 2026-05-03 — fix: P0 hotfix — `POST /api/tos/accept` 422 on every customer submission

**Addendum (copy-only, no version bump):** TOS success view + signed-TOS
email — drop premature payment mention + "if you don't hear back" line;
lead with welcome.

- `frontend/src/pages/TosAcceptance.tsx` — replace the "ACCEPTED" badge
  + "What happens next" framing card with a warmer two-sentence welcome
  ("Thank you. Welcome to BarnardHQ." heading + portal-link follow-up
  body). Brand cyan + Bebas-Neue display heading; Rajdhani body.
- `backend/app/templates/signed_tos_email.html` — same copy swap inside
  the existing brand-cyan accent card. Heading rendered Bebas Neue →
  Arial Black → Arial; body keeps Rajdhani fallback chain. Inline
  styles preserved for email-client safety.
- Operator's portal-link delivery channel is no longer named in the
  customer copy (was "by email or text") since the customer doesn't
  need to predict it. Audit record + Download Signed Copy button +
  legal/E-SIGN paragraph + brand footer all unchanged.


A paying customer hit the TOS-acceptance form at 16:09 PT and got a
generic "acceptance failed" toast on every one of six retries. The
operator regenerated the intake link; the seventh attempt failed the
same way. Backend logs showed all seven returning HTTP 422 in <2 ms,
which is the classic Pydantic-422-before-handler signature.

**Root cause:** `app/routers/tos.py` was the only `@limiter.limit`-
decorated route in the repo whose body parameter was declared *before*
`request: Request` AND whose module declared `from __future__ import
annotations` (PEP 563). PEP 563 stringifies parameter annotations at
decoration time, so when FastAPI introspected the route to decide
"body or query?" it saw `payload: 'TosAcceptanceRequest'` (a forward
ref string) instead of the actual `BaseModel` subclass, fell through
to the `Query()` default, and every customer POST 422'd with
`loc=['query','payload']`. The handler never ran — no acceptance row,
no signed PDF, no email, no operator notification. The hermetic unit
tests in `test_tos_customer_sync.py` did not catch this because they
call the route function directly and bypass FastAPI's request-parsing
pipeline.

**Fix (3 surgical changes in `app/routers/tos.py`):**

1. Remove `from __future__ import annotations` — Python 3.12 supports
   the modern syntax (`str | None`) natively, the import was never
   load-bearing here, and its side effect was the production bug.
   A module-level NOTE comment locks the prohibition in place.
2. Reorder `accept_terms(...)` to put `request: Request` first,
   matching every other slowapi-decorated route in this repo
   (`auth.py`, `intake.py`, `client_portal.py`).
3. Wrap the payload as `Annotated[TosAcceptanceRequest, Body()]` so
   the body intent is explicit and immune to future annotation-
   inference quirks.

**Frontend hardening (`frontend/src/pages/TosAcceptance.tsx`):**

- The `onSubmit` catch block now parses Pydantic's array-of-detail
  shape, joins the per-field `msg` strings into a user-actionable
  message, falls back to the string detail or axios message, and
  always appends the HTTP status. The full response (status, detail,
  raw error) is `console.error`'d so a customer's browser console is
  enough to triage the next class of failure without redeploying.

**Regression test (`backend/tests/test_tos_accept_route_body.py`):**

- `test_tos_accept_route_payload_is_body_not_query` walks
  `app.openapi()` and asserts `requestBody` exists and no parameter
  named `payload` was created — catches the inference regression at
  the schema layer.
- 3 integration tests POST the exact frontend payload shape through
  `fastapi.testclient.TestClient` and assert 201, including the
  cold-visitor `customer_id=null` case and the `confirm: false`
  rejection path. All four tests fail without the fix and pass with
  it. Verified.

**Live verification:** post-deploy `POST /api/tos/accept` against
`https://droneops.barnardhq.com` with a fresh intake token returned
HTTP 201 with a real `audit_id`. Backend logs show the
`[TOS-ACCEPT-POST] SUCCESS` line that v2.66.1 was missing.

**Failover/replication impact:** none. Pure Python code-shape change;
no schema migration, no env var, no port binding.

A full post-incident analysis (ADR-0013) is forthcoming separately.

## [2.66.1] — 2026-05-03 — chore: secret hygiene + leak remediation (ADR-0012)

GitGuardian flagged commit `5ec9392` (the same-day `.env.demo` un-track)
as exposing a Generic Password. The actual exposure was historical: the
PG replication password and demo POSTGRES password had been baked into
the public repo since v2.53.0 — both as `${VAR:-<literal>}` compose
fallbacks and as hard-coded literals inside two standby `primary_conninfo`
strings and three init scripts. This release rotates everything that's
still live and removes every plaintext fallback so the leaked values
cannot be re-introduced silently.

**Rotated (live, BOS-HQ):**

- `droneops-demo-db` POSTGRES password (`doc_demo` role).
- Demo replication role password.
- Prod replication role password (`droneops-standby-db` BOS-HQ →
  `droneops-db-standby` CHAD-HQ). Atomic rotation on both sides;
  streaming verified post-cutover via `pg_stat_replication`.

No new credential value lands in this commit or in any commit. Values
live only in the per-host `.env` files (`~/droneops/.env` on BOS-HQ,
`~/droneops-demo/.env` + `~/droneops-demo/.env.demo` on BOS-HQ,
`~/droneops/.env` on CHAD-HQ).

**Repo changes — `:?required` is the new pattern:**

- `docker-compose.yml` — `POSTGRES_PASSWORD`, `REPLICATION_PASSWORD`,
  `JWT_SECRET_KEY`, `DATABASE_URL` now fail fast if unset.
- `docker-compose.standby.yml`, `docker-compose.demo-standby.yml` —
  `primary_conninfo` interpolates `${REPLICATION_PASSWORD:?…}` instead
  of carrying a literal.
- `docker-compose.demo.yml` — demo `DATABASE_URL` literal removed; now
  requires the value from `.env.demo`.
- `scripts/init-primary.sh`, `scripts/init-standby.sh`,
  `scripts/init-demo-standby.sh` — `: "${REPLICATION_PASSWORD:?…}"`
  guard at top, no fallback default.
- `README.md` — replication-password table cell shows
  *(no default — required)*.
- `.env.example` — required vars are blank with a REQUIRED comment.

**Prevention:**

- `.pre-commit-config.yaml` — gitleaks (`protect --staged`) +
  `detect-private-key` + baseline file hygiene. Run
  `pre-commit install` once per clone.
- `.gitleaks.toml` — extends upstream defaults with two repo-specific
  rules: (1) block reintroduction of the rotated literals by exact
  value; (2) block compose `${VAR:-<long_literal>}` fallback for any
  `*PASSWORD/SECRET/TOKEN/KEY` env var.
- `.github/workflows/secret-scan.yml` — gitleaks v8.21.2 runs on every
  push and PR on the BOS-HQ self-hosted runner; PR fails on detection.
- `.gitignore` — `.env.*` (with `!.env.example` allow), `*.pem`,
  `*.key`, `*.p12`, `*.pfx`, `*.crt`, `.secrets/`, `secrets.{yaml,yml}`,
  `.netrc`, `.pgpass`, `google-credentials.json`,
  `service-account*.json`.

**Documentation:** ADR-0012 (full context, trade-offs, operator
follow-ups). History rewrite intentionally NOT performed — see ADR §
"Trade-offs" for the why.

## [2.66.0] — 2026-05-03 — feat: backend hardening — payment idempotency, invoice numbering, webhook alerting

Bundle of backend fixes + verified dead-code cuts that close the last
known gaps in the v2.65.x payment surface. No schema migrations
required; no operator action required at deploy time.

**P0 fixes**

- **Customer email/name sync on TOS acceptance** — when an operator
  starts the no-email intake path, the customer row is stubbed with
  `email IS NULL` and `name = "Pending Intake YYYY-MM-DD"`. After the
  customer types their name + email into the AcroForm TOS page, both
  fields are now synced back onto the customer row, so the operator
  can immediately email them a portal link. Logged as
  `[CLIENT-PORTAL] Synced customer name/email from TOS acceptance`.
- **Stripe webhook signature failure → urgent ntfy alert.** A
  `SignatureVerificationError` now fires an `urgent` ntfy alert
  (priority=2) on `droneops-deposits` BEFORE the 400 is raised. Redis-
  backed dedup with 5-minute cooldown prevents flood spam. Webhook-
  secret rotations that the System Settings table falls behind on
  are now visible immediately instead of silently dropping every
  paid customer event. (See ADR-0011 §3.)
- **Public TOS endpoints rate-limited.** `GET /api/tos/template` is
  now 10/minute per IP; `POST /api/tos/accept` is 5/minute per IP.
  Mirrors the cadence on `intake.get_intake_form` /
  `intake.submit_intake_form`.
- **Pay/deposit + pay/balance idempotency.** A customer who double-
  clicks Pay no longer mints two Stripe Checkout sessions. If the
  invoice already has a recent (≤30 min), unpaid session, the
  existing URL is returned. Network failure on the freshness probe
  falls through to the previous mint-fresh path. (See ADR-0011 §1.)
- **Sequential invoice numbering.** New invoices receive a stable
  `BARNARDHQ-YYYY-NNNN` identifier (4-digit zero-padded counter, year
  prefix resets every Jan 1). Atomic via a single PG `INSERT … ON
  CONFLICT DO UPDATE … RETURNING` against `system_settings`. Existing
  pre-v2.66.0 invoices keep `invoice_number = NULL` (no backfill —
  they were dev/test). (See ADR-0011 §2.)

**P1 fixes**

- **`TosAcceptanceRequest.email`** — confirmed as Pydantic `EmailStr`
  (was already in source); explicit unit tests added so a future
  loosening can't regress silently.
- **Real `/api/health` probe.** Replaces the trivial process-up
  response with a real DB `SELECT 1` + Redis `PING` + cached (30s TTL)
  Stripe `Account.retrieve`. Returns 503 + `{"status":"degraded", ...}`
  on any probe failure so Docker / NOC / Watchtower see an explicit
  unhealthy signal. `/health` alias preserved for stale-APK clients.
- **Webhook fallback for legacy invoices.** Pre-v2.65.0 invoices have
  `deposit_checkout_session_id = NULL`. The deposit branch of the
  Stripe webhook handler now falls back to looking up by
  `stripe_checkout_session_id` before logging "no invoice found",
  which prevents silent drop on legacy invoice + deposit payment_phase.

**Operator audit-browse UI (TOS acceptances)**

- New `GET /api/tos/acceptances` operator-only endpoint
  (`Depends(get_current_user)`). Free-text `q` (ILIKE on
  `client_email` / `audit_id` / `client_name`), optional
  `customer_id` UUID filter, `limit` (1–200, default 50), `offset`
  pagination, ordered `accepted_at DESC`. Returns full audit row
  detail minus `signed_pdf_path` (filesystem-internal) plus a
  `download_url` pointing back at the existing operator-gated
  `/api/tos/signed/{audit_id}`.
- New schemas `TosAcceptanceListItem` + `TosAcceptanceListResponse`
  alongside the existing `TosAcceptanceResponse`.
- New `frontend/src/pages/TosAcceptancesAdmin.tsx` mounted at
  `/tos-acceptances`. Search input (300 ms debounce), table with
  click-to-copy audit IDs + truncated SHA hashes (full hash on hover
  tooltip), download / view-customer / by-token-link actions per row,
  Mantine Pagination. Operator dark theme + cyan accents (NOT the
  customer-portal brand). Lazy-loaded chunk (~6.9 kB gzipped).
- Nav entry "TOS Audit" added to AppShell sidebar (mobile drawer +
  desktop sidebar both pull from the shared `navItems` array).
- Tests: `test_tos_acceptances_list.py` — 8 hermetic cases covering
  empty list, populated list + download_url synthesis, ILIKE
  search by email / audit-id fragment, customer_id filter,
  pagination passthrough, default ordering, and auth-dep wiring.
  No new ADR — this exposes existing data (ADR-0010) under existing
  auth (`get_current_user`); failover-neutral, no schema change.

**Verified cuts (Cut 1 — duplicate routes)**

- **Removed unreachable duplicate operator routes** in
  `routers/client_portal.py` (lines ~297-460 in v2.65.1). FastAPI
  uses the LAST registered handler when two share a path; the first
  set was dead code. Tests already exercised the second set, so no
  test changes were needed.

**Verified KEPT — audit was wrong**

- **`managed_instance` config flag + auto-provision block.** Used in
  5 places (`auth.py`, `system_settings.py` ×2, `llm_provider.py`,
  `main.py`). Load-bearing for managed-tenant routing. Kept.
- **Ollama integration** (`services/ollama.py`,
  `services/llm_provider.py`, settings UI provider toggle). Kept —
  `droneops-ollama-1` container is up and healthy on BOS-HQ;
  `OLLAMA_BASE_URL` set in prod `.env`; `claude_llm.py` imports
  `SYSTEM_PROMPT_TEMPLATE` from `ollama.py`.
- **Demo middleware + `useDemoMode` hook + Login auto-fill + AppShell
  banner.** Kept — the `droneops-demo-*` stack is running on BOS-HQ
  and serving traffic at command-demo.barnardhq.com. The middleware
  blocks destructive ops in that environment.
- **`/api/branding` endpoint.** Kept — `frontend/src/hooks/useBranding.ts`
  consumes it on every page load. The 5 customer-facing email
  templates use the same DB system_settings rows the endpoint
  surfaces; the public endpoint exists so the SPA can theme without
  needing an auth token.

**Documentation**

- New ADR `docs/adr/0011-payment-idempotency-and-invoice-numbering.md`.
- Test additions: `test_tos_customer_sync.py`,
  `test_stripe_webhook_signature_alert.py`,
  `test_stripe_webhook_legacy_fallback.py`,
  `test_pay_idempotency.py`, `test_invoice_numbering.py`,
  `test_health_check.py`, `test_tos_acceptances_list.py`.

**Frontend polish addendum (v2.66.0 same-release)**

UI / UX gaps closed alongside the backend hardening above. No version
bump (same release as the backend bundle); references the same
ADR-0011 where applicable. All changes preserve every existing feature
on the touched pages.

- **Operator MissionDetail — deposit-paid indicator (Fix #1).** Above
  the line items, a clearly-styled status block now shows whether the
  ADR-0009 deposit cleared. Green checkmark + ISO date when paid;
  yellow warning when due; grey "not required (Emergent Services)"
  when off. Closes the gap where an operator could mark a mission
  COMPLETED without knowing whether the deposit hit Stripe.
- **Operator MissionDetail — `Mark as SENT` button (Fix #5).** Mission
  status enum has had `SENT` since v2.0; the only path to flip there
  was direct DB. Button now appears on the header when status =
  `completed`, behind a Mantine confirm modal so a misclick can't
  permanently advance state. PUTs `{status: 'sent'}` to
  `/missions/{id}` and refreshes the local state.
- **Operator MissionDetail / new standalone Edit Invoice page (Fix #4).**
  New route `/missions/:id/invoice/edit` mounts a focused invoice form
  for an existing mission — line items, deposit toggle, deposit
  amount, tax, paid-in-full, notes — sharing the same backend
  endpoints (`GET/PUT/POST/DELETE /missions/{id}/invoice + /items`)
  as the wizard. New cyan-outline `EDIT INVOICE` button on the
  MissionDetail header. Pre-v2.66 the only path to fix a typo in the
  invoice was re-walking the 5-step `MissionNew` wizard.
- **TOS success view — "what happens next" copy (Fix #2).** After
  `/tos/accept` flips to ACCEPTED, the customer now sees a brand-cyan-
  bordered panel explaining that their operator has been notified and
  will send a secure portal link by email or text. Removes the
  "I signed it, now what?" gap.
- **TOS-signed email — same "what happens next" copy (Fix #3).**
  `signed_tos_email.html` mirrors the post-acceptance copy, so the
  customer gets the same expectation in their inbox alongside the
  attached signed PDF. Email-client safe (table-based, inline
  styles).
- **Customer portal — persistent payment polling refresh (Fix #6).**
  ClientMissionDetail's post-Stripe-redirect polling no longer ends
  silently after 30s. (1) A brand-cyan `Refresh` button now sits
  inside the payment-phase strip from the moment of redirect until
  the phase advances, re-arming the 30s polling clock on press.
  (2) The "I just paid" context is mirrored to `sessionStorage` so a
  hard refresh during polling preserves the confirming-state UI for
  up to 10 minutes. (3) Toast copy upgraded to the customerNotify
  helper: "Confirming payment with Stripe…" (info), "Payment
  Confirmed" (success), "Still processing — try Refresh in a moment"
  (warning).
- **TOS PDF iframe — mobile sizing (Fix #7).** The 5-page TOS PDF
  used to render in a ~467px-tall iframe on phones, leaving customers
  trying to scroll the page instead of the document. Mobile (≤768px)
  now gets `max(500px, 90vh - 24px)` plus a brand-mono caption
  ("Scroll inside the document to read all pages"). Desktop keeps
  the v2.65.0 `max(70vh, 800px)` cap.

## [2.65.1] — 2026-05-03 — feat(intake): email-optional Initiate Services + prominent Copy Link

Operator-facing UX tweak so Bill can generate an intake link without an
email on file (for cases where the customer prefers SMS/text or the
email address isn't known yet).

- **`POST /api/intake/initiate`** now accepts an empty/missing `email`
  field. When omitted: customer stub is created with `email=null` and
  name `"Pending Intake YYYY-MM-DD"` (operator updates from the
  TOS-acceptance row once the customer follows the link).
- **Operator-copyable URL pivoted from `/intake/{token}` to
  `/tos/accept?token=…&customer_id=…`** — same target the email flow
  has used since v2.65.0 (ADR-0010). Whether the operator emails or
  texts the link, the customer lands on the new AcroForm TOS page.
- **Initiate Services modal (Dashboard + Customers pages):** email
  input labeled "Customer Email (optional)"; help text now reads
  *"Enter the customer's email to send the onboarding link, or leave
  blank to generate a copyable link you can text."*; result view shows
  a prominent full-width **"COPY LINK"** button (green confirmation
  state once clicked); "SEND VIA EMAIL" button is conditional —
  rendered only when an email was provided.

## [2.65.0] — 2026-05-03 — feat: deposit-aware invoicing + AcroForm TOS + portal theming

Three subsystems shipped together — production-ready for first paying
customers under the BarnardHQ Rev 3 TOS. Implemented by three parallel
agents in isolated git worktrees, sequenced merge B → A → C; one
read-only background agent ran a privacy audit of the public repo
in parallel.

- **Deposit feature (ADR-0009).** `invoices` table extended with 7
  deposit columns (`deposit_required`, `deposit_amount`,
  `deposit_paid`, `deposit_paid_at`, `deposit_payment_intent_id`,
  `deposit_checkout_session_id`, `deposit_payment_method`) via
  `_add_missing_columns` (no Alembic in this repo). Default 50% per
  TOS §6.2; operator can opt-out per mission for Emergent Services
  per TOS §6.3. Two-phase customer payment via Stripe Checkout
  (`POST /api/client/missions/{id}/invoice/pay/deposit` any time;
  `POST .../pay/balance` gated on mission `COMPLETED|SENT` AND
  deposit paid). Legacy `/pay` retained as back-compat alias. Webhook
  branches on `metadata.payment_phase` (`deposit | balance`),
  idempotent on each phase; ntfy push to operator on each phase paid.
  53 new tests pass.

- **TOS-acceptance rebuild (ADR-0010).** Replaces canvas-signature
  widget with AcroForm-fill on the BarnardHQ Rev 3 fillable PDF
  (already uploaded to prod 2026-05-03). Seven AcroForm fields filled
  + `/Ff` ReadOnly bit locked, both pre/post bytes SHA-256-anchored
  in new `tos_acceptances` audit table. Settings upload validates
  required fields. Customer signs at `/tos/accept?token=…&customer_id=…`
  (intake email link pivoted accordingly). Operator + customer both
  emailed signed PDF. 12 new tests pass. Old `/intake/{token}` flow
  + canvas widget intentionally preserved during cutover (Phase 4
  cleanup deferred per TOS-Rebuild.md §1.2).

- **Customer portal theming.** All `/client/*` and `/tos/accept`
  pages + 5 transactional emails re-themed to BarnardHQ TOS PDF brand.
  New shared `CustomerLayout` component (navy header strip + Bebas
  wordmark + Share Tech Mono footer line `BarnardHQ LLC · Eugene,
  Oregon · FAA Part 107 Certified · barnardhq.com · DOC-001`). Brand
  cyan `#189cc6` (TOS palette) replaces operator `#00d4ff` in customer
  surfaces. Wordmark + footer line everywhere. `?payment=cancel` now
  first-class (was silently ignored). TypeScript clean, Vite build
  succeeds, Jinja smoke render of all 5 emails confirms wordmark +
  footer + brand cyan present. Operator UI deliberately untouched.

- **Cloudflare Access** bypass app `e2d36c3f-…` extended with
  `/tos/*` and `/api/tos/*` destinations (now 5/5 — at the per-app
  cap, but exactly where we need to be). Customer can now reach
  the TOS-acceptance page from any external IP without CF login.

- **Privacy audit** of the public repo: 0 PII, 0 Stripe/Brevo/CF API
  token/Anthropic/GH PAT secrets in history. **CRITICAL findings**
  separately surfaced for operator action: pre-existing leaked
  Cloudflare Tunnel token + DB passwords in `.env.demo` since
  v2.53.0 (`f6f66ff`); prod replication password as fallback default
  in 6 files. These are pre-existing leaks, NOT introduced by
  v2.65.0; remediation tracked separately as the next workstream.

- **GitHub push protection** caught a Cloudflare Account API token I
  inadvertently pasted into the orchestration plan during v2.65.0
  prep — scrubbed before push (commit `c0dd70d`). Validates that the
  protection works.

## [2.64.0] — 2026-05-02 — feat(client-portal): gate invoice visibility + Pay on mission completion (ADR-0008)

**Also includes two latent-bug fixes** discovered during ADR-0008
end-to-end testing — neither had ever been tripped because no
customer had ever exercised the portal Pay path until today:

- **`Invoice` model was never imported** in `client_portal.py`,
  even though `select(Invoice)...` had been there since v2.57.x.
  Fixed via `from app.models.invoice import Invoice` +
  `selectinload(Invoice.line_items)` on both queries (async
  lazy-load would have crashed serialization).
- **`UUID(client.customer_id)` raised `AttributeError: 'UUID'
  object has no attribute 'replace'`** because the JWT decode
  yields a `UUID` object, not a str, and `UUID(uuid_obj)` calls
  `.replace('-','')` on its arg internally. Fixed by passing
  `client.customer_id` straight through — SQLAlchemy accepts
  either form against a UUID column.



The customer-facing client portal now refuses to surface the invoice
or accept payment until the operator has marked the mission
`COMPLETED` or `SENT`. The operator can still draft and edit the
invoice during `PROCESSING` / `REVIEW`; only the public-facing
release is gated. See `docs/adr/0008-customer-payment-gated-on-mission-completion.md`.

- **`backend/app/routers/client_portal.py`** — added module-level
  `INVOICE_VISIBLE_STATUSES = frozenset({COMPLETED, SENT})`.
- `get_client_invoice` returns `None` (same shape as no-invoice-yet)
  for missions in any other state; logs `[CLIENT-INVOICE] HIDDEN`
  with mission/customer/status for audit.
- `create_client_payment` returns `400` with the message
  *"This invoice is not yet available for payment. Your operator
  will mark the mission complete once the work is finished."*; logs
  `[CLIENT-PAY] BLOCKED`.
- **No operator-side endpoint changed.** `POST /api/missions/{id}/invoice`,
  line-item edits, and rate-template applications continue to work on
  every mission status — the gate is one-sided by design.
- **No frontend change required** — the SPA already handles the
  null-invoice path cleanly.

This makes the TOS-aligned promise (no payment requested until
delivery) machine-enforced rather than convention-enforced.

## 2026-05-02 — Stripe activation (config-only, no version bump)

Wired the existing Stripe Checkout / webhook code (shipped in v2.57.x
client-portal milestone) to live BarnardHQ Stripe account so customer
invoice payment now works end-to-end. **No code changed**, only
configuration + Cloudflare Access policy.

- **`system_settings` rows inserted** (DB on `droneops-standby-db` —
  the promoted primary): `stripe_secret_key` (`sk_live_…kA7s`),
  `stripe_publishable_key` (`pk_live_…hw38`), `stripe_webhook_secret`
  (`whsec_…6Bkl`). Same Stripe account `acct_1TFQnxECLLZwgS9H`
  (BarnardHQ, US, USD, charges + payouts enabled) that `~/marketing`
  has used since 2026-04-21. The xkeysib/xsmtpsib distinction does
  not apply — these are Stripe creds, all account-level except the
  webhook secret which is endpoint-specific (see next bullet).
- **New Stripe webhook endpoint registered:**
  `we_1TSn0UECLLZwgS9Hz4IGv6ZQ` →
  `https://droneops.barnardhq.com/api/webhooks/stripe`,
  events: `checkout.session.completed` (the only event the
  `stripe_webhook.py` handler dispatches on). Created via API; the
  `whsec_…6Bkl` returned by the Create call is the value stored above
  and is **endpoint-specific** — it is NOT interchangeable with the
  marketing webhook's `whsec_Wx…GkAP`.
- **CF Access bypass extended** (see `noc-master/CHANGELOG.md` for the
  full edge change). Stripe POSTs and customer-portal traffic now
  reach origin instead of being redirected to
  `barnardhq.cloudflareaccess.com/cdn-cgi/access/login`.
- **End-to-end verified 2026-05-02 23:30 UTC:**
  1. Outside-IP probe of `POST /api/webhooks/stripe` with a real
     HMAC-SHA256-signed `checkout.session.completed` event →
     `200 {"status":"ok"}` (signature verification passes).
  2. `stripe_service.create_checkout_session()` against an existing
     $500 invoice with 3 line items → real `cs_live_…` URL; HEAD on
     the hosted page → `200 text/html` (Bill's branded
     `checkout.barnardhq.com`).
  3. Operator-issued client-portal magic link (24h JWT) opened from
     a non-Bill external IP → SPA shell loads (`D.O.C — Drone
     Operations Command` title), `/api/client/missions/{id}` returns
     200 with the JWT, returns 403 (auth required) without it. None
     of these paths returned a CF Access redirect.

**Forward-looking:** any new customer who completes a Checkout will
trigger `stripe.WebhookEndpoint we_1TSn0U…` → POST our webhook →
signature verified with `whsec_…6Bkl` → `Invoice.paid_in_full=true`,
`paid_at`, `payment_method` (stripe_card or stripe_ach) +
`stripe_payment_intent_id` recorded → `send_payment_received_email`
fires through Brevo SMTP. Watch `doc.stripe` logger for
`[STRIPE-WEBHOOK]` entries.

## [2.63.15] — 2026-05-01 — audit(flights): tighten attribution edges + lock with regression tests

Audit follow-up to v2.63.14. Three real defects, one regression test
suite.

- **Whitespace-only `drone_serial` no longer reaches the DB.** Some DJI
  parsers emit a present-but-blank serial field as `"   "`. The truthy
  guard at the top of `_match_fleet_aircraft` was letting it through,
  hitting the DB with an empty query and producing a confusing
  `"serial=    present but unmatched in fleet"` log line. Both
  `drone_serial` and `drone_model` are now stripped at function entry
  (`(s or "").strip() or None`), so whitespace inputs fall through to
  the no-serial branch correctly. Tested.
- **Startup backfill no longer overwrites operator-curated `drone_model`.**
  Phase 2 of the auto-backfill in `app/main.py` was iterating every
  linked flight on every container restart and rewriting
  `flight.drone_model` to match the canonical `Aircraft.model_name`.
  That clobbered operator data for flights that were manually attached
  via Flights → Edit and intentionally kept the parsed model string
  verbatim. Phase 1 (linking unattached flights) is preserved on
  startup; Phase 2 (canonical-name normalize) is only run on demand
  via `POST /api/flight-library/backfill-aircraft`, which is the right
  surface for "I just renamed an aircraft, sync linked flights"
  workflows.
- **Flights filter dropdown no longer shows visually-identical
  duplicates.** `getDroneModel()` now `.trim()`s its result, so legacy
  ODL-imported records carrying trailing whitespace in `droneModel` no
  longer survive the `Set` dedupe to produce two rows that look the
  same. Not a Mantine crash (values still differ), but a UX defect.
- **Regression tests added** (`backend/tests/test_flight_attribution.py`).
  12 hermetic unit cases lock in the strict matcher behavior — including
  explicit assertions that the old prefix and substring fuzzy-match
  rules cannot silently come back, and that the whitespace-serial edge
  case routes correctly. All pass.

No DB migrations; no impact on PG replication, blue-green, or failover.

## [2.63.14] — 2026-05-01 — fix(flights): tighten fleet attribution + Batteries page Mantine crash

Adding a new aircraft to the fleet caused two regressions:

1. **Every uploaded flight (and its batteries, by inheritance through
   `BatteryLog → Flight.aircraft_id`) was being attributed to the new
   drone**, even when the flight came from a different aircraft. Root
   cause: `_match_fleet_aircraft()` in `backend/app/routers/flight_library.py`
   ran a three-pass matcher: (1) exact serial, (2) symmetric prefix
   model match, (3) symmetric substring model match. When a flight log's
   serial wasn't present in the fleet, passes 2/3 were so loose that any
   parsed model name sharing a prefix with a fleet record (e.g. parsed
   "Mavic 3" against fleet "DJI Mavic 3 Pro" → `mavic3pro.startswith("mavic3")`)
   would absorb the flight. Adding a new drone with a broader model name
   immediately captured every previously-uploaded flight on the next
   startup backfill.

   **Fix (ADR-0007):** drop pass 2 and pass 3 entirely. Serial match is
   now authoritative — if a serial is present in the flight log, it must
   match a fleet aircraft exactly or the flight stays unattributed. With
   no serial, only an exact normalized model match attributes the flight,
   and only when exactly one fleet aircraft carries that model
   (multiple = ambiguous → unattributed). Match decisions are now logged
   at INFO under `doc.flights` so silently-unattributed flights are
   diagnosable from logs.

   The startup backfill in `main.py:308-346` already operates only on
   `aircraft_id IS NULL` rows, so manual detachments via the Flights UI
   stick across container restarts. Existing flights misattributed by
   the old fuzzy matcher need to be detached manually from the Flights
   page (Edit → Aircraft → clear) — the backfill won't undo prior writes.

2. **Batteries page crash:** `[@mantine/core] Duplicate options are not
   supported. Option with value "DJI Mavic 3 Pro" was provided more than
   once`. `loadDroneModels()` in `frontend/src/pages/Batteries.tsx` pulled
   `model_name` from every fleet aircraft without deduping. A fleet that
   holds two units of the same model (normal once you add a second
   Mavic 3 Pro) produced duplicate Select values, which Mantine v7 hard-rejects.
   Wrapped in `Array.from(new Set(...))`.

Files touched:
- `backend/app/routers/flight_library.py` — `_match_fleet_aircraft` rewritten
- `frontend/src/pages/Batteries.tsx` — dedupe model dropdown source
- `docs/adr/0007-strict-fleet-attribution-matcher.md` — decision record

No database migrations. No impact on PG replication, blue-green, or
failover (FastAPI app code only).

## [2.63.13] — 2026-05-01 — fix(fleet): repair broken aircraft images + add DJI Mavic 4 Pro

Fleet settings tab was rendering broken-image icons for every drone.
Root cause: the 2026-04-20 BOS migration left `aircraft.image_filename`
rows pointing at `/data/uploads/aircraft/<uuid>/<file>.jpg` paths that
no longer exist on disk. The `/uploads/{filename}` fallback only
serves bundled defaults when the request path has no slash, so every
slashed user-upload path 404'd silently.

- **Bundled fleet PNGs replaced** with the canonical artwork from the
  BarnardHQ public-site fleet carousel
  (`barnardhq/site/images/{m30t,m4td,mavic4pro,mavic3pro,avata2,mini5pro,fpv}.png`).
  All seven 1000×1000 RGBA — true transparent backgrounds, identical
  bytes, no re-export. Added `dji_mavic4pro_official.png` (new — was
  missing from the bundled set entirely).
- **`backend/app/seed.py` heal pass** — at the end of `seed_database()`,
  scan every `aircraft` row whose `image_filename` is a slashed
  uploads-path that no longer exists on disk, and fall back to the
  bundled default PNG that matches the model name. Logs each healed
  row at WARN, plus a single INFO summary. Idempotent — only touches
  rows whose path is genuinely missing.
- **`backend/app/seed.py` AIRCRAFT_SEED** — added DJI Mavic 4 Pro
  (Creator Combo) entry with the 14 spec fields drawn from the
  BarnardHQ carousel + DJI's published Mavic 4 Pro spec sheet
  (controller, internal storage, gimbal, operating temp). Refreshed
  `DJI Mavic 3 Pro` and `DJI Mini 5 Pro` specs to match the carousel
  (Mini 5 Pro flight time corrected to 52 min, sensor to 1", wind to
  27 mph; Mavic 3 Pro camera string expanded to include focal lengths
  and HDR).
- **Source of truth:** `barnardhq/site/index.html` fleet carousel
  (slides 1–7). Same images, same spec values — the carousel and the
  Fleet tab now agree.

Operator notes:
- The duplicate empty `DJI Mavic 3 Pro` row (id `6e235b05…`, 0 flights,
  empty specs) was left in place — likely Bill's incomplete add of the
  Mavic 4 Pro under the wrong model name. Once this seed runs, a
  proper `DJI Mavic 4 Pro` row will materialise; Bill can delete the
  empty stub via the Settings UI.
- The `M3P - DECOM` row was untouched (0 flights, decommissioned).

## [2.63.12] — 2026-04-25 — feat: migrate Pushover module to ntfy (ADR-0036 + ADR-0006)

Replaces the Pushover transport for the ADR-0002 §5 silent-drift
watchdog and the ADR-0003 zero-touch key rotation FYI. Strategic frame
in NOC-Master ADR-0036; this repo's local addendum is ADR-0006.

- `backend/app/services/pushover.py` → `backend/app/services/ntfy.py`.
  Same dedup + Redis suppression semantics; same `send_alert` /
  `send_alert_sync` signatures (with optional `topic` / `click` /
  `tags` keyword args added per the ADR-0036 notification standard).
  Transport switched from `api.pushover.net` to self-hosted ntfy on
  BOS-HQ (`ntfy.barnardhq.com`) with publisher-side fallback to
  `ntfy.sh/<droneops-fallback-topic>` and `[FALLBACK]` title prefix
  on the fallback path. 5 s primary timeout + 5 s fallback timeout.
- Env: `PUSHOVER_APP_TOKEN` + `PUSHOVER_USER_KEY` →
  `NTFY_DRONEOPS_PUBLISHER_TOKEN` (one token instead of two).
  `docker-compose.yml` updated for backend, worker, beat services.
- `backend/app/auth/device.py:26`,
  `backend/app/routers/admin_device_rotation.py:35`, and
  `backend/app/tasks/celery_tasks.py` updated to import from the new
  module. Public function names preserved so call-site code is
  unchanged beyond the import path. Pushover-specific log event name
  `rotate_key_pushover_failed` renamed to transport-agnostic
  `rotate_key_alert_failed`.
- `backend/app/config.py` — `pushover_token` + `pushover_user_key`
  fields removed; `ntfy_droneops_publisher_token` added (single).
- `backend/tests/test_ntfy.py` — 13 new unit tests covering: primary
  success, primary-fail-fallback-success-with-prefix, both-fail
  returns False, unconfigured no-op, dedup suppression within TTL,
  Redis-down fail-open behaviour, priority mapping, Bearer-on-primary
  / no-Authorization-on-fallback, title prefix, default click URL.
- `backend/tests/test_device_key_rotation.py` —
  `test_rotate_pushover_dispatched` →
  `test_rotate_alert_dispatched` and equivalent rename for the
  failure-tolerance test. Mocks unchanged in shape; only the symbol
  the rotation router imports moved from `pushover.send_alert` to
  `ntfy.send_alert`.
- Click URLs follow the ADR-0036 3-tier priority contract; default
  fallback is `https://noc.barnardhq.com/status/droneops`.
- Soak-pause set on this repo
  (`~/noc-master/data/soak-pause/droneopscommand.pause`) — code lands
  on `main`, but no redeploy until the operator clears the pause.
- See ADR-0006 in this repo for the watchdog-contract preservation
  analysis; ADR-0036 in `noc-master` for the strategic frame.

## [2.63.11] — 2026-04-24 — docs: ADR-0004/0005 finalized; perf-audit series complete (FIX-5)

Fifth and final commit of the 2026-04-24 perf audit. Code-only this is
a no-op; documentation completes the loop.

- ADR-0004 status flipped from `proposed` to `accepted` with a pointer
  to ADR-0005 for the AFTER measurements.
- ADR-0005 final summary table populated with BOS-HQ measurements,
  acceptance verdict against plan §6 thresholds, anti-goals respected
  list, honest deltas section, and follow-up parking lot.
- ROADMAP unchanged (per audit, F-7 / F-8 / F-9 already entered there
  during the audit phase).
- Final headline: weather endpoint p95 7.4-8.3 s → 6-19 ms warm
  (~400-1200× faster) / 1.09 s cold (6.8-7.6× faster); 30-parallel
  `/api/customers` p95 est. 1.5-3.0 s → 0.27 s warm (5.5-11×); frontend
  main bundle 1.9 MB → 81 KB (23.5×).

## [2.63.10] — 2026-04-24 — perf: client-side `useApiCache` hook + Dashboard adoption (FIX-4, ADR-0005)

Fourth of five performance fixes from the 2026-04-24 perf audit. Targets
the "every navigation re-fetches" root cause: navigating Dashboard →
Flights → Dashboard previously re-ran all 6 dashboard list endpoints
plus the weather call, every time, with no client-side cache.

- **`frontend/src/hooks/useApiCache.ts`** (new, ~100 lines) — TTL-cached,
  request-deduplicated, mutation-invalidatable hook around `axios.get`.
  Same URL across components shares one round-trip; 30 s default TTL;
  errors don't poison the cache. `invalidate(prefix)` exported for
  mutations. ADR-0005 §FIX-4 documents the staleness-window decision
  and why a custom hook beat adopting TanStack Query (~14 KB gzipped
  + larger refactor than scope warrants; would erode FIX-3 gains).
- **`frontend/src/pages/Dashboard.tsx`** — replaced 6 `useEffect + api.get +
  setState` blocks with `useApiCache` calls (missions, customers,
  flightStats, maintenanceAlerts, nextServiceDue, batteries). Maintenance
  mutation handlers now call `invalidate('/maintenance/due')` +
  `invalidate('/maintenance/next-due')` and trigger a refetch. Weather
  remains imperative (auto-refresh + button) but the backend response is
  Redis-cached (FIX-1) so it's also fast.
- **`frontend/src/pages/Flights.tsx`** — moved the aircraft list fetch
  to `useApiCache` (rare-changing list, large payoff on cross-page nav).
  The complex flight-library loader was deliberately left imperative —
  it has multi-fallback semantics + post-mutation reloads that don't fit
  this scoped hook cleanly. Per `feedback_no_deferred_fixes.md`, the
  alternative was a deeper refactor outside this audit's scope.
- **Build verified locally** — main bundle still 83 KB
  (gzip 30 KB), no regression vs FIX-3.
- **Expected gain (target):** Dashboard → Flights → Dashboard navigation
  becomes near-instant on the second mount (cache hit instead of 6
  GETs). Combined with FIX-1 (Redis weather cache) the warm repeat-visit
  Dashboard p95 drops from ~7.5 s to ~150 ms.
- **Failover guard:** ✓ pure frontend; no backend, schema, or replication
  impact. Cache is per-tab, in-memory only.

ADR-0005 §FIX-3 finalized with BOS-HQ live measurements: main `index-*.js`
**1.9 MB → 81 KB** (23.5× smaller); 17 page chunks shipped on demand;
vendor chunks split as designed. FIX-3 ACCEPTED.

## [2.63.9] — 2026-04-24 — perf: code-split 17 main pages + Vite vendor chunks (FIX-3, ADR-0005)

Third of five performance fixes from the 2026-04-24 perf audit. Targets
the bundle-bloat root cause: the operator's first paint was gated on
downloading and parsing a single 1.9 MB / 251 KB CSS bundle that
included Leaflet (used only on /airspace + /flights/replay), react-pdf
(client portal only), tiptap (mission editor only), Mantine forms /
dropzone / dates (heavy), and @sentry/react.

- **`frontend/src/App.tsx`** — converted all 17 main authenticated
  pages to `React.lazy()` + a single shared `Suspense` fallback that
  uses the same dark-theme cyan loader the auth flow already uses (no
  visible flash on route transition). Login + Setup remain eager —
  they are pre-auth and tiny, and bundling them avoids first-paint flash.
- **`frontend/vite.config.ts`** — added `build.rollupOptions.output.
  manualChunks` to split vendor bundles: `mantine-core`, `mantine-rich`,
  `leaflet`, `tiptap`, `pdf`, `icons` (`@tabler/icons-react`), `sentry`.
  Each is cached independently by CF and persists across deploys whose
  Mantine/Leaflet/etc versions don't change.
- **Bundle graph (verified locally via `npm run build`):**
  - main `index-*.js`: **83 KB** (gzip **29 KB**) — was 1,900 KB.
  - Heaviest single chunk: `mantine-core` 467 KB / gzip 146 KB,
    cached separately, only paid once per Mantine version bump.
  - All 17 pages now ship as their own on-demand chunks
    (Dashboard 26 KB / 6.7 KB gz, Settings 73 KB / 16.8 KB gz, etc).
- **Expected gain (target):** Cold first-paint perceived latency on
  residential uplink drops from ~9.5 s (1.9 MB main + 250 KB CSS +
  weather sequential) to ~1.5-2.0 s (small router shell + Mantine core
  + first page chunk + cached weather). Heaviest pages
  (Settings/MissionNew/FlightReplay) load on demand.
- **Failover guard:** ✓ pure build-time change. CI rebuilds the frontend
  container; old hashes invalidate naturally.

## [2.63.8] — 2026-04-24 — perf: async DB pool tuning + cached `get_current_user` (FIX-2, ADR-0005)

Second of five performance fixes from the 2026-04-24 perf audit. Targets
the second highest-impact root cause — Settings page fan-out (34
sequential `api.get` calls) saturating the SQLAlchemy async connection
pool, plus a `SELECT * FROM users` on every authenticated request.

- **`backend/app/database.py`** — `pool_size` 5 → 20, `max_overflow`
  10 → 20. Total ceiling 15 → 40. Headroom verified live on BOS-HQ
  (Postgres `max_connections=100`, current usage 6) — worker(5) +
  beat(2) + flight-parser(5) + backend(40) = 52, leaves 48% PG headroom.
- **`backend/app/auth/jwt.py`** — added a 60 s in-process TTL cache
  around the User-row lookup in `get_current_user`. Keyed by
  `(username, token[:16])` so token rotation invalidates immediately.
  Cached payload is safe-to-replay primitives only (id, username,
  hashed_password, is_active, created_at) — a transient ORM `User`
  is rebuilt per hit. `invalidate_user_cache(username|None)` exposed
  for explicit invalidation; `auth.update_account` now calls it on
  password / username change.
- **5 new pytest cases** under `backend/tests/test_user_cache.py`
  (HIT, MISS, inactive-user reject, per-user invalidate, all-invalidate,
  TTL expiry).
- **Documented staleness window:** token revocation lag <=60 s; container
  restart wipes the cache (revalidates immediately). For self-hosted
  single-operator deployment this is acceptable. ADR-0005 §FIX-2.
- **Expected gain (target):** Settings p95 first-paint ~3.2 s → ~600 ms;
  30-parallel `/api/customers` burst < 700 ms.
- **Failover guard:** ✓ pool sizing is per-process and survives container
  restart; user cache is in-process only (no shared state); no schema
  change; no replication impact.

## [2.63.7] — 2026-04-24 — perf: parallelize + Redis-cache /api/weather/current (FIX-1, ADR-0005)

First of five performance fixes from the 2026-04-24 perf audit
(`docs/plans/2026-04-24-perf-audit.md`). Targets the highest-impact root
cause: the Dashboard's "FLIGHT CONDITIONS" panel was calling 5 external
aviation APIs sequentially (Open-Meteo + AviationWeather METAR/TFR/NOTAM
+ NWS) with **zero caching**, costing 7-8 s wall-clock on every render
and re-firing every 5 min for every viewer.

- **`backend/app/routers/weather.py`** — the 5 fetches now run
  concurrently via `asyncio.gather`. Slowest single fetch dominates
  latency (~1.5-2.5 s) instead of sum-of-fetches (~7-8 s).
- **`backend/app/services/cache.py`** (new, ~110 lines) — Redis-backed
  read-through cache helper (`get_or_fetch`). 5-minute TTL keyed by
  `doc:weather:current:{lat}:{lon}:{airport}`. **Failure-open**: Redis
  unreachable ⇒ live fetch (slow but correct) — never 500. INFO log
  on every hit (with TTL remaining) and miss; WARN on Redis failures.
- **6 new pytest cases** under `backend/tests/test_weather_cache.py`
  using `fakeredis` — covers HIT, MISS, GET-fail, SET-fail, invalidate,
  invalidate-failure-swallow.
- **Expected gain (target):** /api/weather/current cold p95 7-8 s → 1.5-2.5 s;
  warm p95 7-8 s → <50 ms. Dashboard first-paint p95 ~9.5 s → ~2.5 s.
- **Failover guard:** ✓ no schema, replication, or quorum impact. Redis
  is shared but cache is failure-open.

## [2.63.6] — 2026-04-24 — Zero-touch device API key rotation (ADR-0003)

Backend half of the v1.3.25 client release. Eliminates the manual key-paste
step that the 2026-04-24 incident required: when an operator rotates a
device's API key server-side, the paired DJI RC Pro now picks up the new
key automatically on its next preflight call. ROADMAP FU-7 closed.

- **Schema** — `device_api_keys` gains two nullable columns
  (`rotated_to_key_hash`, `rotation_grace_until`). Additive, failover-safe.
  Wired through `_add_missing_columns` per the project's existing migration
  pattern (no Alembic toolchain change).
- **Auth dep** (`backend/app/auth/device.py`) — accepts either `key_hash`
  or `rotated_to_key_hash` while `rotation_grace_until > now()`. Tags the
  matched row with `_authenticated_via_old_key` so the device-health
  endpoint can branch on credential class.
- **Endpoint** — `POST /api/admin/devices/{device_id}/rotate-key`. Admin
  auth (same `get_current_user` gate the existing device-keys endpoints
  use; ADR-0003 §6 flags RBAC as follow-up). Returns the new raw key
  exactly **once**. 409 on overlapping rotation; 503 if Redis is down
  (fail-closed).
- **Hint side-channel** — new raw key written to Redis
  `droneops:device-rotation-hint:{device_id}` with 15-min TTL. Device
  preflight hits `/api/flight-library/device-health` with old key; auth
  passes via grace window; endpoint checks for rotation hint and includes
  `{rotated_key, rotation_grace_until}` in response body **only when
  authenticated via old key**. Device stores the new key; on next preflight
  uses new key; no authorization failure, transparent to all other clients.
- **Celery finalizer** — `finalize_key_rotations_task` on 15-min beat
  (`tasks/celery_tasks.py`). At `rotation_grace_until` expiry, zeroes
  `rotated_to_key_hash` to deactivate the old key cleanly. Runs regardless
  of whether the hint was consumed (belt + suspenders).
- **Alerting** — Pushover FYI on rotation (env-gated like ADR-0002 §5).
  Trigger: rotation scheduled; content: device label + grace window TTL.
- **Tests** — 15 new hermetic cases in `backend/tests/test_device_key_rotation.py`
  covering: new-key generation, hash storage, grace-window auth (both
  old and new key), hint side-channel write/read, finalizer idempotency,
  overlapping-rotation reject, Redis-down fail-closed, notification
  dispatch, hint not-found branch, and POST-404 on missing device.
- **No schema migrations.** `_add_missing_columns` wired per existing pattern.
  Backward-compatible (old clients never see `rotated_key` field; new
  clients see it only during grace).

## [2.63.5] — 2026-04-24 — DroneOpsSync prevention mechanisms + landscape lock (ADR-0002 §5)

Companion layers 1+2 (banner on launch + preflight health gate) + server
layers 3+4 (silence watchdog + first-401 alert) + hardware constraint
(sensorLandscape + configChanges inject). ROADMAP FU-1 and FU-2 closed for
functionality; FU-3 and FU-4 entered.

## [2.63.4] — 2026-04-24 — Unauthenticated /health shim + CapacitorSync auth model (ADR-0002)

Fixes operator's DJI RC Pro upload failure and establishes device auth
pattern. FU-2 closed (delivered as JSON alias).

## [2.63.3] — 2026-04-20 — Maintenance type vocabulary unified

Fixes confusing overdue-maintenance alerts that could not be cleared via
UI. Frontend + backend terminology now unified; legacy snake_case records
remapped to Title-Case via idempotent migration script.

## [2.63.2] — 2026-04-19 — Redis-heartbeat healthcheck (zombie-leak audit)

Replaces CPU-expensive `celery inspect ping` subprocess with lightweight
Redis age check. Worker heartbeat writes unix-ts to Redis with 120s TTL;
healthcheck polls every 30s. Fast path, resilient to Redis brief outages.

## [2.63.1] — 2026-04-18 — Sentry + OTel SDKs + compose labels (Observability Phase 5)

Backend `app/observability/` package + frontend `src/lib/sentry.ts` +
`com.barnardhq.*` compose service labels. Structured JSON logging on root
logger and Celery after_setup_signal. Demo overrides pin CHAD-HQ Alloy +
`env=demo`.

## [2.63.0] — 2026-04-18 — Structured JSON logging pre-req (Observability Phase 5)

Replaces plain `logging.basicConfig` with `python-json-logger` on root +
Celery `after_setup_logger` signals. Pre-requisite for Phase 5 instrumentation.
