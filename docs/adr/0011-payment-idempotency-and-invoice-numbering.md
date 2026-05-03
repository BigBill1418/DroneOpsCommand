# ADR-0011 — Payment idempotency, sequential invoice numbering, and webhook signature alerting

- **Status:** Accepted
- **Date:** 2026-05-03
- **Version:** 2.66.0
- **Related:** ADR-0009 (deposit-feature), ADR-0010 (TOS AcroForm), ADR-0036 (ntfy notifications)

## Context

The v2.65.x payment surface (deposit + balance + customer portal Pay
buttons) shipped without three pieces of insurance:

1. A customer who double-clicks **Pay** on `/client/missions/<id>` mints
   two Stripe Checkout sessions against the same invoice. Stripe will
   accept payment on either; the operator can end up with a customer
   who paid twice and a refund to issue.
2. `Invoice.invoice_number` exists on the model but is always written as
   `NULL`. Tax-invoice / accounting export needs a stable sequential
   identifier per year (state/CPA convention: `BARNARDHQ-2026-0001`).
3. A failed Stripe webhook signature returns HTTP 400 silently. If
   Stripe rotates the webhook secret and the System Settings table
   falls behind, every payment event is dropped and the operator finds
   out only when a customer asks why their invoice still shows unpaid.

(3) is also addressed in this ADR even though it is technically a
notification policy decision; it exists at the same layer as (1) and
(2) and lands in the same v2.66.0 cut.

## Decisions

### 1. Pay endpoint idempotency (Fix 4)

`/api/client/missions/<id>/invoice/pay/deposit` and `/.../pay/balance`
each call `_reuse_existing_checkout_session(...)` BEFORE creating a new
Stripe Checkout session. Reuse criteria, all required:

- The invoice's `deposit_checkout_session_id` (deposit branch) or
  `stripe_checkout_session_id` (balance branch) is non-NULL.
- `stripe.checkout.Session.retrieve(<id>)` returns
  `payment_status != "paid"`.
- `session.created` is within the last **30 minutes**.

If all three hold, the existing session URL is returned. Otherwise a
fresh session is minted exactly as before.

**Why 30 minutes?** Stripe sessions are valid for 24 h. Reusing for
24 h would mean a customer who comes back the next day after the
operator updated line items would pay the stale price. 30 min is long
enough to cover real double-clicks plus a coffee break, short enough
to enforce re-pricing on next-day visits.

**Why not just check the local DB?** The webhook hasn't necessarily
arrived yet when a double-click lands, so `invoice.deposit_paid` may
still be False even though Stripe knows the session is paid. Stripe is
the authority. Network failure on the freshness probe falls through to
"mint fresh" — preferable to blocking the customer.

### 2. Sequential invoice numbering (Fix 5)

Invoice numbers follow `BARNARDHQ-YYYY-NNNN`:

- `YYYY` is the current UTC year.
- `NNNN` is a 4-digit zero-padded counter; values >9999 widen
  naturally (no overflow surprise).
- Year-prefix resets every Jan 1 by virtue of using a separate counter
  row per year.

Counter is held in `system_settings` (one row per year, e.g.
`invoice_number_counter_2026`). The atomic increment uses a single
PostgreSQL statement:

```sql
INSERT INTO system_settings (key, value) VALUES (:k, '1')
ON CONFLICT (key) DO UPDATE
  SET value = (CAST(system_settings.value AS BIGINT) + 1)::TEXT
RETURNING value;
```

This is the same atomicity primitive sequences provide, without the
operational overhead of managing per-year PostgreSQL sequences. It is
safe under concurrency (PG guarantees the row-level lock for the
ON CONFLICT branch).

**Backfill:** the two pre-existing invoices on prod (dev/test) keep
`invoice_number = NULL`. They were never sent to a customer; renumbering
them after the fact would produce confusing audit gaps. Going forward
every new invoice gets a number.

### 3. Stripe webhook signature failure alert (Fix 2)

`SignatureVerificationError` in `routers/stripe_webhook.py` now fires
an `urgent` ntfy alert (priority=2) on topic `droneops-deposits` BEFORE
raising the 400, with:

- Title: `[DroneOpsCommand] STRIPE WEBHOOK SIGNATURE FAILED — payments
  may be silently dropping`.
- Body: includes the truncated `Stripe-Signature` prefix + payload
  byte count + diagnostic context.
- Click URL: `https://noc-mastercontrol.barnardhq.com/status/droneops`
  (ADR-0036 tier-3 fallback — there's no Stripe-specific record page
  to point at).
- Dedup: Redis-backed, key `stripe-webhook-sig-failed`, **5-minute
  cooldown** so a flood of bad webhooks doesn't spam.

Severity is `urgent` per ADR-0037 §rubric: customers may be paying
right now and the system is silently dropping events. This is exactly
the "wake Bill at 3 a.m." class.

`ValueError` (malformed payload) does NOT alert — that's a
malformed-input signal, not a misconfigured-secret signal.

Alert helper failure is fail-soft: the 400 still returns to Stripe
even if ntfy is unreachable.

## Implementation

| File | Change |
|---|---|
| `backend/app/routers/client_portal.py` | Added `_reuse_existing_checkout_session` + wired into deposit/balance pay endpoints |
| `backend/app/routers/invoices.py` | Added `_next_invoice_number` + auto-allocation in `create_invoice` |
| `backend/app/routers/stripe_webhook.py` | Added urgent ntfy alert in `SignatureVerificationError` branch |

No schema migrations required — `Invoice.invoice_number` already
exists. The counter rows materialize on first invoice creation per
year via the upsert.

## Failover & resilience

Per repo CLAUDE.md guard:

1. **Replication?** No — pure additive logic in the routers + idempotent
   row writes in `system_settings`. No PK/FK/index changes.
2. **Container recreate?** Yes — counter rows live in PG (replicated).
3. **Blue-green swap?** Counter writes go to the primary; standby
   replicates them. Promotion does not regress sequence.
4. **Failover engine?** No interaction.
5. **Customer-facing during failover?** Idempotency reduces the chance
   of double-charge during the brief read-only window of a manual
   promotion.

## Rejected alternatives

- **PostgreSQL native SEQUENCE for invoice numbers.** Considered. Not
  used because per-year reset is harder (would need DDL via
  `_add_missing_columns`, and SEQUENCE values are not reusable across
  failover without explicit `setval`). The system_settings approach
  is simpler and integrates with existing row replication.
- **Idempotency-Key on Stripe Checkout creation.** Stripe supports it
  but ties idempotency to a window we don't control; reusing the
  customer's existing in-flight session URL is more direct and avoids
  charging a customer for a session they never see.
- **Severity = `high` on signature failure.** Rejected per ADR-0037 §rubric:
  customer-visible payment loss is `urgent`-class.
