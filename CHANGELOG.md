> **Maintained automatically by NOC doc-autogen.** This file is refreshed twice daily (04:00 + 16:00 UTC) by `~/noc-master/scripts/doc-autogen.py`, which summarizes recent commits via Claude Haiku 4.5 and commits with a `[skip-deploy]` trailer so no container rebuilds are triggered. See [NOC-Master ADR-0013](https://github.com/BigBill1418/NOC-Master-Control-SWARM/blob/main/docs/decisions/ADR-0013-docs-only-deploy-skip.md). Manual edits are preserved — the generator diffs against existing content before writing.

# Changelog

Notable changes to DroneOpsCommand. Dates are absolute (YYYY-MM-DD, UTC).

## [2.68.0] — 2026-05-25 — feat(missions): lead-source attribution (ADR-0016)

Answers the operator's question "how much of my job revenue came from the website."
Nothing previously recorded where a job originated.

- **Schema:** two additive nullable columns on `missions` — `source VARCHAR(50)`
  (allowed: `website`/`referral`/`repeat_client`/`phone`/`social`/`other`, NULL =
  unknown) and `source_ref VARCHAR(255)` (optional external lead id). Applied via the
  repo's idempotent `_add_missing_columns()` startup hook (the repo has no Alembic
  env), matching the ADR-0009 additive-nullable pattern. Failover-safe: standby
  promotion runs the same idempotent ALTER; no PK/FK/index/enum-type change.
- **API:** `source` + `source_ref` added to `MissionCreate` / `MissionUpdate` /
  `MissionResponse` (validated against the `MissionSource` enum → 422 on bad values,
  stored as the plain value not a PG enum). `GET /api/financials/summary` gains a
  `revenue_by_source` block — collected (`paid`) + billed (`total`) revenue and
  mission count per source, sorted by collected desc, NULL → `unknown`. Each summary
  `missions` row now carries `source`.
- **Frontend:** "Lead Source" dropdown on the mission create modal and details
  editor; "Collected Revenue by Lead Source" panel on Financials.
- **Backfill:** "Bella / Banks Missing Dog" (mission `11083323…e46f`, invoice
  `BARNARDHQ-2026-0002`, $1,216.36 paid) set to `source='website'` — it came in
  through the barnardhq.com contact form.
- **Tests:** new `test_mission_source_attribution.py` (9 tests) covering schema
  validate/serialize, enum coercion, and the `revenue_by_source` rollup. Full suite:
  290 passed (2 pre-existing Stripe-probe env failures unrelated).

## [unreleased] — 2026-05-25 — fix(pdf-preview): default report PDF zoom 120% → 60%

`PdfViewer` opened report previews at 120% (`scale=1.2`), too zoomed-in. Default
is now 60% (`scale=0.6`); the reset-zoom control matches. Within the existing
0.5–3.0 zoom range; frontend-only.

## [unreleased] — 2026-05-25 — fix(report-gen): Celery async-loop bug broke AI report generation

**Bug:** AI report generation was flaky/failing. The Celery tasks that run async
DB work (`generate_report`, `send_payment_reminders`) spun up a fresh event loop
per invocation but reused the **module-global `async_session`**, whose asyncpg
connection pool is bound to the *previous* task's now-closed loop. So every such
task after the worker's first raised `RuntimeError: got Future attached to a
different loop` / `Event loop is closed`. `generate_report` (bind=True) only
recovered on Celery's 15s retry and hard-failed after `max_retries=3`;
`send_payment_reminders` has no retry, so the daily dunning sweep silently failed
(the Banks reminder due 2026-05-26 would not have fired).

**Fix:** new `app/tasks/async_db.py` (`new_task_loop_session()` /
`task_event_loop()`) gives each task its own fresh loop + a task-local **NullPool**
async engine (no cross-loop connection reuse), disposed on exit. Both affected
tasks now use it. `send_report_email` was already safe (async SMTP, no DB session)
and is unchanged. 3 regression tests pin the fresh-loop / NullPool / not-global-engine
invariants. Full suite: 279 passed (2 pre-existing Stripe-probe env failures unrelated).

**Also — per-instance model selection:** `claude_model` is now read from DB system
settings (mirroring `anthropic_api_key`), falling back to config. This instance is
set to **`claude-opus-4-7`** (top-tier) for client-facing reports; managed customer
instances keep the config default (`claude-sonnet-4-6`) unless their own DB overrides.
Opus 4.x deprecated the `temperature` parameter (the API 400s if sent), so the
Claude call now gates `temperature=0.3` behind `_supports_temperature(model)` —
omitted for Opus 4.x, kept for Sonnet/Haiku. (This surfaced only after the loop fix
stopped masking it.)

**Failover / blue-green / replication impact:** none — worker-side task plumbing +
a read-only settings lookup; no schema change, no migration.

## [unreleased] — 2026-05-24 — fix(business-signals): tz-aware vs naive datetime zeroed every windowed metric

**Bug:** `GET /api/v1/business-signals` computed its 30/90-day window bounds with
`datetime.now(timezone.utc)` (tz-**aware**), then compared them against
`paid_at` / `updated_at` / `created_at` / `mission_date` columns, which are
stored tz-**naive** (`datetime.utcnow()`). asyncpg rejects that comparison
(`DataError: can't subtract offset-naive and offset-aware datetimes`), so every
windowed query raised and was silently swallowed by `_safe_scalar` → the
endpoint always returned `0` / `null` for `invoice_paid_usd`, `missions_*`,
`new_customers`, etc. Project J.A.R.V.I.S. (the consumer) has been getting
zeroed innovation signals, and the value was unusable for the marketing
revenue bridge (which routed around it via `financials/summary`).

**Fix:** new `_utc_windows()` helper returns the `(now, now-30d, now-90d)` triple
as tz-**naive** UTC (matching the columns), coercing any tz-aware input to naive
UTC defensively. The endpoint uses it. `generated_at` now carries an explicit
`Z` suffix. Verified live on BOS-HQ: `invoice_paid_usd` (30d) `0 → 1216.36`,
`missions_completed` `null → 1`.

**Failover / blue-green / replication impact:** none. Read-only query fix; no
schema change, no writes, no migration. Both blue and green versions read the
same data correctly post-fix; safe to deploy under the standby-first flow.

**Tests:** `tests/test_business_signals_windows.py` pins the naive-UTC invariant
(3 tests). Full suite: 276 passed (2 pre-existing Stripe-probe env failures
unrelated to this change).

## [unreleased] — 2026-05-24 — fix(nginx): re-resolve backend DNS per request so backend rebuilds don't 502 the API

**Incident (resolved):** after rebuilding + recreating the `backend` container, the
`frontend` nginx kept proxying `/api/` to the backend's **old** container IP and
returned **502 for the entire API** (`connect() failed (111: Connection refused)
... upstream: http://172.19.0.11:8000`, while the live backend was at `172.19.0.9`).
The customer portal surfaced this as "link expired or invalid" because the SPA treats
a failed `/api/client/auth/validate` call as an invalid token. Immediate recovery was
an `nginx -s reload`.

**Root cause:** `nginx.conf` used `proxy_pass http://backend:8000;` with no `resolver`
directive, so nginx resolved the `backend` name **once at startup** and pinned the IP.
Any backend container recreate (every image rebuild / real deploy) changes that IP and
strands nginx on the dead address until it is reloaded.

### Fixed

- **`frontend/nginx.conf`** — added `resolver 127.0.0.11 valid=10s ipv6=off;` (Docker
  embedded DNS) and `set $backend_upstream backend;` at server scope, and switched all
  six `proxy_pass` directives to `http://$backend_upstream:8000`. Using a variable in
  `proxy_pass` forces nginx to re-resolve the name per request (cached 10s), so it picks
  up a recreated backend's new IP within 10s instead of 502-ing until a manual reload.
  Requires a frontend image rebuild to take effect.

## [unreleased] — 2026-05-24 — fix(client-portal): correct broken `/client/missions/` redirect route + sign-off

**Two latent bugs in the customer-facing portal URLs**, surfaced while verifying the
dunning pay-link. The frontend page route is `/client/mission/:missionId` (singular)
and the magic-link route is `/client/:token` — but two backend code paths emitted a
**plural** `/client/missions/{id}` URL that matches no route, so the browser fell
through to the operator login screen.

### Fixed

- **`backend/app/routers/client_portal.py`** — `_client_redirect_urls` (the Stripe
  Checkout `success_url` / `cancel_url`) used the plural `/client/missions/{id}` route
  → a customer returning from a successful payment landed on the operator login instead
  of their mission page. Also the cancel URL sent `?payment=cancelled` while the
  frontend only handles `?payment=cancel`. Both corrected to
  `/client/mission/{id}?payment=success|cancel`.
- **`backend/app/services/dunning.py`** — `_build_pay_url`'s no-token fallback used the
  same broken plural route. (The happy path already returns the correct
  `/client/{jwt}` magic-link; only the edge-case fallback was wrong.) Now
  `/client/mission/{id}`.

### Changed

- **dunning email templates** — sign-off changed from the templated
  `{{ company_name }} {{ company_tagline }}` to a fixed **"Bill Barnard — BarnardHQ"**
  in both `payment_reminder_email.html` and `payment_final_notice_email.html`.

## [unreleased] — 2026-05-24 — style(dunning): theme the reminder/final-notice emails to match the project

The dunning emails shipped as bare 14-16 line layouts (logo + name only, hard-coded
teal/red, no table structure) — off-brand next to the rest of the project's emails.
Rebuilt both `payment_reminder_email.html` and `payment_final_notice_email.html` on
the same template as `payment_received_email.html`: dark `#0e1117/#161b22` theme, the
shared `_brand_header.html` / `_brand_footer.html` partials, `brand_accent_color`
(cyan) for the reminder and red for the final notice, a mono amount-due card, the
gold pay CTA, and a past-due badge on the final notice. Render-verified with the
branding dict the send functions already pass (wordmark, amount, pay link, FAA
footer all present).

## [unreleased] — 2026-05-24 — fix(dunning): await email sends instead of nesting run_until_complete

**Bug (confirmed + reproduced):** the dunning sweep runs inside
`loop.run_until_complete(_run())` from the `send_payment_reminders` Celery
task, but `DunningSender` then called `self._loop.run_until_complete(<email
coro>)` on that already-running loop — raising `RuntimeError: This event loop
is already running`. The per-invoice `try/except` in `run_dunning_sweep`
swallowed it, so the feature silently sent nothing.

### Fixed

- **`backend/app/services/dunning.py`** — made the send path async/await
  end-to-end. `process_invoice` is now `async` and `await`s the sender
  methods; `DunningSender.send_reminder/send_final/send_operator` are `async`
  and `await` the email coroutine directly; the `loop` constructor param and
  `self._loop` field are gone. `run_dunning_sweep` dropped its `loop`
  parameter and `await`s `process_invoice` and the no-customer-email operator
  send. Decision logic, stamping, guards, and the summary are unchanged.
- **`backend/app/tasks/celery_tasks.py`** — `send_payment_reminders` now calls
  `await run_dunning_sweep(db)`; the outer `new_event_loop()` /
  `run_until_complete(_run())` wrapper (the correct one) is unchanged.
- **`backend/tests/test_dunning.py`** — the three `process_invoice` tests are
  now `@pytest.mark.asyncio` and `await` the call; `_FakeSender`'s send methods
  are `async`. `due_stage`/`amount_due` tests stay synchronous.

## [unreleased] — 2026-05-24 — fix(invoices): tax-rate unit (100× bug) + atomic save for legacy wizard

Follow-ups to the bulletproofing work — neither affected billing today (all
invoices use 0 tax) but both closed for robustness.

### Fixed

- **Tax-rate unit (100× overcharge, latent):** the editor's "Tax Rate (%)"
  field loaded/saved the raw stored fraction, so typing "8.5" stored 8.5 and
  the backend computed `subtotal × 8.5` = 850% tax. The field is now a true
  percent — loads ×100 (0.085 → 8.5), saves ÷100 (8.5 → 0.085) — matching the
  fraction convention every consumer already used (backend multiply, PDF/Stripe
  `×100` display, Numeric(5,4)). No data migration needed (0 nonzero tax rows).
  (`frontend/src/pages/MissionInvoiceEdit.tsx`.) The editor's live deposit
  preview now also includes tax (50% of subtotal+tax), matching the backend.
- **Legacy mission wizard** (`MissionWizardLegacy.tsx`) now saves line items via
  the atomic `PUT /invoice/items` endpoint instead of the old per-item
  delete-then-add loop — the last non-transactional save path is gone.
- **`backend/tests/test_deposit_pricing.py`** — test pinning the fraction
  convention: tax_rate 0.085 on $1000 → tax $85, total $1085, deposit $542.50.

Verified end-to-end (Playwright): tax field shows 8.5% for a stored 0.085,
deposit preview reads $542.50, and the save sends `tax_rate: 0.085` via the
atomic items endpoint. Backend suite 259 pass; frontend type-check/build clean.

## [unreleased] — 2026-05-23 — fix(invoices): bulletproof totals — recompute-at-charge + atomic line-item save

**Incident:** The stored invoice `total` repeatedly went stale vs. the line
items (observed live: line items summing to $1,204.30 / $1,216.36 while the
stored total read $544.30 / $556.36). Root cause: the editor saved line items
via a non-transactional delete-each-then-add-each loop; an interruption
(flaky field connection) left the items written but the total reflecting only
a partial set. A stale total would then drive the wrong Stripe charge —
critical for an operator who bills a deposit pre-mission and adds line items
after.

### Fixed

- **`backend/app/routers/invoices.py`** — new `PUT /{mission_id}/invoice/items`
  replaces ALL line items + recalculates the total in a single request (one
  DB transaction via `get_db`), so items and total can never desync. Replaces
  the per-item delete/add loop.
- **`backend/app/routers/client_portal.py`** — `_load_pay_context` (the
  chokepoint for every `/pay/*` endpoint) now recomputes the total from the
  current line items before any Stripe charge, so a stale stored total can
  never be billed; it self-heals the row at the same time. A paid deposit
  stays locked, so adding line items after the deposit only grows the balance.
  Checkout-session reuse is now amount-aware — a session minted for a
  now-changed amount is never reused (no stale-price charge).
- **`frontend/src/pages/MissionInvoiceEdit.tsx`** — the editor save now calls
  the atomic replace endpoint instead of the delete/add loop.
- **`backend/tests/test_deposit_pricing.py`** — regression test: paid deposit
  stays locked and the balance grows correctly when line items are added after.

Full backend suite (258 pass) + frontend type-check clean. Follow-ups tracked:
the same atomic save for the legacy mission wizard, and the latent tax-rate
unit bug.

## [unreleased] — 2026-05-23 — fix(mission-hub): readable facet cards on mobile (Invoice card was garbled)

On a phone, the Mission Hub's Invoice card was unreadable — the title stacked
one letter per line and the summary collapsed into a ~3-character column on the
left. Root cause: `MissionFacetCard` laid out summary and actions side-by-side
in a `wrap="nowrap"` row. The Invoice card is the only one with `extraActions`
(Issue Link / Email / Edit), so on a narrow screen those buttons claimed the
width and squeezed the `flex:1, minWidth:0` summary to a sliver.

### Fixed

- **`frontend/src/components/MissionFacetCard.tsx`** — on mobile (≤768px) the
  card stacks vertically: title + summary full-width, action buttons in a
  wrapping row beneath. Desktop keeps the side-by-side layout (unchanged).
- **`frontend/src/pages/MissionDetail.tsx`** — the invoice card's portal action
  group now wraps (`wrap="wrap"`) so Issue Link / Email / Edit reflow on narrow
  screens instead of overflowing.

Verified with Playwright at 390px (Invoice card now fully readable — title,
`Total/Deposit/Balance`, and buttons all legible) and 1280px (desktop unchanged);
no horizontal overflow at either width.

## [unreleased] — 2026-05-23 — feat(invoices): show billed-time quantity as hours on the client invoice

The "Hours" labelling now carries through to the client-facing invoice so
fractional billed time is unambiguous to the customer, not just the operator.

### Changed

- **`backend/app/templates/report_pdf.html`** — billed-time line items render
  the quantity with an "hrs" suffix (e.g. `2.1 hrs`); the quantity is also
  formatted to drop trailing zeros (`290.00` → `290`, `2.10` → `2.1`).
- **`backend/app/schemas/client_portal.py`** + **`backend/app/routers/client_portal.py`**
  — `ClientInvoiceLineItem` now carries `category` so the portal can label by line type.
- **`frontend/src/pages/client/ClientMissionDetail.tsx`** — the client portal
  invoice table shows `N hrs` for billed-time lines, the bare quantity otherwise.

Verified: PDF cell renders `2.1 hrs` / `2 hrs` / `0.25 hrs` (billed-time) and clean
`290` (travel); backend suite + frontend type-check pass. Stripe already folds
fractional quantities into the unit amount, so the charged amount is correct.

## [unreleased] — 2026-05-23 — feat(invoices): label billed-time line quantity "Hours"

Line items in the `billed_time` category are priced per hour, so the quantity
field now reads **"Hours"** (instead of the generic "Qty") on both the mobile
and desktop editor, making fractional billed time (e.g. 2.1) unambiguous. Other
categories keep "Qty". Label is derived from `item.category`, so it updates live
if the category changes. (`frontend/src/components/invoice/LineItemFields.tsx`.)

## [unreleased] — 2026-05-23 — fix(invoices): decimal hours in line-item Qty (e.g. 1.5h, 2.1h)

Operator needs to bill fractional billed-time (1.5 hours, 2.1 hours). The Qty
field accepted decimals but had no `decimalScale`, so it allowed 3+ decimals
(e.g. `2.151`) that the DB's `Numeric(10,2)` then rounds — the displayed/computed
total could disagree with the saved value. Its `onChange` also snapped an
emptied field back to `1`, fighting clear-and-retype.

### Fixed

- **`frontend/src/components/invoice/LineItemFields.tsx`** — Qty now has
  `decimalScale={2}` (matches the DB exactly, so typed = stored = computed) and
  an `onChange` that handles empty/partial input without snapping to 1. Verified:
  1.5h→$225.00, 2.1h→$315.00, 0.25h→$37.50, and a stray 3rd decimal (2.151) caps
  to 2.15→$322.50. Backend already stored/computed quantity as a float; no
  backend change needed.

## [unreleased] — 2026-05-23 — feat(invoices): mobile-first invoice editor (MissionInvoiceEdit)

The invoice editor was desktop-first and unusable in the field on a phone —
line items were a single non-wrapping row and several inputs had hard-coded
widths that forced horizontal scrolling. Redesigned the presentation layer
(no change to data flow, the deposit logic, or the save sequence).

### Changed

- **`frontend/src/components/invoice/LineItemFields.tsx`** (new) — line items
  render as a card-per-item on mobile (full-width stacked fields, Qty/Price side
  by side, a visible per-item line total, a labelled Remove button) and as the
  original single dense row on tablet/desktop (>768px), now also showing the
  per-item line total.
- **`frontend/src/pages/MissionInvoiceEdit.tsx`** — `useMediaQuery` drives the
  responsive layout; a sticky bottom bar on mobile shows live Subtotal + 50%
  deposit and a full-width SAVE (always reachable, respects safe-area inset);
  template select / deposit / tax widths are now fluid on mobile. Desktop layout
  unchanged.

Verified with Playwright at 390/412/768/1280px (reviewed by eye): no horizontal
overflow at any width, desktop unchanged. Design: `docs/plans/2026-05-23-mobile-invoice-editor-ux.md`.

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
- `pytest` (full backend suite) — **240 passed, 1 skipped, 2 pre-existing failures**.
