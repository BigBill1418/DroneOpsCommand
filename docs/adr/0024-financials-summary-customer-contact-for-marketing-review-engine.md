# ADR-0024 — Expose customer contact fields on `/api/financials/summary` for the marketing review engine

- **Status:** Accepted
- **Date:** 2026-06-20
- **Supersedes / Amends:** Extends [ADR-0016](0016-mission-source-attribution.md) (the `missions[]` detail rows on the financials summary).

## Context

The BarnardHQ marketing stack runs a service-revenue bridge
(`marketing/api/droneops-financials.js`, `startDroneopsFinancialsScheduler`)
that polls DroneOpsCommand's `GET /api/financials/summary` hourly and stores
the response in `droneops_financial_snapshots.raw_json`. The bridge consumes
`summary.missions[]` — one row per billable mission — to drive the Overview /
Sales dashboards.

Marketing now wants to fire a **post-job Google review request** to the
customer after a completed mission. Each `missions[]` row already carries
`id, title, mission_type, mission_date, location, customer_name, source,
invoice_total, paid, invoice_number` — but **no way to reach the customer**.
There is no email or phone on the row.

The contact data already exists in DroneOpsCommand: `Customer.email` and
`Customer.phone` are columns on the customer model, and `Mission.customer` is
already eager-loaded in the financials summary query
(`selectinload(Mission.customer)` in `_billable_mission_load_options`). The
fields are one attribute access away — no new data source, no extra query.

## Decision

Add two fields to each `missions[]` detail row in
`backend/app/routers/financials.py`:

- `customer_email` — `mission.customer.email if mission.customer else None`
- `customer_phone` — `mission.customer.phone if mission.customer else None`

Both pull from the already-loaded customer relationship. No loader change, no
schema change, no migration.

## PII posture (this is the load-bearing part)

This is **customer PII leaving the ops system** for marketing
review-solicitation. The constraints:

- **Minimal.** Only the two contact fields needed to send a review request.
  No address, no notes, no portal data — those stay inside DroneOpsCommand.
- **Not logged.** The endpoint does not log the contact values. (It never
  logged row contents; this preserves that.)
- **Opt-out honored downstream.** The marketing review engine is responsible
  for honoring per-customer opt-out / suppression before sending any review
  request. DroneOpsCommand is the source of contact data, not the consent
  authority for review messaging.
- **Same trust boundary as today.** The endpoint is already JWT-gated and the
  bridge already pulls `customer_name` + full financials over the same
  service-account session on the BOS-HQ internal network
  (`http://10.99.0.4:8000`, behind CF Access on the public host). This adds
  two fields to an already-authenticated, already-customer-identifying payload.

## Consequences

- **Additive and backwards-compatible.** Existing consumers (the Financials
  dashboard, the revenue bridge) ignore unknown keys; nothing breaks.
- **Null-safe.** A billable mission with no attached customer record, or a
  customer with no email/phone on file, emits `null` for the respective
  field — never raises. Marketing must skip rows where `customer_email` is
  null (no address = no review request). This is the documented gotcha.
- **No performance cost.** `Mission.customer` is already eager-loaded; this
  reads two attributes off an in-memory object. Zero additional queries, so
  the ADR-0019/P0-1 OOM-avoidance loader contract is untouched.

## Verification

TDD: `backend/tests/test_mission_source_attribution.py` gains
`test_summary_mission_row_carries_customer_contact` (present customer →
email + phone surface) and `test_summary_mission_row_null_customer_yields_null_contact`
(no customer / no email on file → null, no raise). Verified live against the
deployed endpoint that the row now carries `customer_email` for a real recent
mission.
