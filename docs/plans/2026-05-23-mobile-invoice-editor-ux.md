# Mobile UX redesign — invoice editor (MissionInvoiceEdit)

**Date:** 2026-05-23
**Status:** Implemented
**Scope:** `frontend/src/pages/MissionInvoiceEdit.tsx` only (operator-confirmed
scope — the new-mission wizard's invoice step is intentionally out of scope).

## Problem

The invoice editor was built desktop-first. In the field on a phone (~390px)
the operator could not reliably see or edit the fields:

- Each line item was a single non-wrapping horizontal row (Description / Category
  / Qty 80px / Price 110px / trash). On a phone the fields collapsed to unusable
  widths.
- Hard-coded widths forced horizontal scrolling: template select 280px, deposit
  220px, tax 180px.
- The Save action required scrolling and small touch targets throughout.

## Design

Mobile-first responsive presentation; **no change to data flow, the deposit
auto-50% logic, the save sequence, or the dirty-guard**. Breakpoint:
`useMediaQuery('(max-width: 768px)')`, matching existing app convention.

1. **Line items → card-per-item on mobile.** Extracted `LineItemFields`
   (`frontend/src/components/invoice/LineItemFields.tsx`). On mobile each item is
   a card: full-width Description and Category, Qty + Price side by side (`grow`),
   a divider, and a visible **per-item line total**, plus a labelled "Remove"
   button. On tablet/desktop (>768px) it renders the original single dense row
   (now also showing the per-item line total). Larger input size on mobile.

2. **Sticky summary + save bar on mobile.** A fixed bottom bar shows live
   Subtotal + 50% Deposit and a full-width SAVE INVOICE (plus Cancel), always
   reachable without scrolling. Respects `env(safe-area-inset-bottom)`; the page
   adds bottom padding so content clears it. The top and bottom desktop Save
   buttons are hidden on mobile.

3. **Fluid widths.** Template select, deposit display, and tax input become
   fluid/full-width on mobile; the hard-coded pixel widths apply on desktop only.

## Verification

Playwright screenshots of the real component (dev server, stubbed API + seeded
auth token) at 390 / 412 / 768 / 1280 px, reviewed by eye. Result: no horizontal
overflow at any width (`scrollWidth == clientWidth`), desktop layout unchanged,
mobile fully legible with the sticky save bar pinned. `tsc` clean; existing
`MissionInvoiceEdit` unit tests pass. Final on-device confirmation pending from
the operator.
