# ADR-0039 — Unpaid-invoice download-link gate (with per-report operator override)

- **Status:** Accepted
- **Date:** 2026-07-05
- **Relates to:** ADR-0036 (Alembic single-path — migration 0008 rides that
  boot path), the client-portal payment-phase machinery
  (`Invoice.payment_phase_for`), ADR-0015 (the other report send-time gate —
  audience leak; this ADR follows its "gate at the choke point, surface in
  the editor" shape).

## Context

The mission-footage download link (`missions.download_link_url`, typically a
UI Drop / file-share URL) was embedded into the report PDF and the report
email whenever the operator ticked the per-report `include_download_link`
checkbox. **Nothing checked payment.** On 2026-07-02 a report went out to a
customer (River M., invoice BARNARDHQ-2026-0005, $400.50, unpaid) with the
footage link included — the deliverable was released with zero dollars
collected, and the only remedy was revoking the share at its source.

The operator's standing policy, stated 2026-07-05: **clients do not get a
download link until the invoice is paid in full**, and the behavior must be
controllable from the mission system.

## Decision

**Server-side payment gate at a single choke point, fail-closed, with a
deliberate per-report operator override.**

1. **Choke point.** Both exposure paths (PDF render `POST /report/pdf`, email
   send `POST /report/send`) build the link exclusively via
   `_build_download_link()` in `backend/app/routers/reports.py`, which
   consults `_download_link_payment_blocked()`. Neither path can drift
   around the gate.

2. **Policy** (`_download_link_payment_blocked`):
   - override set → **release** (operator's deliberate call);
   - mission not billable → release (nothing to collect);
   - billable with **no invoice row** → **withhold** (fail-closed: nothing
     has been collected; create/pay the invoice or override);
   - invoice `paid_in_full` → release;
   - invoice `total <= 0` → release (mirrors the payment-links section:
     nothing to collect);
   - otherwise (billable, invoiced, unpaid) → **withhold**.

3. **Override** is a new column `reports.download_link_payment_override`
   (bool, NOT NULL, default false; migration
   `0008_report_dl_payment_override`). It is settable **only** via
   `PUT /report` — never via the generate request — so a report regeneration
   can never reset or trip it. Every flip is audit-logged with the acting
   user (`[DL-PAYMENT-GATE] override set to ...`).

4. **Operator visibility.** `GET/PUT /report` responses carry a computed
   `download_link_payment_blocked` flag. The report editor
   (`MissionReportEdit.tsx`) shows a yellow alert ("link withheld — invoice
   not paid in full") plus the orange override switch whenever the link is
   requested and payment is outstanding. The send response returns
   `download_link_withheld` so the "Sent" toast says explicitly when the
   client did NOT get the link. Withholding never blocks the report itself —
   the client still receives the report; only the footage link is held.

5. **Deposit ≠ paid.** A paid deposit does not release the link; only
   `paid_in_full` does. This is deliberate — the deliverable is the final
   leverage for the balance.

## Known residual — stale PDFs

The PDF embeds (or omits) the link at **render time**. A PDF rendered while
the link was permitted, then sent after the invoice becomes/remains unpaid,
still carries the baked-in link. The send path warn-logs this case
(`[DL-PAYMENT-GATE] ... regenerate the PDF`). The normal editor flow
(generate PDF → send in one sitting) makes the window small; the one known
pre-gate PDF (River) had its `pdf_path` invalidated in prod so the next send
forces a fresh, gated render. Auto-regeneration inside send was rejected as
duplicating the heavy PDF endpoint logic for a corner the warn-log covers.

## Alternatives considered

- **Gate = hard 4xx on send while unpaid.** Rejected: the report (minus
  link) is legitimate customer communication; blocking it conflates the
  deliverable with the correspondence.
- **Gate at the frontend checkbox only.** Rejected: client-side-only
  enforcement is not enforcement; the 2026-07-02 leak was exactly a manual
  flag doing policy work.
- **Auto-uncheck `include_download_link` while unpaid.** Rejected: loses the
  operator's intent — with the gate, the checkbox can stay on and the link
  releases automatically the moment the invoice is paid (next PDF/send).

## Failover & Resilience Guard self-check

1. Replication: additive column via WAL — safe. 2. Container recreation:
schema change is an Alembic migration on the ADR-0036 advisory-locked boot
path — survives. 3. Blue-green: additive column + defaulted Pydantic fields
are backward/forward compatible across a mixed pair. 4. Failover engine:
untouched. 5. Customer-facing during failover: no new external dependency;
the gate is pure in-process logic.
