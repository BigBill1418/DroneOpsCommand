# Deposit Feature + TOS-Acceptance Rebuild + Themed Customer Portal — Design

**Date:** 2026-05-03
**Status:** Approved (Bill, 2026-05-03)
**Related:** `docs/TOS-Rebuild.md` (full TOS-acceptance subsystem spec), `docs/adr/0008-customer-payment-gated-on-mission-completion.md` (extended by §3 of this design), CHANGELOG entry for v2.64.0 Stripe activation (2026-05-02).

---

## 1. Why this exists

The DroneOps Command instance is about to receive paying customers. Three blockers remain before the customer-facing flow is production-ready:

1. **The TOS PDF Bill uploaded today (`/data/uploads/tos/default_tos.pdf`, Rev 3) explicitly requires a 50% non-refundable deposit before the operator mobilizes** (§6.2), with operator discretion to waive for Emergent Services (§6.3). The current `Invoice` model has no deposit concept — it's a single `paid_in_full` boolean over the full total. We cannot honor the TOS we are about to ask customers to sign.
2. **The TOS acceptance flow itself is being replaced** (per `docs/TOS-Rebuild.md`) with an AcroForm-fill + SHA-256-anchored audit row pattern. The new TOS PDF already carries the seven required AcroForm fields (`client_name`, `client_email`, `client_company`, `client_title`, `signed_at_utc`, `client_ip`, `audit_id`); the backend code to fill, lock, hash, and persist them does not exist yet.
3. **The customer-facing portal pages (`/client/login`, `/client/<jwt>`, `/client/missions/:id`, plus the new `/tos/accept`) are functional but visually generic.** They need to be themed to match the BarnardHQ TOS PDF's brand identity so the customer experience reads as one continuous BarnardHQ artifact rather than three loosely related pages.

This design covers all three. The deposit feature is the load-bearing piece; the TOS rebuild and the theming pass are co-shipped because they touch the same surface and the same customer.

---

## 2. Architecture (one diagram)

```
                           ┌────────────────────────────────────────────┐
       Operator action ──→ │  POST /api/intake/{id}/send-email          │
                           │  → emails customer the intake link         │
                           └────────────────────────────────────────────┘
                                              │
                                              ▼
                           ┌────────────────────────────────────────────┐
       Customer email  →   │  GET  /tos/accept?token=…                  │ ← TOS-Rebuild.md Phase 3
                           │  Reads embedded PDF, types name+email,     │   (this design references; not duplicated)
                           │  checks box, clicks Accept                 │
                           │  POST /api/tos/accept → AcroForm fill+lock │
                           │  → audit row + SHA hashes + email both     │
                           └────────────────────────────────────────────┘
                                              │
                                              ▼
                  Operator issues client portal link (existing flow)
                                              │
                                              ▼
                           ┌────────────────────────────────────────────┐
                           │  GET  /client/<jwt>                        │
                           │  GET  /api/client/missions/{id}/invoice    │ ← extended this design
                           │       returns { deposit_*, balance_*,      │
                           │                 payment_phase, … }         │
                           │                                            │
                           │  POST /api/client/missions/{id}/invoice/   │
                           │       pay/deposit       (any time, if      │
                           │                          deposit_required) │
                           │       pay/balance       (gated: mission    │
                           │                          COMPLETED|SENT    │
                           │                          AND deposit paid) │
                           │  → returns Stripe Checkout URL             │
                           └────────────────────────────────────────────┘
                                              │
                                              ▼
                  Customer completes Stripe Checkout (live mode)
                                              │
                                              ▼
                           ┌────────────────────────────────────────────┐
                           │  POST /api/webhooks/stripe                 │ ← signature verified
                           │  metadata.payment_phase = deposit|balance  │
                           │  → updates Invoice row, fires:             │
                           │    - ntfy push to operator                 │
                           │    - receipt email to customer             │
                           │    - BCC operator on receipt               │
                           └────────────────────────────────────────────┘
                                              │
                                              ▼
                  Portal page polls invoice every 3s for 30s →
                  visual progress strip advances to next phase
```

Three separately-deployable changes, one customer journey:

- **A. Deposit feature** — Alembic migration + Invoice model + 2 new client endpoints + webhook upgrade + ntfy notification + operator UI checkbox + portal payment-phase strip. ADR-0009.
- **B. TOS rebuild** — Per the existing `docs/TOS-Rebuild.md` spec, end to end. Uses the already-uploaded Rev 3 PDF. ADR-0010 (renumbered from the spec's "ADR-0030" to fit this repo's actual ADR sequence).
- **C. Customer portal theming** — `ClientLogin`, `ClientPortal` (= `ClientDashboard`), `ClientMissionDetail`, `TosAcceptance` (new from B), plus the redirect-success view, all re-themed to lift the visual language of the BarnardHQ TOS PDF (Bebas Neue display, Share Tech Mono mono, navy `#003858` and cyan `#189cc6` accents, footer line `BarnardHQ LLC · Eugene, Oregon · FAA Part 107 Certified · barnardhq.com · DOC-001`). All customer-facing emails get the same header/footer treatment.

The three pieces are independent enough to be built by separate parallel agents in isolated git worktrees, then merged in order: B → A → C.

---

## 3. Deposit data model + APIs (the load-bearing piece)

### 3.1 Schema migration

Single new Alembic revision (next free number per `alembic heads`):

```sql
ALTER TABLE invoices
    ADD COLUMN deposit_required boolean NOT NULL DEFAULT false,
    ADD COLUMN deposit_amount   numeric(10,2) NOT NULL DEFAULT 0,
    ADD COLUMN deposit_paid     boolean NOT NULL DEFAULT false,
    ADD COLUMN deposit_paid_at  timestamptz NULL,
    ADD COLUMN deposit_payment_intent_id   text NULL,
    ADD COLUMN deposit_checkout_session_id text NULL,
    ADD COLUMN deposit_payment_method      text NULL;

ALTER TABLE invoices
    ADD CONSTRAINT deposit_amount_nonneg     CHECK (deposit_amount >= 0),
    ADD CONSTRAINT deposit_amount_le_total   CHECK (deposit_amount <= total),
    ADD CONSTRAINT deposit_required_consistent CHECK (
        deposit_required = false OR deposit_amount > 0
    );
```

Both `invoices` rows currently in production (verified 2026-05-03 against `droneops-standby-db`) were created before deposits existed; the defaults make them backwards-compatible (`deposit_required=false`, `deposit_amount=0` → existing `paid_in_full` behavior is unchanged).

### 3.2 Invoice model additions

`backend/app/models/invoice.py` gets the seven new mapped columns matching the schema above.

A computed property `payment_phase` returns one of four string literals — derived, not persisted, so the ground truth always lives in the seven columns + the joined mission's status:

| `deposit_required` | `deposit_paid` | mission `status` | `paid_in_full` | `payment_phase` |
|:-:|:-:|:-:|:-:|---|
| true  | false | any         | false | `deposit_due` |
| true  | true  | not done    | false | `awaiting_completion` |
| true  | true  | COMPLETED/SENT | false | `balance_due` |
| false | (n/a) | not done    | false | `awaiting_completion` |
| false | (n/a) | COMPLETED/SENT | false | `balance_due` |
| any   | any   | any         | true  | `paid_in_full` |

(`mission_status not done` = anything other than COMPLETED or SENT, per ADR-0008's `INVOICE_VISIBLE_STATUSES`.)

### 3.3 Operator-side invoice creation

`POST /api/missions/{id}/invoice` request body gains:

```json
{
  "subtotal": 1000.00,
  "tax_rate": 0,
  "deposit_required": true,           // NEW; defaults to true at the API level (TOS §6.2)
  "deposit_amount": null,             // NEW; null → server fills as round(total * 0.50, 2)
  "line_items": [...],
  ...existing fields...
}
```

When `deposit_required=true` and `deposit_amount=null`, the server computes `round(total * 0.50, 2)` and stores it. Operator can override (e.g., 30% deposit, or fixed $500 retainer); validated against the CHECK constraints above. When `deposit_required=false`, server forces `deposit_amount=0` regardless of input.

**Operator UI** — the existing invoice-creation/edit form on the mission-detail page (`frontend/src/pages/MissionDetail.tsx` invoice section). Frontend agent may relocate if a clearer spot exists, but no UI rewrite — just two new controls inserted into the existing form:

- New checkbox **"Require 50% deposit"** — default checked. Below it, an editable amount field pre-filled with `total * 0.50` and a small italic note: *"TOS §6.2 default. Uncheck for Emergent Services per TOS §6.3."*

### 3.4 Customer-side endpoints (extends ADR-0008)

ADR-0008's gate logic in `client_portal.py:get_client_invoice` is amended:

```
visibility_rule:
  invoice is shown to the customer when EITHER
    (1) deposit_required AND NOT deposit_paid          -- they need to pay the deposit
    OR
    (2) mission.status in {COMPLETED, SENT}            -- existing ADR-0008 rule
  hidden only when:
    deposit_required = false AND mission.status NOT in {COMPLETED, SENT}
```

The response `ClientInvoiceResponse` is extended with the deposit fields above plus `payment_phase`. Line items are returned in all visible states.

Two new pay endpoints, both POST, both client-JWT authenticated:

- **`POST /api/client/missions/{id}/invoice/pay/deposit`**
  - 400 unless `deposit_required AND NOT deposit_paid AND deposit_amount > 0`
  - Creates a Stripe Checkout session for `deposit_amount` with `metadata.payment_phase = "deposit"` and `metadata.invoice_id`/`metadata.mission_id`
  - Persists `deposit_checkout_session_id`
  - Returns `{ "checkout_url": "https://..." }`

- **`POST /api/client/missions/{id}/invoice/pay/balance`**
  - 400 unless `mission.status in {COMPLETED, SENT} AND (deposit_paid OR NOT deposit_required) AND NOT paid_in_full`
  - Creates Stripe Checkout session for `(total - deposit_amount)` with `metadata.payment_phase = "balance"`
  - Persists `stripe_checkout_session_id` (existing column, repurposed for balance — deposit gets its own column per 3.1)
  - Returns `{ "checkout_url": "https://..." }`

The pre-existing `POST /invoice/pay` is retained as an **alias** that infers phase from current state — keeps any old bookmarks/links working through the cutover.

### 3.5 Webhook handler upgrade

`backend/app/routers/stripe_webhook.py:_handle_checkout_completed` reads `event.data.object.metadata.payment_phase`:

- **`deposit`** — set `deposit_paid=true`, `deposit_paid_at=now()`, `deposit_payment_intent_id`, `deposit_payment_method` (resolved from PaymentIntent → PaymentMethod, same code path as today). Fire `[CLIENT-PAY] DEPOSIT RECEIVED` log line. Trigger:
  - **ntfy push** to topic `droneops-deposits`, priority `high`, title `[DroneOps Command] Deposit received — Mission '{title}' — ${amount}`, click URL `https://droneops.barnardhq.com/missions/{id}`.
  - **Receipt email** to customer using new `deposit_received_email.html` template (themed per §5).
  - **BCC** of the receipt email to `me@barnardhq.com` (operator confirmation).
- **`balance`** — set `paid_in_full=true`, `paid_at`, etc. (existing behavior, unchanged). Fire existing `[STRIPE-WEBHOOK] Invoice ... marked PAID`. ntfy push to same `droneops-deposits` topic with title `[DroneOps Command] Balance paid — Mission '{title}' — ${amount}` (different prefix, same channel so it threads). Email = existing `payment_received_email.html`.
- **No `payment_phase` metadata** (e.g., a webhook from a pre-deposit-feature checkout session): fall back to existing balance behavior. Backward-compatible.

Idempotency: each branch checks the relevant `*_paid` flag first and returns early if already set, so a duplicate webhook delivery never double-fires the notification.

### 3.6 Front-end invoice surface

`ClientMissionDetail.tsx` invoice section is restructured:

- A 4-step horizontal **payment-phase progress strip** at the top: `Deposit Due → Awaiting Mission Completion → Balance Due → Paid In Full`. Current step lit cyan; completed steps dim green; future steps grey. Strip reads `payment_phase` directly from the API response.
- Below the strip, a **two-row payment table**:
  - Row 1: "Deposit (50% — TOS §6.2)" / `${deposit_amount}` / Status badge (`Due` / `Paid 2026-05-15`) / Pay button (only when `deposit_due`).
  - Row 2: "Balance" / `${balance_amount}` / Status badge (`Awaiting completion` / `Due` / `Paid`) / Pay button (only when `balance_due`).
  - When `deposit_required=false`, Row 1 is suppressed entirely; Row 2 reads "Total" instead of "Balance".
- Below the payment table, the **line-item invoice** as today, just re-themed.

After Stripe redirect (`?payment=success`), the page polls `GET /api/client/missions/{id}/invoice` every 3s for up to 30s (or until `payment_phase` changes), then stops. A toast notification flashes confirming the phase transition. No SSE/WebSocket — polling is sufficient at customer-payment cadence and avoids a stateful connection through CF Access.

---

## 4. TOS-acceptance subsystem

Implemented per `docs/TOS-Rebuild.md` end-to-end. That document is the canonical spec; this section only enumerates the deltas needed to fit it into this repo:

- **ADR number:** the spec says ADR-0030; renumber to **ADR-0010** to match this repo's actual ADR sequence (current head is 0007, ADR-0008 shipped today as part of v2.64.0, deposit feature claims 0009, TOS rebuild is 0010).
- **Migration revision:** the spec's `0030_tos_acceptances` Alembic revision becomes whatever the next free revision is at implementation time (run `alembic heads` first, per the spec's Phase 0).
- **Frontend route base:** the spec uses `command.barnardhq.com`; correct hostname is `droneops.barnardhq.com`.
- **AcroForm validation gate (Phase 2.9):** must be added to the existing `POST /api/intake/upload-default-tos` and `POST /api/intake/{customer_id}/upload-tos` endpoints. The Rev 3 PDF Bill uploaded today already validates; older PDFs without the seven fields are rejected.
- **Customer-portal-facing changes:** the new `/tos/accept` page must be added to the CF Access bypass app `e2d36c3f-d4a5-40e8-a669-23eb89b15863` (extend `destinations` to include `droneops.barnardhq.com/tos/*` and `droneops.barnardhq.com/api/tos/*` — well within the 5-destination cap).
- **Intake email link target:** `send_intake_email` (`backend/app/services/email_service.py`) currently builds `f"{frontend_url}/intake/{token}"` — change to `f"{frontend_url}/tos/accept?token={token}&customer_id={customer_id}"` so the customer lands on the new TOS page first.

Everything else in `TOS-Rebuild.md` is implemented as written.

---

## 5. Customer portal theming pass

Visual brand carried directly from the TOS PDF onto every customer-facing page and email:

- **Typography:** Bebas Neue (display, all headings, all-caps), Share Tech Mono (data — amounts, audit IDs, dates), Inter or system sans (body).
- **Palette:** background `#0e1117` (existing operator dark), card `#161b22`, brand navy `#003858`, brand cyan `#189cc6` (NOT the existing operator cyan `#00d4ff` — TOS uses the more saturated `#189cc6`), success green `#28a850`, danger red `#dc3545`. Borders `#1f2937`.
- **Footer line** on every customer-facing page: `BarnardHQ LLC · Eugene, Oregon · FAA Part 107 Certified · barnardhq.com · DOC-001` in Share Tech Mono, dimmed.
- **Email templates** (existing + new): same header strip with BarnardHQ wordmark on dark, body white-on-light card, footer line above. Five templates touched: `intake_email.html`, `client_portal_email.html`, `payment_received_email.html`, the new `deposit_received_email.html`, and the new `signed_tos_attached_email.html` (from TOS-Rebuild.md §2.7).
- **Pages re-themed:** `ClientLogin.tsx`, `ClientPortal.tsx`, `ClientDashboard.tsx`, `ClientMissionDetail.tsx`, the new `TosAcceptance.tsx` (from TOS-Rebuild.md §3.1), and the post-Stripe-redirect success view.

The frontend-design agent owns this pass. Operator-side pages are out of scope — they keep their existing look. The dividing line is the URL prefix: `/client/*` and `/tos/accept` get the TOS-PDF brand treatment; everything else stays as-is.

---

## 6. Notification matrix

| Event | ntfy topic | priority | title format | click URL |
|---|---|---|---|---|
| Deposit paid | `droneops-deposits` | `high` | `[DroneOps Command] Deposit received — '{title}' — ${amount}` | `…/missions/{id}` |
| Balance paid | `droneops-deposits` | `high` | `[DroneOps Command] Balance paid — '{title}' — ${amount}` | `…/missions/{id}` |
| TOS signed | `droneops-tos` | `default` | `[DroneOps Command] TOS signed — {client_email} — audit {audit_id}` | `…/customers/{id}` |

ntfy topics added to `~/noc-master/data/service-registry.json` and obscured-fallback list (`data/ntfy-fallback-topics.yml`) per ADR-0036 cookbook. Both topics use the existing `NTFY_DRONEOPS_PUBLISHER_TOKEN`.

---

## 7. Privacy / public-repo audit (parallel concern)

The DroneOps repo is public on GitHub. This design pass is the moment to verify nothing personal/sensitive has leaked. A read-only general-purpose agent runs in parallel during implementation, checking:

- Real customer emails / names / phone numbers in any committed file (especially `CHANGELOG.md`, `docs/`, scripts, fixtures).
- API keys, webhook secrets, JWT signing keys (anything matching `sk_live_`, `pk_live_`, `whsec_`, `xkeysib-`, `xsmtpsib-`, `cfat_`, `cfut_`, `ghp_`, `EXPO_TOKEN`, etc.).
- Internal hostnames or IPs that shouldn't be public (HSH-HQ home WAN `69.9.133.92`, internal WG `10.99.0.x`).
- Bill's personal email / address strings.
- Any `.env*` accidentally committed.

If anything is found: produce a remediation patch (`git rm --cached` + `.gitignore` updates + scrub from the file, with rotation of any leaked credential noted as an operator action). Run also against the staged changes for this design's implementation BEFORE merging.

---

## 8. Testing strategy

| Layer | Location | What it asserts |
|---|---|---|
| Unit | `backend/tests/test_deposit_pricing.py` | `deposit + balance == total`; default 50% rounding; CHECK constraints |
| Unit | `backend/tests/test_payment_phase.py` | All 8 truth-table rows for `payment_phase` |
| Unit | `backend/tests/services/test_tos_acceptance.py` | (per TOS-Rebuild.md §5) round-trip, lock bits, hash anchoring |
| Integration | `backend/tests/test_client_portal_pay.py` | Two-phase pay flow: deposit any time; balance gated; webhook idempotent |
| Integration | `backend/tests/test_tos_accept_endpoint.py` | POST accept → row + file + email path |
| Webhook | `backend/tests/test_stripe_webhook_phases.py` | Signed event with each `payment_phase` value lands in correct slot, fires correct ntfy |
| Manual smoke | (operator script) | Test card `4242 4242 4242 4242` → real deposit → mark mission COMPLETED → real balance |
| Privacy audit | n/a (agent task) | No PII / secrets in any committed file |

Coverage target: every new or modified backend code path has at least one test. The frontend theming pass is verified by **visual smoke** (load each `/client/*` page in a browser, confirm the brand cohesion) — no automated visual-regression rig in this repo today and adding one is out of scope.

---

## 9. Out of scope

Explicitly NOT in this design — separate work, future ADRs:

- **Automatic refund handling per TOS §6.5 cancellation policy.** Manual operator action via the Stripe dashboard for now. A future ADR can add a "Cancel & refund" button on the operator's mission page.
- **Recurring billing / subscriptions / saved cards.** Stripe supports it; we don't need it for one-time mission billing.
- **Deposit refund-on-mission-cancel automation.** Operator decides per-case (TOS sliding scale).
- **Per-customer default deposit percentage** (option C from brainstorming). Operator overrides per-invoice instead.
- **Operator-side TOS-acceptance review UI.** TOS-Rebuild.md adds the audit table; surfacing it in the operator UI is a follow-up. For now, query via `psql` or the existing settings page.
- **Receipt PDF generation.** Stripe sends its own receipt; no need to duplicate.

---

## 10. Implementation orchestration

Three parallel agents in isolated git worktrees. Branch protection: each branch is reviewed and merged sequentially in the order **B → A → C** (TOS rebuild lands first because the deposit feature's intake email change references `/tos/accept`; the theming pass lands last because it touches files the other two also touch).

| Agent | Subagent type | Branch / worktree | Scope |
|---|---|---|---|
| **B. TOS rebuild** | aegis | `feat/tos-acroform-acceptance` | Implement `docs/TOS-Rebuild.md` end-to-end. ADR-0010. |
| **A. Deposit feature** | aegis | `feat/invoice-deposit` | §3 of this spec. ADR-0009. |
| **C. Portal theming** | webdev-engineer (or frontend-design) | `feat/customer-portal-theming` | §5 of this spec. No new ADR (visual change). |
| **D. Privacy audit** | general-purpose, background | (read-only) | §7. Reports findings; remediation PRs only if anything is actually found. |

After all three feature branches merge, a single integration test pass confirms: real customer → TOS sign → portal → deposit → mission COMPLETED → balance → all notifications fire and all rows are correct. Documented in CHANGELOG as v2.65.0 (MINOR for the new deposit + TOS subsystems).

---

## 11. Operator runbook (post-deploy)

1. **Confirm the new TOS PDF is the Rev 3 with all seven AcroForm fields.** (Already verified 2026-05-03: `/data/uploads/tos/default_tos.pdf`, 158826 bytes, fields present.)
2. **Set the deposit default per customer profile.** Existing customers have `deposit_required=false` after the migration; flip on for any standard-services customer. Emergent-only customers stay off.
3. **First live test:** mint a portal link for a $1 test mission to a personal email. Walk through TOS sign → portal → deposit pay (Stripe test card `4242 4242 4242 4242`) → mark mission COMPLETED → balance pay → confirm both ntfy pushes arrive on Bill's phone, both receipt emails land in inbox.
4. **Second live test:** same but with `deposit_required=false` to verify the back-compat single-payment path is unchanged.
5. **Watch `doc.stripe` log** during the first real customer payment — `[STRIPE-WEBHOOK] DEPOSIT RECEIVED ...` and `[STRIPE-WEBHOOK] BALANCE PAID ...` confirm webhook signature + idempotency.
