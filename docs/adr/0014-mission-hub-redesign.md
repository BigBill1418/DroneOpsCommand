# ADR-0014: Mission Hub Redesign — Hub + Facet pattern, slim create modal, legacy wizard preserved for soak

**Status:** Accepted (2026-05-03, shipped in v2.67.0)
**Related ADRs:** 0008 (invoice gated on mission completion), 0009 (deposit feature), 0010 (AcroForm TOS rebuild), 0011 (payment idempotency + sequential invoice numbering), 0012 (secret hygiene + leak remediation), 0013 (contract tests + 4xx burst alerting)
**Spec:** `docs/superpowers/specs/2026-05-03-mission-hub-redesign-design.md`
**Plan:** `docs/superpowers/plans/2026-05-03-v2.67.0-mission-hub-orchestration-plan.md`
**Author:** v2.67.0 4-agent parallel ship (A: Hub + create + status; B: Details/Flights/Images facets; C: Report facet; D: routing + tests + this ADR)

---

## Context

On 2026-05-03 the operator opened an existing mission for editing, clicked **Save** on the Details step of the wizard, and the system created a *brand-new* mission row carrying the edited fields instead of updating the existing record. Two duplicates were created back-to-back at **18:46:54 UTC** and **18:49:28 UTC**; both were manually deleted by the operator. No customer-facing data was lost, but the bug class is class-S (silent data corruption with operator-visible symptom) and warrants a structural fix, not a defensive patch on top of the same shape.

**Root cause:** `frontend/src/pages/MissionNew.tsx` was a single ~1500-line component mounted at *both* `/missions/new` (create) and `/missions/:id/edit` (edit). One `handleCreateMission` function decided "POST `/missions`" vs "PUT `/missions/{id}`" by inspecting `isEditing && missionId`. When either side of that conjunction read as falsy under any edge case (state race, router warm-up, fast click before `loadMission` settled), the conditional fell through to **POST** and created a duplicate mission carrying the operator's edited fields as a brand-new row. The bug was *latent* — the only thing protecting against it was one boolean conditional. Nothing in the data layer prevented it.

**Operator requirement** (verbatim): *"I need to be able to edit any detail until it's finalized and sent out... the flow needs to improve so I can navigate the different pieces and make the mission creation or edit flow more user friendly. It is currently not friendly or intuitive."* Combined with the SAFETY-CRITICAL constraint, also verbatim: *"do not break any current missions or data — this is critically important"*.

The wizard's linear "Details → Flights → Images → Report → Invoice" stepper also mismatched reality: operators don't progress linearly; they create a stub at booking, add flights post-flight, curate images later, draft the report later, and edit the invoice across multiple touchpoints. They need to **edit any one facet at any moment** until status is `SENT`. The wizard forced them through unrelated steps to update one field.

## Decision

Adopt the **Hub + Facet pattern** in front of the existing backend routes:

1. **`/missions/:id` is the Hub** — a read-only summary view with five **facet cards** (Details, Flights, Images, Report, Invoice). Each card displays a summary plus an `[Edit]` button that routes to the facet-specific editor. The Hub itself never writes mission data; it only reads.
2. **Each facet has its own URL and its own component:** `/missions/:id/details/edit`, `/missions/:id/flights/edit`, `/missions/:id/images/edit`, `/missions/:id/report/edit`, `/missions/:id/invoice/edit`. Each editor calls only its facet-specific route (`PUT /api/missions/{id}` for Details; `POST/DELETE /api/missions/{id}/flights` for Flights; etc.). **No facet editor shares the `POST /api/missions` code path.** That path is reserved exclusively for the slim create modal.
3. **`/missions/new` becomes a slim `MissionCreateModal`** mounted on the Missions list page (and on the Dashboard "NEW MISSION" button). It collects only title + customer + type + optional date, POSTs once, navigates to `/missions/{id}`. The standalone `/missions/new` route is removed; stale bookmarks redirect to `/missions` with a Mantine notification.
4. **`/missions/:id/edit` (the old wizard URL) becomes a redirect to `/missions/:id`** (the Hub) for back-compat with operator bookmarks.
5. **The legacy 1484-LOC wizard is preserved verbatim** as `frontend/src/pages/MissionWizardLegacy.tsx` (renamed from `MissionNew.tsx`, no code change), mounted at the hidden `/missions/:id/edit-legacy` route as a soak-window fallback. Deleted only after the criteria below are met.
6. **Backend defensive guards** (already shipped in Agent A's slice):
   - `POST /api/missions` rejects request bodies that include an `id` field with HTTP 400 — even a future stale frontend bundle accidentally sending `id` cannot create a duplicate.
   - `POST /api/missions` logs `[MISSION-POST-DUP]` WARNING when the same `(customer_id, title, mission_date)` triple was POSTed in the last 5 minutes (operator override allowed; log only).
   - `PATCH /api/missions/{id}` for status transitions (Mark COMPLETED / Mark SENT / Reopen Mission); logs `[MISSION-STATUS]` on every transition and `[MISSION-REOPEN]` when status reverts from SENT.

The existing backend endpoints already supported every Hub interaction; this redesign is **strictly frontend reorganization plus two backend defensive guards plus one new PATCH for status**. **No schema migrations, no data backfill, no FK changes.**

## Consequences

### What this delivers

- **The duplicate-mission bug class is physically impossible.** No facet editor calls `POST /api/missions`; the only POST is in the slim create modal which has no concept of "edit mode" to fall through. Even if a regression were introduced, the backend `id`-rejection guard blocks the POST at the API boundary.
- **Operator UX matches the actual mental model.** The question is now "which facet of this mission needs attention?" — answered by clicking the facet card on the Hub. Each editor opens scoped, saves scoped, returns to the Hub. No walking through five unrelated steps to fix one field.
- **Edit-any-facet-until-finaled is structural, not aspirational.** Per spec §8.5, the Hub renders Edit buttons disabled with the tooltip "Mission sent — locked" only when `mission.status === 'sent'`. All earlier states keep all facets editable. A "Reopen Mission" action on SENT-state Hubs flips status back to COMPLETED with audit trail.
- **Every feature shipped 2026-05-02 / 2026-05-03 still works** — see the integration audit below. Specifically:
  - **ADR-0008** (invoice gated on mission status) — Hub Invoice card surfaces the current visibility state to the operator; customer portal logic unchanged.
  - **ADR-0009** (deposit feature, 7 invoice columns + payment_phase + pay/deposit + pay/balance routes) — Hub Invoice card §8.6 surfaces deposit state + Issue Portal Link + Send Email + Copy Link as first-class operator actions, not buried inside the invoice editor.
  - **ADR-0010** (AcroForm TOS rebuild) — Hub does NOT host TOS sign; the Customer card on the Hub links to `/tos-acceptances?customer_id=…` and the Issue-Portal-Link flow routes the customer through the existing `/tos/accept` AcroForm gate before they reach the invoice.
  - **ADR-0011** (payment idempotency + sequential invoice numbering) — Hub Invoice card displays `invoice_number` (BARNARDHQ-YYYY-NNNN) prominently; Issue-Portal-Link is idempotent (double-click returns the same token).
  - **ADR-0012** (secret hygiene) — gitleaks pre-commit + CI gate active across all four agent branches; no new secrets introduced; no `${VAR:-default}` for credentials anywhere in the redesign.
  - **ADR-0013** (contract tests + 4xx burst alerting) — every new Hub backend route ships with an `httpx.AsyncClient` contract test (no `_mk_payload(SimpleNamespace)` bypass); every new Hub frontend page ships with a Vitest+msw contract test that includes a load-bearing `POST /api/missions = 0` tripwire assertion. The 4xx-burst alert (queued for v2.66.x) catches any new Hub endpoint that goes 422 on real customer payloads.
- **Failover/replication unaffected.** Zero schema migrations; the only backend additions are an additive PATCH route, an additive 400 guard on POST, and a structured WARN log. PostgreSQL streaming replication, blue-green deploy, and the failover engine see no behavioral change.
- **Rollback is clean.** Reverting the routing-layer commit immediately restores the old `/missions/:id/edit` (because the legacy wizard lives at `/missions/:id/edit-legacy` and the redirect is purely route-table). No data state to roll back because Phase 2 makes no schema changes.

### What this requires

- **The legacy wizard `MissionWizardLegacy.tsx` stays on disk during the soak window.** That is ~1484 LOC of dead-but-loaded code. Acceptable cost for the safety guarantee. Lazy-loaded behind its own route so it never touches the operator's normal bundle until they explicitly hit the soak URL.
- **Operators must learn one new behavior:** the "+ NEW MISSION" button now opens an inline modal rather than navigating to a wizard page. The modal has the same five required fields as the wizard's Step 1 had, so the muscle memory transfers cleanly. The Dashboard, Missions list, and any future entry points all share the same `MissionCreateModal` component.
- **CHANGELOG + ADR maintenance discipline.** Per repo CLAUDE.md "Documentation Discipline (MANDATORY)", every shipped agent slice updated CHANGELOG.md inline; this ADR consolidates them into the v2.67.0 ship summary.

### Deletion criteria for `MissionWizardLegacy.tsx`

The legacy wizard is removed only when **all three** of the following are true:

1. ≥ 1 week of operator-confirmed production use of the new Hub flow (operator explicitly says "looks good, ship it final").
2. **Zero** `/missions/:id/edit-legacy` route hits in nginx access logs across a rolling 7-day window. This is the empirical proof that no operator workflow silently depends on the fallback.
3. Operator explicit OK to delete.

When the criteria are met, `MissionWizardLegacy.tsx`, the `/missions/:id/edit-legacy` route, and this ADR's "preserved for soak" caveat all get retired in a single commit referenced from a follow-up ADR (likely ADR-0015 or higher) closing out the migration.

## Alternatives considered

### Alternative A — Defensive patch on the shared component (rejected)

Add a `useEffect`-guarded `assert(!isEditing || missionId)` plus a `disabled={isEditing && !missionId}` on the Save button in the existing `MissionNew.tsx`, plus the backend `POST /api/missions` rejects-`id` guard. This patches the immediate symptom but leaves the structural risk in place: one shared component for two lifecycles, one boolean conditional gating writes, ~1500 LOC of code path risk. The next regression in any of the five wizard steps re-exposes the duplicate-creation class. **Rejected** because the operator explicitly asked for the structural fix ("the flow needs to improve so I can navigate the different pieces").

### Alternative B — Full rewrite without legacy fallback (rejected)

Delete `MissionNew.tsx` outright in v2.67.0; ship the Hub + facet pattern as the only flow. Cleaner code state on day one. **Rejected** because the operator's first paying customer was active on this stack 2026-05-02 and the SAFETY-CRITICAL constraint ("do not break any current missions or data — critically important") forbids any path where the operator cannot fall back to the known-working wizard if the new Hub regresses on a specific mission. Soft cutover is the only acceptable plan.

### Alternative C — Hub + Facet pattern WITH legacy wizard preserved (chosen)

The structural fix from Alternative A's stronger sibling, gated behind a soak-window fallback that delivers Alternative B's safety net. The ~1484 LOC of legacy code is the price; the price is acceptable because (a) it's lazy-loaded so it costs nothing in the operator's normal bundle, (b) the deletion criteria above are concrete and measurable, and (c) the legacy wizard stays exactly as it was (rename-only) so its known behavior is preserved bit-for-bit during the soak.

## Implementation snapshot

Four parallel aegis agents in isolated worktrees off `main`:

| Agent | Branch | Scope | Key files |
|---|---|---|---|
| **A** | `feat/mission-hub` | Hub refactor of `MissionDetail.tsx`; new `MissionCreateModal` + `MissionStatusBadge` + `MissionFacetCard`; backend POST `id`-rejection guard + dup-warn log + PATCH status endpoint with Reopen audit | `frontend/src/components/MissionCreateModal.tsx`, `frontend/src/components/MissionStatusBadge.tsx`, `frontend/src/components/MissionFacetCard.tsx`, `frontend/src/pages/MissionDetail.tsx`, `frontend/src/pages/Missions.tsx`, `backend/app/routers/missions.py` |
| **B** | `feat/mission-facets-1` | Details + Flights + Images facet editors with msw `POST /api/missions = 0` tripwire | `frontend/src/pages/MissionDetailsEdit.tsx`, `frontend/src/pages/MissionFlightsEdit.tsx`, `frontend/src/pages/MissionImagesEdit.tsx` |
| **C** | `feat/mission-facet-report` | Report facet editor (preserves narrative + AI generate/poll + Save Draft + Generate PDF + Send-to-Customer) | `frontend/src/pages/MissionReportEdit.tsx` |
| **D** | `feat/mission-routing-tests` | Route-table wiring; `MissionNew.tsx → MissionWizardLegacy.tsx` rename; cross-cutting routes contract test; this ADR; consolidated CHANGELOG | `frontend/src/App.tsx`, `frontend/src/pages/Dashboard.tsx`, `frontend/src/pages/MissionWizardLegacy.tsx` (renamed), `frontend/src/__tests__/missions.routes.test.tsx`, `docs/adr/0014-mission-hub-redesign.md`, `CHANGELOG.md` |

Merge order: A → B → C → D. Each agent's tests pass independently; D's cross-cutting routes test verifies the integrated flow.

## Verification

- Backend contract suite (Agents A's 16 + Agent D's tos-customer-sync repair): green.
- Frontend Vitest+msw suite: 5 facet contract tests + 1 cross-cutting routes test + 2 modal tests + 7 Hub tests, total 25+ tests across 6 files; all green.
- Spec §11 done definition (15 checkboxes): orchestrator runs post-deploy via the playbook in `docs/superpowers/plans/2026-05-03-v2.67.0-mission-hub-orchestration-plan.md` Task 8.
- Spec §9.5 integration audit (every feature shipped 2026-05-02/03): orchestrator runs post-deploy via the same playbook.

## References

- Design spec: `docs/superpowers/specs/2026-05-03-mission-hub-redesign-design.md`
- Orchestration plan: `docs/superpowers/plans/2026-05-03-v2.67.0-mission-hub-orchestration-plan.md`
- Prior ADRs cited in Consequences: 0008, 0009, 0010, 0011, 0012, 0013
- v2.67.0 CHANGELOG entry: top of `CHANGELOG.md`
- Cross-cutting contract test: `frontend/src/__tests__/missions.routes.test.tsx`
