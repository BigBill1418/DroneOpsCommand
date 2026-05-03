# Mission Hub Redesign — Design Spec

**Date:** 2026-05-03
**Status:** Approved by operator (Bill, 2026-05-03)
**Triggered by:** duplicate-mission bug (operator opened existing mission for edit, clicked Save on Details step, system created a NEW mission instead of updating). Two duplicates created at 18:46:54 + 18:49:28 UTC; both since deleted by operator.
**Related:** ADR-0009 (deposit feature), ADR-0010 (TOS rebuild), ADR-0011 (payment idempotency), ADR-0013 (contract tests + 4xx alerting).

---

## 1. Why this exists

Today's `frontend/src/pages/MissionNew.tsx` is one ~1500-line component mounted at BOTH:

```
/missions/new            → create
/missions/:id/edit       → edit
```

It implements a **5-step linear stepper wizard** (Details → Flights → Images → Report → Invoice). The same `handleCreateMission` function decides "POST `/missions`" vs "PUT `/missions/{id}`" by inspecting `isEditing && missionId`. When either reads as falsy under any edge case (state race, router weirdness, fast click before `loadMission` settles), the conditional falls through to POST and **creates a duplicate mission** carrying the edited fields as a brand-new record.

Two structural problems made the bug possible:

1. **Shared component for create + edit.** The same code path is responsible for both lifecycles; the only thing protecting against duplication is one boolean conditional.
2. **Linear wizard mismatches reality.** Operators don't create-then-progress-linearly. They:
   - Create at booking (DRAFT) — only need title/customer/type/date/location
   - Add flights later (IN_PROGRESS / PROCESSING) — independent of the rest
   - Curate images later (REVIEW)
   - Draft the report later
   - Build/edit the invoice across multiple touchpoints
   - Need to **edit any single facet at any moment** until status flips to `SENT`

The wizard forces them to walk through unrelated steps to update one field. It also makes "what step am I on?" the primary mental model when the actual operator question is "which facet of this mission needs attention?"

Operator's own words: *"I need to be able to edit any detail until it's finalized and sent out... the flow needs to improve so I can navigate the different pieces and make the mission creation or edit flow more user friendly. It is currently not friendly or intuitive."*

**SAFETY-CRITICAL constraint** (operator's exact wording): "do not break any current missions or data — this is critically important". Every change must be additive on the data layer; no schema migrations that drop or rewrite fields; existing missions and their flights/images/reports/invoices must be readable + editable through the new flow without backfill.

---

## 2. Architecture — Mission Hub + Facet pattern

```
┌─────────────────────────────────────────────────────────────────┐
│  /missions/new (slim modal)                                      │
│    title* + customer + type + date (optional)                    │
│    → POST /api/missions → /missions/:id (the hub)                │
└─────────────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│  /missions/:id        — THE HUB                                  │
│  ─────────────────                                               │
│  Mission status badge ▪ Mark COMPLETED ▪ Mark SENT               │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │ DETAILS              [Edit]                               │  │
│  │ Title · Customer · Type · Date · Location · Description  │  │
│  └──────────────────────────────────────────────────────────┘  │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │ FLIGHTS  (N attached)                          [Edit]     │  │
│  │ Compact list                                              │  │
│  └──────────────────────────────────────────────────────────┘  │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │ IMAGES   (N uploaded)                          [Edit]     │  │
│  │ Thumbnail strip                                           │  │
│  └──────────────────────────────────────────────────────────┘  │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │ REPORT   (status: drafting / sent / not started)  [Edit]  │  │
│  │ Snippet of latest narrative                              │  │
│  └──────────────────────────────────────────────────────────┘  │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │ INVOICE  ($XXX · paid_in_full=N · deposit=$XX)   [Edit]   │  │
│  │ Existing v2.66.0 deposit indicator + Mark SENT button    │  │
│  └──────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                           │
                           ▼
       ┌──────────────┬──────────────┬──────────────┬──────────────┬────────────┐
       │              │              │              │              │            │
       ▼              ▼              ▼              ▼              ▼            ▼
/details/edit   /flights/edit  /images/edit  /report/edit  /invoice/edit  (back to hub)
   focused        focused         focused        focused        focused
   editor         editor          editor         editor         editor
   (PUT only)     (POST/DELETE    (POST/DELETE   (PUT only)     (PUT only —
                   on flights)    on images)                     already exists v2.66.0)
```

Each facet editor is its own page → its own URL → its own component → cannot create a duplicate mission. The facet editors share NO `POST /api/missions` code path. The only POST happens in the slim creation modal.

The Hub itself is purely a **read + navigate** view. It never writes mission data; clicking Edit on any facet routes to that facet's editor. Status transitions (Mark COMPLETED, Mark SENT) happen on the Hub via `PATCH /api/missions/{id}`.

---

## 3. URL design

| Route | Component | What it does |
|---|---|---|
| `/missions` | existing `Missions.tsx` | List page (unchanged) |
| `/missions/new` | NEW `MissionCreateModal.tsx` mounted on `Missions.tsx` | Slim create — title + customer + type + (optional) date. Submit → POST → route to `/missions/:id` |
| `/missions/:id` | existing `MissionDetail.tsx` (heavily refactored to be the Hub) | The Hub — read-only summary cards + Edit buttons + status transitions |
| `/missions/:id/details/edit` | NEW `MissionDetailsEdit.tsx` | Edit basic mission fields (title, customer, type, date, location, description, is_billable, UNAS download link). PUT only. |
| `/missions/:id/flights/edit` | NEW `MissionFlightsEdit.tsx` | Add/remove flights, assign aircraft. POST/DELETE on `/missions/{id}/flights` only (no `/missions` POST). |
| `/missions/:id/images/edit` | NEW `MissionImagesEdit.tsx` | Upload/remove images. POST/DELETE on `/missions/{id}/images` only. |
| `/missions/:id/report/edit` | NEW `MissionReportEdit.tsx` | Narrative + final content + AI generation + PDF render. PUT on `/missions/{id}/report`. |
| `/missions/:id/invoice/edit` | existing `MissionInvoiceEdit.tsx` (v2.66.0) | Already exists; keep as-is. |
| `/missions/:id/edit` | (deprecated alias) `Navigate to="/missions/:id"` | Old wizard URL — redirect to Hub for back-compat. Operator's existing bookmarks still land somewhere sensible. |

The legacy `/missions/:id/edit` route stays as a redirect for back-compat (per the safety-critical constraint). The 1500-line `MissionNew.tsx` itself is **kept but renamed** to `MissionWizardLegacy.tsx` and only mounted via a dev-only `/missions/:id/edit-legacy` route hidden from the nav. If the new Hub flow has any regression, operator can fall back to the wizard. We delete `MissionWizardLegacy.tsx` after a full operator-confirmed soak (≥ 1 week production usage of the new flow).

---

## 4. Backend changes (minimal — primarily defensive)

**No schema migrations. No new tables. No FK changes. No deletions.**

The existing endpoints already support the Hub pattern; the redesign is mostly frontend reorganization:

- `GET    /api/missions/{id}` — already returns full mission including flights, images, report, invoice. Hub uses it. ✓
- `POST   /api/missions` — used ONLY by the new slim create modal. ✓
- `PUT    /api/missions/{id}` — used by Details Edit. ✓
- `PATCH  /api/missions/{id}` — used for status transitions (Mark COMPLETED, Mark SENT). Verify exists; if not, add.
- `POST/DELETE /api/missions/{id}/flights` — used by Flights Edit. ✓
- `POST/DELETE /api/missions/{id}/images` — used by Images Edit. ✓
- `PUT    /api/missions/{id}/report` — used by Report Edit. ✓
- `POST/PUT /api/missions/{id}/invoice` — used by Invoice Edit. ✓

**One defensive backend addition** (the safety net from the brainstorm's option A — still worth shipping even though we're going straight to the Hub):

- **`POST /api/missions` rejects requests where the body contains an `id` field.** Returns 400 with `"detail": "POST /api/missions must not include 'id' in body — use PUT /api/missions/{id} for updates"`. Pure defense in depth: even if a future bug or stale frontend bundle sends `id` to POST, the duplicate-creation class is **physically impossible**.
- **Soft duplicate-detection log line.** When a `POST /api/missions` request arrives, log a WARNING if a non-deleted mission exists with the same `(customer_id, title, mission_date)` triple created within the last 5 minutes. Don't reject (operator may legitimately want two missions for the same customer/title on the same day) — just emit a structured log line that the new ADR-0013 4xx-burst alert can graduate into a real alarm later.

---

## 5. Frontend changes

### 5.1 New files
- `frontend/src/pages/MissionDetailsEdit.tsx` — Details facet editor (~250 LOC, extract from `MissionNew.tsx` Step 1)
- `frontend/src/pages/MissionFlightsEdit.tsx` — Flights facet editor (~300 LOC, extract from Step 2)
- `frontend/src/pages/MissionImagesEdit.tsx` — Images facet editor (~200 LOC, extract from Step 3)
- `frontend/src/pages/MissionReportEdit.tsx` — Report facet editor (~350 LOC, extract from Step 4)
- `frontend/src/components/MissionCreateModal.tsx` — slim create modal (~120 LOC, brand-new minimal)
- `frontend/src/components/MissionStatusBadge.tsx` — shared status pill component (~50 LOC)
- `frontend/src/components/MissionFacetCard.tsx` — shared "section card with Edit button" component used by the Hub (~60 LOC)

### 5.2 Modified files
- `frontend/src/pages/MissionDetail.tsx` — heavily refactored from "view existing detail page" to "Hub". Add the 5 facet cards, Mark COMPLETED button, Mark SENT button. Keep the existing functionality (deposit indicator added in v2.66.0, TOS audit link added in v2.66.3) — those become parts of the Customer / Invoice cards on the Hub.
- `frontend/src/pages/MissionNew.tsx` → renamed to `frontend/src/pages/MissionWizardLegacy.tsx`. **No code changes**, just rename + remove from primary nav. Stays mounted at the hidden `/missions/:id/edit-legacy` route during the soak window.
- `frontend/src/App.tsx` — route table updated per §3.
- `frontend/src/pages/Missions.tsx` — open the new create modal instead of routing to `/missions/new`. List rows still link to `/missions/:id` (the Hub).

### 5.3 Behavioural rules to enforce
- **Each facet editor's "Cancel" returns to `/missions/:id` (the Hub)** — operator never gets stranded mid-edit.
- **Each facet editor's "Save" PUT-or-POST against the facet-specific endpoint, never against `/missions` directly.** A stray `api.post('/missions', ...)` in any facet editor is a bug.
- **The Hub disables status-transition buttons (Mark COMPLETED / Mark SENT) until all editor sub-pages are saved.** No way to lock-out a half-saved facet.
- **Status `SENT` makes every Edit button on the Hub disabled** with tooltip "Mission sent — open in audit/refund flow to modify." (Refund/audit flow is out of scope; for now Edit just won't fire.)

---

## 6. Safety strategy — preserving existing missions and data

The operator was explicit: **do not break any current missions or data**. The following guarantees are baked into the design:

1. **No DB schema migrations.** No `ALTER TABLE`, no column adds/drops, no enum changes. The Hub reads existing mission rows as-is.
2. **No data backfill.** Every existing mission's existing fields display unchanged on the Hub. Customers + flights + images + reports + invoices keep their existing IDs and FKs.
3. **Old wizard route stays as fallback.** `/missions/:id/edit-legacy` mounts the old `MissionWizardLegacy.tsx` (renamed `MissionNew.tsx`) for the soak window. If the new flow regresses on any specific mission, operator clicks the legacy URL and the old flow still works. Removed only after explicit operator OK after ≥ 1 week of production use.
4. **No production cutover until contract tests pass.** Per ADR-0013, every new facet editor ships with an `httpx.AsyncClient` contract test that exercises the real PUT/POST against the real route. No `_mk_payload(SimpleNamespace)` bypass tests. CI must be green before the merge.
5. **Deploy verification.** After the new flow ships:
   - Manually open one DRAFT mission, edit Details, save → confirm it's a PUT, confirm no new mission row created.
   - Manually open one COMPLETED mission, edit Invoice line items, save → confirm PUT, confirm changes persist.
   - Manually open one SENT mission → confirm Edit buttons are disabled.
   - DB row count for `missions` table BEFORE deploy = row count AFTER deploy + the one or two test missions you intentionally create. **Numerical equality is the proof.**
6. **Rollback path.** If anything is wrong post-deploy, revert the merge commit. The legacy wizard is still mounted; reverting the new routes restores the old `/missions/:id/edit` immediately. No data state to roll back because Phase 2 makes no schema changes.

---

## 7. Testing strategy

Per ADR-0013 (post-422-incident standard), every customer-or-operator-data-touching route needs a real-HTTP contract test:

| Layer | Test | Asserts |
|---|---|---|
| Backend unit | `test_missions_post_rejects_id_in_body.py` | POST `/api/missions` with `{"id": "...", ...}` → 400 |
| Backend unit | `test_missions_post_logs_dup_warning.py` | POST creating same `(customer_id, title, mission_date)` within 5min logs WARN |
| Backend integration | `test_missions_patch_status.py` | PATCH `/api/missions/{id}` with `{"status": "completed"}` → 200, row updated |
| Frontend contract | `test_missions_create_modal.tsx` (Vitest + msw) | Modal POST sends NO `id` field, redirects to `/missions/:id` on success |
| Frontend contract | `test_mission_details_edit.tsx` | `details/edit` save calls `PUT /missions/{id}` (asserted via msw); never POST |
| Frontend contract | `test_mission_flights_edit.tsx` | `flights/edit` save calls `/missions/{id}/flights` POST/DELETE; never `/missions` POST |
| Frontend contract | `test_mission_images_edit.tsx` | same for images |
| Frontend contract | `test_mission_report_edit.tsx` | same for report |
| E2E manual | Create → Hub → Details Edit → save | mission count unchanged after save (= no duplicate) |
| E2E manual | Create → Hub → Mark COMPLETED → Mark SENT | status transitions persist |
| E2E manual | Open a SENT mission | Edit buttons disabled |

Coverage gate: **ALL new tests must pass + ALL existing tests must still pass** before merge. No "143 of 145 pass, the other 2 are flaky" — green across the board.

---

## 8. Implementation orchestration

Per the operator's "use all parallel agents" direction, parallel-isolated work:

| Agent | Subagent type | Branch / worktree | Scope | Estimated time |
|---|---|---|---|---|
| **A — Hub + slim create + status transitions** | aegis | `feat/mission-hub` | Refactor `MissionDetail.tsx` to Hub; new `MissionCreateModal.tsx`; new `MissionStatusBadge.tsx` + `MissionFacetCard.tsx`; new `Mark COMPLETED` / `Mark SENT` buttons; backend `PATCH /api/missions/{id}` if missing; defensive `POST /missions` rejects-`id` guard | 60-90 min |
| **B — Facet editors (Details + Flights + Images)** | aegis | `feat/mission-facets-1` | New `MissionDetailsEdit.tsx` + `MissionFlightsEdit.tsx` + `MissionImagesEdit.tsx`. Extract logic from `MissionNew.tsx` Steps 1-3. Each PUT-only or facet-route only — NO `POST /missions`. | 60-90 min |
| **C — Report facet editor** | aegis | `feat/mission-facet-report` | New `MissionReportEdit.tsx` (the Report step is the most complex — narrative, AI gen, PDF, send). Extract from `MissionNew.tsx` Step 4. Must preserve all existing report functionality (Claude/Ollama generate, draft save, PDF render, send). | 60-90 min |
| **D — Routing + legacy preservation + tests** | aegis | `feat/mission-routing-tests` | App.tsx routes per §3; `MissionNew.tsx` → `MissionWizardLegacy.tsx` rename + de-nav; `Missions.tsx` updated to launch create modal; backend contract tests for all routes; frontend Vitest+msw contract tests; ADR-0014 (Mission Hub Redesign). Merges LAST. | 60-90 min |

**Merge order:** A → B → C → D. D ships the routing layer that ties everything together + the test coverage gate. Each merge waits for previous to land and tests to pass.

After all four merge: version bump to **2.67.0** (MAJOR feature, structural UX redesign). New ADR-0014 documents the design. CHANGELOG entry.

---

## 9. Out of scope (explicitly NOT in this redesign)

- **Refund / audit flow for SENT missions.** Edit buttons disabled; refund-via-Stripe-dashboard remains manual.
- **Multi-mission bulk edit.**
- **Mission templates / clone-from-existing.** Future enhancement.
- **Mission archive / soft-delete.** Existing missions can be deleted through the existing endpoints; no UI change here.
- **Operator-side TOS audit page changes.** v2.66.0 Agent C UI is unaffected.
- **Customer portal changes.** All `/client/*` pages unaffected.
- **Schema changes of any kind.** Strictly additive frontend reorg + 2 small backend defensive guards.

---

## 10. ADR target

This redesign warrants its own ADR: **`docs/adr/0014-mission-hub-redesign.md`** — Agent D writes it as part of their scope. Records the structural decision (Hub + Facet pattern over linear wizard), the safety constraints, the legacy-route preservation policy, and the deletion criteria for `MissionWizardLegacy.tsx`.

---

## 11. Done definition (no claim of "done" before all of these are TRUE)

- [ ] All four agent branches merged to `main`
- [ ] Backend contract tests: 100% pass
- [ ] Frontend contract tests: 100% pass
- [ ] Existing test suites: 100% pass (no regressions)
- [ ] Manual E2E: create + edit details + edit flights + edit images + edit report + edit invoice + mark COMPLETED + mark SENT — all observed working
- [ ] DB row-count assertion: `count(missions)` before deploy + N test missions = `count(missions)` after deploy + N test missions (numerical equality proof — no duplicates created during testing)
- [ ] Legacy wizard at `/missions/:id/edit-legacy` still loads and saves correctly
- [ ] Version bumped to v2.67.0 in 4 files
- [ ] CHANGELOG entry appended
- [ ] ADR-0014 written + committed
- [ ] Live deploy verified: `app.version` reads `2.67.0`
- [ ] External smoke probes return expected status codes

If ANY of these is false, the answer is "in progress" not "done". The operator's direction was explicit: **"DO NOT tell me things are done or tested if they are not actually tested and green across the board."**
