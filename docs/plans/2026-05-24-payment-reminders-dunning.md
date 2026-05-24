# 2026-05-24 — Automated payment reminders (dunning) — design spec

- **Status:** Approved (design); ready for implementation plan
- **Owner:** Bill / Claude
- **Scope:** DroneOpsCommand backend. Phase 1 = email. Phase 2 (later) = SMS.

## Goal
When an invoice has been billed to the customer and isn't paid in full, automatically:
- **+48h unpaid** → one **gentle reminder email** to the customer.
- **+7 days unpaid** → a **firmer "final notice" email** to the customer **and** an **escalation email to `bill@barnardhq.com`** so Bill can intervene.
No further automated customer contact between day 2 and day 7 (two customer touches total).

## Behavior (rules)
- **Clock anchor — `billed_at`:** the timestamp the invoice was first sent to the
  customer (when the client portal link is first emailed / mission moves to
  `sent`). There is no such field today → add it; set it once, on the first send,
  never overwritten.
- **48h stage** (`billed_at + 48h ≤ now < billed_at + 7d`, `reminder_sent_at` is
  null): send the gentle reminder email (greeting, amount still due, the pay
  link, a soft nudge). Stamp `reminder_sent_at`.
- **7-day stage** (`now ≥ billed_at + 7d`, `final_notice_sent_at` is null): send
  the customer the firmer final-notice email (references ToS §6.4 late terms +
  the pay link), AND send the operator escalation email to `bill@barnardhq.com`
  (`INV-<number> · 7 days overdue · $<amount due> · <customer name/email>`, link
  to the mission/invoice). Stamp `final_notice_sent_at`. If the invoice reached
  day 7 without a 48h reminder ever firing, skip straight to the 7-day stage.
- **Stop conditions:** `paid_in_full = true` (or mission cancelled) → no sends;
  the next sweep simply skips it. Already-stamped stages never re-send.
- **Missing contact:** no customer email on file → skip the customer email and
  note it in the operator escalation (so Bill knows to reach out manually).

## Architecture (chosen)
Daily **Celery-beat sweep** + **reminder-state columns on `invoices`**. The sweep
queries due invoices, sends the appropriate stage, and stamps the timestamp —
idempotent (safe to re-run; never double-sends).
*Rejected:* a separate `invoice_reminders` table (over-built for two touches);
per-invoice delayed tasks scheduled at bill-time (fragile across broker restarts,
hard to cancel on payment).

### Components
1. **Data model — `app/models/invoice.py` + idempotent `ALTER` in `app/main.py`**
   (mirrors the ADR-0009 deposit-column pattern). Add to `invoices`:
   - `billed_at TIMESTAMP NULL`
   - `reminder_sent_at TIMESTAMP NULL`
   - `final_notice_sent_at TIMESTAMP NULL`
2. **`app/services/dunning.py`** — the testable business logic:
   - `due_stage(invoice, now) -> None | "reminder" | "final"` — pure function
     deciding which stage (if any) is due, given `billed_at`, the two sent
     stamps, `paid_in_full`, and thresholds. Unit-testable without DB/Celery.
   - `run_dunning_sweep(db)` — query eligible invoices, for each compute
     `due_stage`, send via the channel layer, stamp, commit; per-invoice
     try/except so one failure can't abort the sweep; returns a summary.
3. **Channel seam (Phase-2-ready)** — `notify_customer(invoice, stage)` sends the
   stage email today; SMS slots in here later (gated on `customer.phone` + a
   provider). Phase 1 implements email only.
4. **`app/tasks/celery_tasks.py`** — `@celery_app.task(name="send_payment_reminders")`
   calling `run_dunning_sweep`, plus a `beat_schedule` entry running **once daily
   at 16:00 UTC (~9am Pacific)**. (Existing tasks: `check_device_silence`,
   `finalize_key_rotations` — same pattern.)
5. **Set `billed_at`** at the first client-portal-link send (`client_portal.py`
   client-link/send endpoint, where `[CLIENT-LINK-SEND]` logs): set `billed_at`
   only if currently null.
6. **Email templates** (`app/templates/`, jinja, via existing `email_service.py`
   SMTP + branding): `payment_reminder_email.html` (gentle) and
   `payment_final_notice_email.html` (firm). Operator escalation = a short
   templated/plain email to `bill@barnardhq.com`.

### Eligibility & "amount due"
Eligible = `billed_at IS NOT NULL` AND `paid_in_full = false`, joined to a
non-cancelled mission, customer has an email. (`paid_in_full = false` is the gate
— don't use `balance_amount > 0`, since the stored `balance_amount` is the
*post-deposit* balance and would mis-read a deposit-unpaid invoice as $0 owed.)

"Amount due" shown in the email = the next amount the portal will actually charge:
`deposit_amount` when `deposit_required AND NOT deposit_paid`, otherwise
`balance_amount`. This matches what the pay link collects, so the email figure
and the Stripe charge always agree.

## Error handling
- Per-invoice `try/except` inside the sweep — log and continue; return
  `{reminders, final_notices, escalations, skipped, errors}` and log the summary.
- Email-send failures are logged and do NOT stamp the timestamp (so the next
  sweep retries) — except the customer-email-missing case, which is a skip, not
  an error.

## Testing
- `due_stage` unit tests: before 48h → None; 48h–7d, unsent → "reminder";
  reminder already sent → None until day 7; ≥7d unsent → "final"; ≥7d with final
  sent → None; paid_in_full → None at every point; reached day-7 with no prior
  reminder → "final" (skip gentle).
- Idempotency: running the sweep twice sends each stage once.
- Stop-on-paid and no-customer-email-skip paths.
- Mirror the repo's hermetic test style (pure-function `due_stage`; the sweep
  tested with constructed invoice objects / mocked send).

## Out of scope (Phase 2+)
- SMS channel (needs a provider decision — Twilio etc.) — the channel seam is the
  only Phase-1 accommodation.
- Repeated reminders / configurable cadence, late-fee auto-application,
  customer opt-out management.
