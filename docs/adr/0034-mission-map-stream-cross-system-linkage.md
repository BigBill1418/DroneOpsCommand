# ADR-0034: Minimal cross-system linkage — Mission ↔ Map job ↔ EyesOn stream

- **Status:** Proposed
- **Date:** 2026-07-03
- **Deciders:** Bill Barnard (operator)
- **Related:** DroneOpsMap `Job.mission_id` "Phase 1 mission spine"; ADR-0016
  (DOC `source`/`source_ref` — marketing attribution, **not** cross-system);
  CallSign ADR-0018 & ADR-0020 (the auto-chain firewall — see §Constraints).
- **Excludes:** CallSign. This ADR does not touch, reference, or integrate the
  public-safety product in any automated way (see §Constraints).

---

## Context

BarnardHQ's drone stack is five systems that are **operational islands**:

- **DroneOpsCommand (DOC)** — mission management, the customer record, billing,
  and the client deliverable (PDF report + download link). `Mission` is a native
  UUID entity with a client portal (`/api/client/missions/{mission_id}`). It has
  **no reference** to any Map job or EyesOn stream today.
- **DroneOpsMap** — photogrammetry. `Job` is a native UUID entity and **already
  carries a nullable `mission_id` FK back to a DOC Mission** ("Phase 1 mission
  spine"), currently unpopulated in production. Client delivery is a token-gated
  public share (`/api/share/{token}`).
- **EyesOn** — live streaming + recording. A `stream` is keyed by an immutable
  `stream_key` (string); there is **no external-reference column**. Client
  delivery is a token-gated guest link (`/share/{token}`).

A single real-world job — "fly the Springfield Drifters promo" — can produce a
DOC mission (report + invoice), a DroneOpsMap orthomosaic/3D deliverable, and an
EyesOn live stream + recording. **Nothing links them.** The client receives three
unrelated links from three systems; the operator has no single place to see "what
did this job produce." This friction lands hardest on exactly Bill's two growth
lines:

- **Real-estate / marketing:** the sellable deliverable is a *bundle* — aerial
  stills/report + a 3D/map walkthrough + optional highlight video. Today that is
  three disconnected surfaces.
- **Live-streaming / DFR / public-safety:** an incident/mission wants its live
  EyesOn feed and its after-action recording tied to the mission record for
  review and (commercial) billing.

## Constraints (load-bearing)

1. **CallSign firewall — CallSign ADR-0018 & ADR-0020
   (`/home/bbarnard065/callsign/docs/adr/`).** Verbatim: *"Do not build any
   automated CallSign ↔ DroneOpsCommand ↔ EyesOn chain… no shared state and no
   auto-triggering across product boundaries."* This ADR **excludes CallSign
   entirely** and, by analogy, honors its spirit for the systems it *does* touch:
   linkage here is **data-only and operator-initiated** — no system
   auto-provisions or auto-triggers work in another. Populating a reference field
   is not a "chain"; auto-creating a stream because a mission was booked would be.
   We do the former, never the latter.
2. **No new cross-service auth surface for v0.** Each system has independent
   JWT/token auth. A federated-SSO aggregation is out of scope for the minimal
   step (noted as future work).
3. **DOC is `.deployer-disabled`** — schema changes ship via Alembic at startup
   and require a manual BOS-HQ rebuild (verify container build time, not deployer
   status — ADR-0033 note).
4. **References, not copies.** The flight-attach incident family (ADR-0025/0026/
   0033, see `2026-07-03-flight-attach-unification.md`) is a direct lesson:
   attach-time *copies* go stale. Cross-system links must store an **id/key
   reference** and resolve the live artifact on read, never snapshot its state.

## Decision

Adopt a **minimal, incremental, two-tier linkage**. The mission is the
commercial spine; map jobs and streams are its **artifacts**, so the durable
reference lives on the **artifact side, pointing up to the mission** (1 mission →
N artifacts), reusing the linkage DroneOpsMap already built.

### The model

```
                 DOC Mission (UUID)  ── hub / commercial spine
                   ▲            ▲
   (child→parent)  │            │  (child→parent, soft ref)
      DroneOpsMap  │            │   EyesOn
      Job.mission_id (FK, exists)   stream.external_mission_id (NEW, nullable string)
```

- **DroneOpsMap → DOC:** *activate the existing* `Job.mission_id`. No schema
  change — populate it when a job is created for a known mission. (Operator picks
  the mission, or it is passed through from a "process this mission's imagery"
  action.) This is a soft/cross-DB reference by UUID string; keep it nullable
  (jobs without a mission remain valid).
- **EyesOn → DOC:** add one nullable column `streams.external_mission_id`
  (string, DOC mission UUID). Operator sets it when creating/labeling a stream
  for a mission. Soft ref (cross-DB, no FK). Optionally also
  `external_job_id` later; not required for v0.
- **DOC stays the reader-hub.** DOC does **not** duplicate `map_job_id` /
  `eyeson_stream_id` columns on `Mission` (that would force 1:1 and re-introduce
  the copy-goes-stale problem). Instead DOC *discovers* a mission's artifacts.

### Tier 0 (v0 — ship first, zero integration): manual convenience refs on Mission

For a single-operator reality, the cheapest robust step is **operator-pasted
deliverable URLs** on the mission, rendered in the existing client portal:

- Add nullable `Mission.map_share_url` and `Mission.eyeson_share_url` (text).
- The operator pastes the DroneOpsMap `/share/{token}` link and/or the EyesOn
  `/share/{token}` guest link into the mission.
- The DOC client portal (`/api/client/missions/{mission_id}` view) renders them
  as "View 3D map / measurements" and "View live stream / recording" buttons
  next to the existing report download — **one client surface, all deliverables.**
- Zero cross-service HTTP, zero SSO, zero firewall exposure. Fully honors ADR-0018/
  0020 (operator pastes a link; nothing auto-provisions).

This delivers the *user-visible* win (unified client delivery) on day one.

### Tier 1 (later — automate discovery): activate the id spine

When manual paste becomes friction:

- Populate `Job.mission_id` (DroneOpsMap) and `streams.external_mission_id`
  (EyesOn) at artifact-creation time (operator selects the mission).
- DOC's client-portal mission view performs a **read-only** aggregation: a
  service-user `GET map.barnardhq.com/api/jobs?mission_id={id}` and
  `GET eyeson/api/streams?external_mission_id={id}` to list live artifacts +
  their share links, resolved fresh on each render (reference-not-copy).
- Still operator-initiated and read-only — no auto-triggering.

## Options considered

**A. Put `map_job_id` + `eyeson_stream_id` on `Mission` (the task's first-cut).**
- Pros: DOC self-contained; one place to look.
- Cons: forces 1:1 (a mission can have several map jobs / streams); duplicates the
  reference DroneOpsMap already stores (`Job.mission_id`); a copied id/state on the
  parent re-creates the ADR-0033 staleness pattern; DOC must learn the child id
  (inbound write) which is more coupling than reading. **Rejected as the durable
  model** — but its *manual-URL* variant is adopted as Tier 0 because pasted
  deliverable links are display-only, not state to keep in sync.

**B. Child-side reference, DOC reads (chosen).**
- Pros: reuses existing `Job.mission_id`; natural 1:N; DOC never copies child
  state; references resolve live; minimal new schema (one EyesOn column).
- Cons: DOC needs a read path to enumerate artifacts (deferred to Tier 1; Tier 0
  sidesteps it with pasted URLs).

**C. New unified "Operation/Project" entity spanning all three systems.**
- Pros: clean conceptual hub; single portfolio object.
- Cons: a new shared entity + federated auth + migration of three systems = the
  rewrite this ADR is explicitly avoiding. **Rejected for now**; revisit only if
  the stack outgrows the mission-as-hub model.

## Consequences

- **Positive:** one client-facing surface per job (DOC client portal) shows
  report + map + stream; the operator finally has a mission → artifacts view;
  reuses existing infrastructure (`Job.mission_id`); tiny schema footprint (Tier
  0: 2 nullable DOC columns; Tier 1: 1 nullable EyesOn column + read paths).
- **Neutral / watch:** cross-DB soft refs are not FK-enforced — a deleted mission
  leaves dangling `external_mission_id`/`mission_id` values (benign; resolve-on-
  read simply finds nothing). Document that referential integrity is
  best-effort across systems.
- **Guardrails preserved:** CallSign remains firewalled and untouched; all
  linkage is operator-initiated and (Tier 1) read-only; no auto-provisioning
  anywhere. Reference-not-copy honored throughout.
- **Growth-line fit:** real-estate bundle and DFR after-action both become a
  single deliverable link without any new product.

## Implementation sketch (minimal)

1. **DOC (Tier 0):** Alembic revision adding `missions.map_share_url`,
   `missions.eyeson_share_url` (nullable text); mission edit form fields; client
   portal renders the two buttons when present. Version bump per CLAUDE.md (4
   files). Manual BOS-HQ rebuild.
2. **EyesOn (Tier 1 prep):** migration adding `streams.external_mission_id`
   (nullable); expose it in the stream create/edit surface and in
   `GET /api/streams?external_mission_id=`.
3. **DroneOpsMap (Tier 1):** populate `Job.mission_id` at submit;
   `GET /api/jobs?mission_id=` filter.
4. **DOC (Tier 1):** read-only aggregation in the client-portal mission view via
   a service user; feature-flag it so Tier 0 manual URLs remain the fallback.

## Open decision for Bill

Ship **Tier 0 (manual pasted deliverable URLs → unified client portal)** now, and
treat **Tier 1 (id-spine + read aggregation)** as a fast-follow only if manual
paste becomes a chore? Recommended: yes — Tier 0 is a few hours of work, no
integration risk, and delivers the entire visible benefit for a single-operator
business.
