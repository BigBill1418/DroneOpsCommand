# ADR-0040 — Automated download-link delivery on payment-in-full (email + portal)

- **Status:** Accepted
- **Date:** 2026-07-06
- **Relates to:** ADR-0039 (the unpaid-invoice gate this completes — 0039
  withholds, 0040 delivers), ADR-0009 (two-phase Stripe payments — the
  balance-paid webhook is the primary trigger), ADR-0036 (migration 0009
  rides the advisory-locked Alembic boot path).

## Context

ADR-0039 shipped the withholding half of the download-link policy: no link
until the invoice is paid in full. The operator's follow-up direction
(2026-07-06) killed the manual release step: *"I'm not going to go manually
regenerate the report when the client pays — when the client pays they will
get a link in a separate follow-up automated email and it will then populate
in the client portal. Otherwise everything here is automated."*

So payment-in-full must be a **trigger**, not just a gate condition.

## Decision

**One delivery service, three triggers, one dedup stamp, plus portal
exposure — zero operator steps.**

1. **Service:** `app/services/download_link_delivery.py` owns both the
   ADR-0039 gate policy (`download_link_payment_blocked` — the reports
   router and portal import it, so the three surfaces can never drift) and
   `deliver_download_link_if_due()`, which sends the branded
   `download_link_email.html` follow-up and stamps
   `missions.download_link_email_sent_at` (migration
   `0009_mission_dl_email_sent_at`). Fail-soft end to end: a delivery
   failure logs loudly (`[DL-DELIVERY]`) but never breaks the payment flow
   that triggered it, and the stamp is only written after a successful
   send so the next trigger retries.

2. **Triggers:**
   - **Stripe balance-paid webhook** (`_send_balance_notifications`) — the
     normal path: client pays online, receipt email fires (existing), then
     the download-link email fires.
   - **Manual mark-paid** (`PUT /missions/{id}/invoice` with
     `paid_in_full=true` transitioning false→true) — cash/check/Zelle
     payments get the same automation.
   - **Download URL set/changed on the mission** (`PUT /missions/{id}`) —
     covers "payment landed before the footage was ready". A URL *change*
     also **resets the dedup stamp**, so a replacement link (e.g. a
     revoked-and-reminted share) is re-delivered automatically.

3. **Skip conditions** (logged with reason): no URL, already sent,
   non-billable (no payment event exists; the link is already un-gated for
   those via report/portal), not paid in full, link expired
   (`download_link_expires_at` in the past — WARN), no customer email
   (WARN).

4. **Client portal:** `GET /api/client/missions/{id}` now returns
   `download_url` + `download_expires_at` **only when the ADR-0039 gate
   passes** (paid / non-billable / operator override — the override is read
   from the report row; no report row means no override). The portal's
   DELIVERABLES card renders the download button when unlocked; while an
   invoice is unpaid it says the download unlocks on payment. After an
   in-portal Stripe payment confirms, the page re-pulls the mission so the
   link appears without a reload. Note: `Mission.invoice` is
   `lazy="noload"` — the endpoint eager-loads it explicitly or the gate
   would mis-read a billable mission as never-invoiced.

## Consequences

- The full lifecycle is automated: report goes out link-less while unpaid
  (ADR-0039) → client pays (Stripe or manual) → follow-up email with the
  link + portal unlock, immediately and exactly once per link URL.
- The ADR-0039 report-side gate is unchanged; the manual override remains
  the early-release valve and also unlocks the portal.
- Non-billable missions get no payment-triggered email (no payment event
  exists). Their link still flows through report + portal ungated. If a
  send-on-URL-set behavior is ever wanted for non-billable work, extend
  `_delivery_skip_reason` — one line.
- A mission with the URL set but payment never collected simply never
  fires — correct by policy.

## Failover & Resilience Guard self-check

1. Replication: additive nullable column via WAL — safe. 2. Container
recreation: Alembic migration 0009 on the ADR-0036 advisory-locked boot
path. 3. Blue-green: additive column + optional response fields are
compatible across a mixed pair; the dedup stamp prevents double-sends even
if both sides process a webhook replay. 4. Failover engine: untouched.
5. Customer-facing during failover: email send is fail-soft; portal fields
degrade to null.
