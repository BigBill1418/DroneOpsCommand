# ADR-0016: Add lead-source attribution to missions

**Status:** Accepted (2026-05-25).
**Related ADRs:** 0014 (Mission Hub redesign — Hub + Facet edit pattern, the surface that gains the new field), 0009 (deposit feature — established the additive-nullable-ALTER + failover-safe migration pattern this ADR reuses), 0012 (secret hygiene — no tokens in code/commits).
**Author:** Aegis (feature lane), at operator request.

---

## Context

The operator (Bill, solo FAA Part 107 drone-services operator) could not answer a basic business question: **"How much of my job revenue came from the website?"** Revenue is well modeled — `missions` + `invoices` + `line_items`, aggregated by `GET /api/financials/summary` (`backend/app/routers/financials.py`), which already groups paid/billed totals by customer, mission type, drone, and month. But **nothing recorded where a job originated.** Neither `missions` nor `customers` carried a `source` / `lead` / `referral` / `origin` / `utm` column (verified against live prod schema on 2026-05-25). So there was no column to group revenue by, and the marketing dashboard's planned "website-attributed revenue" tile had no data source.

Concretely, the "Bella / Banks Missing Dog" job (mission `11083323-4de8-434d-a19c-6b4f32e4e46f`, LOST_PET, Washington County OR, 2026-05-23, invoice `BARNARDHQ-2026-0002` paid in full at $1,216.36) came in through the barnardhq.com contact form, but the system had no way to express that fact.

## Decision

Add a nullable lead-source attribution field to **missions** (the revenue-bearing entity, joined 1:1 to an invoice), expose it through the API and the Mission Hub forms, roll it up in the financials summary, and backfill the one known website job.

1. **Two new nullable columns on `missions`:**
   - `source VARCHAR(50)` — the origin. Allowed values are the `MissionSource` enum: `website`, `referral`, `repeat_client`, `phone`, `social`, `other`. `NULL` = origin unknown (every mission booked before this column existed, plus any future mission the operator leaves blank).
   - `source_ref VARCHAR(255)` — an optional external reference (e.g. a website contact-form lead id) for future linkage to the marketing pipeline. Free text.

2. **`source` is a plain `VARCHAR`, NOT a PostgreSQL enum.** The existing `mission_type` / `status` PG enums carry documented operational pain (uppercase-NAME vs lowercase-value label mismatches; see the `values_callable` comment block on `Mission.status` and the v2.67.1 normalization migration). A new small fixed-vocabulary field does not justify a `CREATE TYPE` + `ALTER TYPE ADD VALUE` migration or those traps. Allowed values are enforced in the **Pydantic schema layer** (`MissionSource`), which returns 422 on anything else — the same validation guarantee, without the DDL surface.

3. **Migration via the repo's real mechanism — `_add_missing_columns()` in `app/main.py`, NOT Alembic.** Although `alembic==1.14.0` is in `requirements.txt`, this repo has **no `alembic.ini`, no `env.py`, no `versions/` tree**. Schema evolution is done by `Base.metadata.create_all` + the idempotent `_add_missing_columns()` startup hook (a `migrations` dict of `(column_name, ALTER ... ADD COLUMN ...)` pairs guarded by an `inspector.get_columns()` presence check). This ADR adds the two `missions` ALTERs to that dict, matching ADR-0009/ADR-0015 precedent. (The task brief suggested Alembic; the repo's actual convention overrides that — introducing a one-off Alembic env here would fragment the migration story, not unify it.)

4. **API surface.** `source` + `source_ref` are added to `MissionCreate`, `MissionUpdate`, and `MissionResponse`. The missions router persists the `MissionSource` enum's plain value (`"website"`), not the member repr, on the VARCHAR column. The `revenue_by_source` rollup is added to `GET /api/financials/summary`, grouping **both collected (`paid`) and billed (`total`)** revenue per source plus a mission count, sorted by collected revenue descending, with `NULL` source mapped to the synthetic `"unknown"` bucket. Each row in the summary `missions` list also carries `source`.

5. **Frontend.** A "Lead Source" dropdown is added to the mission create modal (`MissionCreateModal.tsx`) and the details editor (`MissionDetailsEdit.tsx`), and a "Collected Revenue by Lead Source" panel to the Financials page. The dropdown options mirror the `MissionSource` vocabulary; an empty selection sends/clears `NULL`.

6. **Backfill.** The Banks mission is set to `source='website'` via a parameterized single-row UPDATE (matched by id, before-state captured).

## Failover & resilience analysis (per CLAUDE.md §Failover Guard)

1. **Streaming replication:** unaffected — two additive nullable column ALTERs, no port/pg_hba/connstring change.
2. **Container recreation:** the columns are reasserted idempotently by `_add_missing_columns()` on every startup; survives recreation.
3. **Blue-green swap:** standby-first deploy is safe — the new backend reads/writes the columns; an old backend simply ignores them (additive, nullable).
4. **Failover engine:** standby promotion runs the identical idempotent ALTER on the promoted node; no PK/FK/index/enum-type change to diverge.
5. **Customer-facing impact during failover:** none.

## Consequences

- The marketing↔DroneOpsCommand revenue bridge can now surface website-attributed collected revenue from `revenue_by_source` (the `website` bucket's `paid`).
- Historical missions read as `unknown`; the operator backfills origin going forward via the form. Only the Banks job is backfilled in this change (the one explicit, verified correction).
- `source_ref` is unused by the UI today — it exists so a future website→DroneOps lead handoff can stamp the originating lead id without another migration.

## Rejected alternatives

- **PG enum for `source`:** rejected — see Decision §2. The case-label traps and `ALTER TYPE` ceremony outweigh any storage/validation benefit for a 6-value field already validated in Pydantic.
- **Column on `customers` instead of `missions`:** rejected — a customer can arrive via the website once and become a repeat/phone client later; origin is a per-job fact, and revenue attribution must be per-mission to match how `financials/summary` aggregates.
- **A standalone Alembic env:** rejected — see Decision §3. Would fragment the repo's single migration story.
