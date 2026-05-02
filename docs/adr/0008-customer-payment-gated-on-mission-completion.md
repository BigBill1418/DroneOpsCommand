# ADR-0008 — Customer payment gated on mission completion

**Status:** Accepted (2026-05-02, v2.64.0)
**Owners:** Bill Barnard / Claude

## Context

The client portal (`v2.57.x` milestone) shipped two customer-facing
endpoints that touch invoices:

- `GET  /api/client/missions/{mission_id}/invoice` — render the
  invoice in the portal.
- `POST /api/client/missions/{mission_id}/invoice/pay` — create a
  Stripe Checkout session and return the URL.

Stripe was wired up live on 2026-05-02 (see
`CHANGELOG.md` 2026-05-02 entry + `noc-master/CHANGELOG.md` for the
CF Access bypass). With Stripe live, both endpoints would happily
expose and charge an invoice the moment one existed in the DB,
regardless of where the underlying mission was in its lifecycle
(`DRAFT → SCHEDULED → IN_PROGRESS → PROCESSING → REVIEW → DELIVERED
→ COMPLETED → SENT`).

Operator workflow today: **invoices are drafted on the back end as
the mission progresses** (line items added during PROCESSING /
REVIEW), then the mission is marked COMPLETED once the work is
delivered to the customer's satisfaction.

The gap: a customer holding a portal magic link could see a
work-in-progress invoice (or be prompted to pay it) before the
operator had finished the work. That contradicts the customer's
TOS expectation — payment is asked for *after* delivery, not
during.

## Decision

Add a mission-status gate on the **customer-facing** invoice
endpoints only:

```python
INVOICE_VISIBLE_STATUSES: frozenset[MissionStatus] = frozenset({
    MissionStatus.COMPLETED,
    MissionStatus.SENT,
})
```

- `get_client_invoice` returns `None` (the same shape it returns when
  no invoice exists at all) for missions outside that set, with a
  `[CLIENT-INVOICE] HIDDEN` audit log line.
- `create_client_payment` returns `400` with the message
  *"This invoice is not yet available for payment. Your operator will
  mark the mission complete once the work is finished."* for missions
  outside that set, with a `[CLIENT-PAY] BLOCKED` audit log line.

**Operator-side endpoints are intentionally unchanged.**
`POST /api/missions/{id}/invoice` (and the rate-template / line-item
edit endpoints) continue to work for any mission status. The
operator can still build and refine the invoice during PROCESSING /
REVIEW; only the customer surface is gated.

## Why these two states, not more

`MissionStatus` enum values:

| State | Meaning | Customer-facing? |
|---|---|---|
| `DRAFT` | mission stub | no |
| `SCHEDULED` | booked | no |
| `IN_PROGRESS` | actively flying | no |
| `PROCESSING` | post-flight processing | no |
| `REVIEW` | internal review | no |
| `DELIVERED` | report delivered to client | borderline |
| `COMPLETED` | operator marks closed | **yes** |
| `SENT` | final paperwork sent | **yes** |

`DELIVERED` was deliberately excluded from the visible set even
though the report has reached the client by then — the operator may
still intend to revise the invoice (add overage hours, dispute
items) before "closing" the mission with `COMPLETED`. Promoting
`DELIVERED` to the visible set would re-open the
work-in-progress-charge problem.

If practice shows customers want to see and pay as soon as they have
the report (i.e., at `DELIVERED`), expand the set in one place — the
constant is the contract.

## Why a gate on the customer side, not on operator invoice creation

Bill (operator) explicitly requested: "I want to still be able to
build the invoice though on the back end as things progress, just
can't go out or be paid until marked complete." Locking
invoice-creation behind a status check would break the in-flight
workflow (operator drafts $/hr on IN_PROGRESS, finalizes during
PROCESSING, marks COMPLETED to release).

The gate-on-customer-side design also means **a single status flip
is the public-release moment** — no separate "publish invoice"
button to forget. Marking `COMPLETED` releases the invoice to the
customer atomically.

## Consequences

- **TOS implication:** the operator can promise customers in writing
  that no payment is requested until the work is marked complete.
  The system enforces it; not just convention.
- **Logging:** every blocked attempt leaves an audit log
  (`[CLIENT-INVOICE] HIDDEN ...` or `[CLIENT-PAY] BLOCKED ...`) with
  the customer ID, mission ID, and current status. Useful for
  investigating "why can't I pay?" support questions.
- **No frontend change required.** The invoice card disappears
  cleanly because `GET /invoice` returns `None` — same code path
  the SPA already handles for "no invoice yet".
- **Stripe webhook is unaffected.** The `checkout.session.completed`
  handler still fires whenever a real Stripe payment completes; the
  mission-status gate only governs who can *initiate* a Checkout
  session.

## Alternatives considered

- **Operator-side gate (reject `POST /invoice` until status is
  COMPLETED).** Rejected — breaks Bill's draft-during-mission
  workflow.
- **Two booleans on invoice (`drafted` / `released`).** Considered
  briefly. Rejected because a single mission-status flip is simpler
  and matches the operator's existing mental model.
- **Whitelist `DELIVERED` in addition to `COMPLETED` / `SENT`.**
  Available if usage data later shows operators are marking
  `DELIVERED` and not following up to `COMPLETED` quickly enough.
  Easy to add — flip one constant.
