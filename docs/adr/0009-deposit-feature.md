# ADR-0009 — Two-phase invoice billing (deposit + balance)

**Status:** Accepted (2026-05-03, v2.65.0)
**Owners:** Bill Barnard / Claude
**Extends:** [ADR-0008 — Customer payment gated on mission completion](0008-customer-payment-gated-on-mission-completion.md)
**Sibling:** [ADR-0010 — TOS-acceptance AcroForm](0010-tos-acceptance-acroform.md) (co-shipped)

## Context

The DroneOps Command instance went live with Stripe in v2.64.0 and is
about to take paying customers. The new TOS PDF Bill uploaded
2026-05-03 (`/data/uploads/tos/default_tos.pdf`, Rev 3) explicitly
requires a **50% non-refundable deposit before mobilization** (TOS
§6.2), with operator discretion to waive for **Emergent Services**
(TOS §6.3).

The current `Invoice` model has only `paid_in_full: bool` over the
full total. We cannot honor the TOS we are about to ask customers to
sign — there is no representation of "deposit collected, balance
still owed" anywhere in the data model.

ADR-0008 added a customer-facing visibility gate on `COMPLETED|SENT`
mission status. That gate is correct for the **balance** phase but
explicitly wrong for the **deposit** phase: deposits are due
*before* mobilization, so the gate must allow the customer to see and
pay the invoice well before the mission is delivered.

## Decision

Add a two-phase billing model on the existing `Invoice` row:

1. **Schema** — seven new columns on `invoices`, plus three CHECK
   constraints, all additive with safe defaults so pre-feature rows
   behave exactly as before:

   ```
   deposit_required             boolean NOT NULL DEFAULT false
   deposit_amount               numeric(10,2) NOT NULL DEFAULT 0
   deposit_paid                 boolean NOT NULL DEFAULT false
   deposit_paid_at              timestamp NULL
   deposit_payment_intent_id    varchar(255) NULL
   deposit_checkout_session_id  varchar(255) NULL
   deposit_payment_method       varchar(50) NULL

   CHECK (deposit_amount >= 0)
   CHECK (deposit_amount <= total)
   CHECK (deposit_required = false OR deposit_amount > 0)
   ```

   Migration runs through the existing `_add_missing_columns` mechanism
   in `backend/app/main.py` (this repo has no Alembic). CHECK
   constraints are added via `DO $$ BEGIN ... EXCEPTION WHEN
   duplicate_object THEN NULL; END $$;` so the migration is idempotent
   and standby-promotion-safe.

2. **`payment_phase` is computed**, not persisted. Source of truth
   stays in the seven columns + the joined mission's status. The
   truth table (spec §3.2):

   | `deposit_required` | `deposit_paid` | mission `status` | `paid_in_full` | `payment_phase` |
   |:-:|:-:|:-:|:-:|---|
   | true  | false | any            | false | `deposit_due` |
   | true  | true  | not done       | false | `awaiting_completion` |
   | true  | true  | COMPLETED/SENT | false | `balance_due` |
   | false | (n/a) | not done       | false | `awaiting_completion` |
   | false | (n/a) | COMPLETED/SENT | false | `balance_due` |
   | any   | any   | any            | true  | `paid_in_full` |

   "not done" = anything other than `COMPLETED|SENT`, matching ADR-0008's
   `INVOICE_VISIBLE_STATUSES`. (`DELIVERED` is intentionally NOT a
   billing trigger — operator marks `COMPLETED` after final QA.)

   `app/models/invoice.py:compute_payment_phase` is the single source
   of truth; `Invoice.payment_phase_for(mission_status)` is a thin
   shim. Tests cover all 8 cells of the truth table.

3. **Customer-side endpoints** — three pay endpoints, JWT-authenticated:

   - `POST /api/client/missions/{id}/invoice/pay/deposit` — usable
     any time `deposit_required AND NOT deposit_paid`. Explicitly
     NOT gated on mission status (the whole point of a deposit is
     pre-mobilization).
   - `POST /api/client/missions/{id}/invoice/pay/balance` — gated:
     `mission.status in {COMPLETED, SENT} AND (deposit_paid OR NOT
     deposit_required) AND NOT paid_in_full`.
   - `POST /api/client/missions/{id}/invoice/pay` — back-compat
     alias retained from ADR-0008. Infers phase from current state
     and delegates to the deposit or balance handler. Returns 400
     when phase is `awaiting_completion` or `paid_in_full`. Existing
     bookmarks and any cached UI from before v2.65.0 keep working.

4. **Visibility extension to ADR-0008.** `client_portal.py:get_client_invoice`
   now returns the invoice when EITHER the deposit phase is active
   OR the mission is COMPLETED|SENT. Hidden ONLY when
   `deposit_required=false AND mission not yet COMPLETED|SENT`. The
   ADR-0008 constant becomes one branch of an OR.

5. **Webhook handler upgrade** — `_handle_checkout_completed` now
   reads `metadata.payment_phase` from the session:

   - `deposit` → set `deposit_paid` + sibling columns; idempotent
     (returns early if `deposit_paid` already true).
   - `balance` → existing `paid_in_full` path; idempotent via the
     existing `paid_in_full` flag.
   - **absent metadata** → fall through to the balance branch
     (legacy path; covers any checkout session in flight at the
     v2.65.0 cutover).

   Each branch fires:
   - **ntfy** push to topic `droneops-deposits` (single channel so
     deposit + balance events thread together in the operator's
     notification client). Per ADR-0036 transport. Topic
     registration in `noc-master/data/service-registry.json` is
     orchestrator follow-up — fail-soft path logs and continues if
     the topic returns 401/403 from the ntfy server.
   - **Customer receipt email** — `deposit_received_email.html`
     (new, mirrors `payment_received_email.html` shape; agent C
     performs the deep brand pass) for the deposit branch;
     existing `payment_received_email.html` for the balance branch.
   - **Operator BCC** of the customer receipt to `me@barnardhq.com`
     (no separate operator email path).

6. **Operator UI** — single new control inserted into the existing
   invoice form on the Mission step 5 page (`MissionNew.tsx`):

   - **"Require 50% deposit"** Switch, default checked.
   - Below it, an editable **Deposit Amount** number field. Empty =
     server-fills `round(total * 0.50, 2)` (TOS §6.2). Operator can
     override (e.g., 30%, or fixed $500 retainer); validated against
     the CHECK constraints. After deposit is collected, both
     controls disable (server rejects mutation with 400).

7. **Customer UI** — restructured invoice section on
   `ClientMissionDetail.tsx`:

   - 4-step horizontal **payment-phase progress strip** at the top.
     Reads `payment_phase` directly from the API response.
   - Two-row payment table (Deposit / Balance) with status badges
     and per-row Pay buttons that show only when their phase is
     active. When `deposit_required=false`, the Deposit row is
     suppressed and the Balance row reads "Total".
   - Existing line items below.
   - After Stripe redirect (`?payment=success`), polls
     `GET /api/client/missions/{id}/invoice` every 3s for up to 30s
     or until `payment_phase` changes. Mantine notification flashes
     the phase transition. No SSE/WS — polling is sufficient at
     human-payment cadence and avoids a stateful CF Access connection.

## Failover & resilience evaluation (per repo CLAUDE.md)

1. **Streaming replication?** — Pure additive ALTERs and
   `DO $$ ... EXCEPTION ...` CHECK adds. No PK/FK/index changes; the
   standby will receive the WAL records and apply them transparently.
2. **Container recreation?** — All schema changes run in
   `_add_missing_columns` on every startup, idempotent. No init
   scripts touched, no runtime state introduced.
3. **Blue-green swap?** — The promoted standby runs the same
   idempotent ALTER on first boot. Any in-flight Stripe checkout
   sessions migrate cleanly because the `deposit_checkout_session_id`
   column is present on both sides.
4. **Failover engine?** — No effect — failover engine doesn't read
   invoice state.
5. **Customer-facing during failover?** — Customer-facing `/pay/*`
   endpoints are read-mostly; a failover during the brief Stripe
   redirect window may surface a generic "payment session
   unavailable" message, which is the same experience as today's
   `/pay` endpoint.

**Conclusion:** failover-safe.

## Out of scope (explicit non-decisions)

- **Refunds on cancel** — manual operator action via the Stripe
  dashboard; future ADR can add a "Cancel & refund" button.
- **Recurring billing / saved cards** — not needed for one-time
  mission billing.
- **Per-customer default deposit percentage** — operator overrides
  per-invoice instead.
- **Operator-side deposit collection UI** — already shown on the
  same mission detail page via the same invoice section; no
  separate dashboard.

## Tests

- `backend/tests/test_payment_phase.py` — 18 tests covering all 8
  truth table rows + every MissionStatus + the legacy invoice path.
- `backend/tests/test_deposit_pricing.py` — 16 tests: rounding,
  bounds validation, sum-invariant, defensive `balance_amount`.
- `backend/tests/test_client_portal_pay.py` — 12 tests: deposit any
  time, balance gated, /pay legacy alias phase inference.
- `backend/tests/test_stripe_webhook_phases.py` — 7 tests: each
  phase lands in correct columns, idempotency on duplicate delivery,
  legacy no-metadata fall-through, no-invoice-found logs-and-returns.

All Stripe SDK calls (`stripe.checkout.Session.create`,
`stripe.PaymentIntent.retrieve`, `stripe.PaymentMethod.retrieve`)
are mocked. No live Stripe API calls.

53 new tests. All pass. No regressions in the 48 unrelated existing
backend tests.

## Operator runbook (post-deploy)

1. Rolling restart of the API container picks up the new columns +
   CHECK constraints idempotently.
2. Existing customers default to `deposit_required=false` after the
   migration; flip on per-customer in the operator UI for any
   standard-services customer. Emergent-only customers stay off.
3. **First live test** — mint a portal link for a $1 test mission
   to a personal email. Walk through TOS sign → portal → deposit pay
   (Stripe test card `4242 4242 4242 4242`) → mark mission COMPLETED →
   balance pay → confirm both ntfy pushes arrive on Bill's phone, both
   receipt emails land in inbox.
4. **Second live test** — same but with `deposit_required=false` to
   verify the back-compat single-payment path is unchanged.
5. Watch `doc.stripe` log during the first real customer payment —
   `[STRIPE-WEBHOOK] DEPOSIT RECEIVED ...` and
   `[STRIPE-WEBHOOK] BALANCE PAID ...` confirm signature + idempotency.
