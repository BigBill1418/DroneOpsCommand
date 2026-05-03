> **Maintained automatically by NOC doc-autogen.** This file is refreshed twice daily (04:00 + 16:00 UTC) by `~/noc-master/scripts/doc-autogen.py`, which summarizes recent commits via Claude Haiku 4.5 and commits with a `[skip-deploy]` trailer so no container rebuilds are triggered. See [NOC-Master ADR-0013](https://github.com/BigBill1418/NOC-Master-Control-SWARM/blob/main/docs/decisions/ADR-0013-docs-only-deploy-skip.md). Manual edits are preserved — the generator diffs against existing content before writing.

# Changelog

Notable changes to DroneOpsCommand. Dates are absolute (YYYY-MM-DD, UTC).

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
  `test_health_check.py`.

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
