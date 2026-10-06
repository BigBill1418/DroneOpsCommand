# ADR-0050: Website-lead prefill for missions/customers + `won` write-back

**Status:** Accepted — v2.97.0 (2026-10-05)
**Amends:** [ADR-0016](0016-mission-source-attribution.md) — `missions.source_ref` now carries the lead key
**Counterpart:** marketing ADR-0110 (DOC lead integration endpoints)
**Spec / plan:** `docs/superpowers/specs/2026-10-05-lead-to-customer-mission-design.md`,
`docs/superpowers/plans/2026-10-05-lead-to-customer-mission.md`

## Context

Website contact-form leads carry the prospect's name, email, phone, organization,
requested service and details. Turning one into work in DOC meant retyping all of it,
and nothing linked the mission back to the lead. ADR-0016 reserved `missions.source_ref`
for "a future website→DroneOps lead handoff". The lead pipeline (stages
`new / contacted / qualified / won / lost`) lives in the BarnardHQ marketing portal on
BOS-HQ. The Cloudflare D1 "handled" flag was retired on 2026-06-04.

## Decision

Decisions taken with the operator (2026-10-05):

- **Leads in scope:** website contact-form leads only.
- **Flow:** prefill, then the operator reviews. Nothing is created until Save.
- **Email match:** an email match suggests the existing customer. Reuse is the default,
  and the operator can still create a new customer.
- **Picker:** shows open leads by default, with search and a "show closed" toggle.
- **Attribution:** link only. DOC stores the lead key and copies no attribution data.
- **Write-back:** the lead is set to `won` when a mission is created from it.

Mechanics:

- **Source.** DOC's backend calls the marketing API's `/api/doc/leads*` routes on the same
  host at `http://host.docker.internal:3002`, using `extra_hosts: host-gateway` on the
  backend service. It authenticates with a dedicated token (`LEADS_API_TOKEN`, which equals
  marketing's `DOC_LEADS_TOKEN`). The Cloudflare Access front door is never in the path.
- **Lead key.** The portal's canonical id, matching `^(web|cold)-\d{1,12}$`. A website lead
  merged with a cold-campaign contact of the same email has canonical key `cold-<id>`; it
  is still a website lead, because its `sources` include `website`.
- **Backend.** `app/services/lead_source.py` is the HTTP client. `app/routers/leads.py`
  serves `GET /api/leads/status`, `GET /api/leads`, `GET /api/leads/{key}` (with a
  case-insensitive customer email match) and `POST /api/leads/{key}/mark-won` (retry).
  Migration `0013_lead_integration` adds `customers.source_ref` and
  `missions.lead_writeback_at`, guarded so it is idempotent on fresh installs.
- **Write-back.** When a mission is created with, or updated to, a lead-key `source_ref`,
  the endpoint commits first and then schedules `run_lead_writeback` as a FastAPI
  background task. Success stamps `missions.lead_writeback_at`. A failure is logged with
  keys only and leaves the column NULL, and the mission Hub then shows
  "Lead not marked won — Retry".
- **Frontend.** `LeadPicker` appears in the new-mission modal and the new-customer form.
  `MissionLeadStatus` on the Hub shows "View lead", which deep-links to
  `marketing.barnardhq.com/outbound/leads?lead=<key>`, plus the retry banner.

## Alternatives considered

- **Read Cloudflare D1 directly.** Rejected. The stage lives in the portal, and the `cfat_`
  token is IP-restricted (CF `7403` from BOS-HQ egress).
- **One-click "convert lead" in the portal.** Rejected by the operator, who wants to review
  the prefilled fields first.
- **Snapshot attribution (UTMs, landing page) onto the mission.** Rejected by the operator
  in favour of link-only. Attribution stays in the lead stores.
- **Set the lead to `qualified` and auto-flip it to `won` on payment.** Rejected by the
  operator: `won` is set on mission create.

## Consequences

- **Off unless configured.** With `LEADS_API_BASE` and `LEADS_API_TOKEN` blank, the picker
  is hidden and `/api/leads*` returns 404 (`/status` reports `enabled: false`). That is the
  self-hosted default. **Never set these on the demo instance**
  (`command-demo.barnardhq.com`): they expose real prospects' personal details.
- **Token rotation.** Store the new value in 1Password Fleet "DOC ↔ marketing leads token"
  and in both `.env` files. Then run `docker compose up -d backend` here and
  `docker compose up -d api` in marketing (`restart` does not reload `.env`).
- **`update_mission` signature.** `background_tasks` is the last parameter, defaulted to
  `None`, so direct positional callers keep working. A direct call schedules no write-back.
- **Logging.** Lead keys, mission ids and outcomes only. No names, emails or phone numbers.

## Amendment 1 — 2026-10-06 (v2.97.2, LD-2)

- **Retry route.** The retry route is now `POST /api/leads/missions/{mission_id}/mark-won`. The lead key
  comes from the mission's `source_ref`, so a caller can no longer name an arbitrary lead. The old
  `POST /api/leads/{key}/mark-won` is removed.
- **Write-back hardening.** `run_lead_writeback` catches every exception and returns False when the
  mission row is missing.
- **Commit order.** Create and update serialize before they commit.
- **UI.** The Hub hides Retry when `/api/leads/status` reports disabled.

## Failover self-check

The feature degrades to manual entry if the marketing API or its host is down. The only
new state is two nullable columns, so there is no new replication surface. The write-back
is idempotent, so a retry or a duplicate call is harmless.
