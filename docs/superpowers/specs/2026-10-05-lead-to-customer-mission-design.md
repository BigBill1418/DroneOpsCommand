# Website lead → DOC customer / mission prefill — design

**Status:** spec — awaiting operator review (2026-10-05)
**Owner:** Claude (with Bill)
**Repos:** DroneOpsCommand (this repo), `marketing` (BarnardHQ marketing API + portal)
**Builds on:** ADR-0016 (mission lead-source attribution; reserved `missions.source_ref`
"for a future website→DroneOps lead handoff")

## 1. Problem

Website contact-form leads carry the prospect's name, email, phone, organization,
requested service and free-text details. When Bill turns one into work in DOC he
retypes all of it into the customer and mission forms, and nothing links the
resulting mission back to the lead. The lead's pipeline stage in the marketing
portal is also updated by hand, if at all.

## 2. Decisions (operator, 2026-10-05)

| # | Question | Decision |
|---|----------|----------|
| 1 | Which leads are pickable | **Website contact-form leads only** (not cold-campaign replies, no manual entry) |
| 2 | Flow | **Prefill, operator reviews** — pick a lead in the form, fields fill in, nothing is created until Save |
| 3 | Email matches an existing customer | **Suggest existing** — banner, reuse is the default, operator may create new |
| 4 | Write-back | Update the lead's state when a mission is created from it |
| 5 | Picker contents | **Open leads by default + search** that also reaches closed/older leads |
| 6 | Attribution | **Link only** — store the lead key, show a "View lead" link; attribution stays in the lead store |
| 7 | Stage set on mission create | **`won`** |

## 3. Where the leads come from (and why not D1)

Two stores hold website leads today:

- **Cloudflare D1 `barnardhq-leads`** (Worker `barnardhq-relay`, `api.barnardhq.com`) — the
  capture record: UTMs, click ids, landing page.
- **Marketing portal `leads` table** (`marketing.barnardhq.com`, BOS-HQ SQLite) — every
  website lead is mirrored here at capture (`worker/src/marketing-sync.ts` →
  `POST /webhook/leads/store`, upsert on email). The portal's `lead_pipeline` overlay
  (`new / contacted / qualified / won / lost`, keyed `web-<id>`) is where leads are worked.

DOC reads from the **marketing portal API**. Reasons:

1. The write-back target (decision 7) is the portal's `lead_pipeline`. D1's
   `handled_at` and its nag were retired 2026-06-04; the Worker's `/leads` and
   `/lead/:id` pages now redirect to `marketing.barnardhq.com/outbound/leads`.
2. DOC and the marketing API both run on BOS-HQ — a same-host call.
3. Reading D1 from BOS would need the IP-restricted `cfat_` token, which already returns
   CF API `7403` from BOS-HQ egress (marketing `docs/plans/2026-06-03-unified-leads-inbox.md`).

Accepted cost: the portal copy has one row per email and no UTMs/click ids. Decision 6
(link only) means DOC never needs those.

**Lead key** used everywhere in DOC: the portal's unified id, `web-<integer>`.

## 4. Components

### 4.1 Marketing API — DOC integration endpoints (repo `marketing`)

New, narrow routes. Each accepts **only** a dedicated DOC token
(`DOC_LEADS_TOKEN`, compared in constant time) — not the global `VITE_AUTH_TOKEN`
internal token, and the DOC token is accepted nowhere else. Registered before the global
`authMiddleware`, the same way the mobile pairing routes are.

| Route | Behaviour |
|-------|-----------|
| `GET /api/doc/leads?q=&include_closed=0\|1&limit=` | Website leads only (from `buildUnifiedLeads`, `source === 'website'`). Default: `is_open` only, newest first, `limit` default 25, max 100. `q` matches name, email, organization (case-insensitive substring). Returns `{ leads: [{ key, name, email, phone, organization, service, details, created_at, stage, is_open }] }`. |
| `GET /api/doc/leads/:key` | One website lead by `web-<id>`; 404 for unknown keys and for any `cold-*` key. |
| `POST /api/doc/leads/:key/won` | Upserts `lead_pipeline.stage = 'won'` through the same SQL as `PATCH /api/leads/:lead_key`. Idempotent; returns `{ key, stage }`. Body may carry `{ mission_ref }`, appended to `lead_pipeline.notes` as `DOC mission <id>` only if not already present. |

Plus one portal UI change: `/outbound/leads?lead=web-<id>` scrolls to and highlights that
lead, so DOC's "View lead" link lands on the right row. The portal has no per-lead
page today.

### 4.2 DOC backend

- **`app/services/lead_source.py`** — async `httpx` client: `list_leads(q, include_closed)`,
  `get_lead(key)`, `mark_won(key, mission_id)`. 5 s timeout. Raises
  `LeadSourceUnavailable` on transport/5xx and `LeadNotFound` on 404.
- **Config** (`app/config.py`): `LEADS_API_BASE` and `LEADS_API_TOKEN`. When either is
  unset, the feature is **off**: every `/api/leads*` route except `/api/leads/status`
  returns 404, and `GET /api/leads/status` returns `{ "enabled": false }`. DOC has no
  general feature-flag endpoint today, so this one route is what the frontend reads. Set only on Bill's production instance — **never on the
  demo instance** (`command-demo.barnardhq.com`) and not in the self-hosted defaults,
  because these are real prospects' personal details.
- **`app/routers/leads.py`** (operator JWT required):
  - `GET /api/leads/status` — `{ "enabled": bool }`; always 200.
  - `GET /api/leads?q=&include_closed=` — proxies the list.
  - `GET /api/leads/{key}` — the lead plus `matching_customer: {id, name} | null`, by
    case-insensitive email match against `customers.email`.
  - `POST /api/leads/{key}/mark-won?mission_id=` — retry hook for the write-back.
- **Migration** (Alembic): `customers.source_ref VARCHAR(255) NULL`. Must be idempotent
  (`ADD COLUMN IF NOT EXISTS`) — see the fresh-install migration trap fixed in v2.80.2.
  `missions.source` / `missions.source_ref` already exist (ADR-0016).
- **Mission create/update**: no new endpoint. After the mission row commits, if
  `source_ref` starts with `web-` and the mission was just created (or `source_ref` just
  changed to a `web-` value), call `mark_won` in the background. It is **never inside the
  DB transaction** and its failure never fails the request. Outcome recorded on the
  mission as `lead_writeback_at` (timestamp, nullable) — one more nullable column in the
  same migration — so the UI can show "not yet marked won".
- **Logging**: log lead keys and outcomes only — never names, emails or phone numbers.

### 4.3 DOC frontend

- **"Start from a lead" picker** — one shared component used in `MissionCreateModal.tsx`
  and the new-customer form. Hidden when `GET /api/leads/status` reports
  `enabled: false`. A searchable
  select showing open leads ("name — organization — service — 3 d ago"), with a
  "Show closed leads" toggle.
- **On pick**, prefill (every field stays editable):
  - Customer: name, email, phone, company ← organization.
  - Mission: title `"<service> — <organization or name>"`, description ← details,
    `source = "website"`, `source_ref = <key>`.
  - If `matching_customer` is set: banner *"Matches existing customer {name}"*, with
    **Use existing** (default) / **Create new**.
- **Mission page**: a **View lead** link to
  `https://marketing.barnardhq.com/outbound/leads?lead=<key>` when `source_ref` is a `web-`
  key. If `lead_writeback_at` is null, a warning *"Lead not marked won"* with **Retry**.
  The same link shows on the customer page when `customers.source_ref` is set.

## 5. Data flow

```
Operator opens New Mission → "Start from a lead"
  → DOC GET /api/leads → marketing GET /api/doc/leads (DOC token)
Operator picks web-42 → DOC GET /api/leads/web-42 (+ customer email match)
  → form prefilled; operator edits; Save
DOC POST /api/missions  (customer reused or created; source_ref=web-42)
  → commit → background: marketing POST /api/doc/leads/web-42/won
  → success: missions.lead_writeback_at = now()
```

## 6. Failure behaviour

| Failure | Behaviour |
|---------|-----------|
| Marketing API down / timeout | Picker shows "Leads unavailable"; manual entry unaffected |
| Lead deleted between pick and save | Save succeeds; write-back 404 is logged, warning shown |
| Write-back fails | Mission saved; warning + Retry on the mission page; retry is idempotent |
| Feature unconfigured (demo, self-host) | Picker hidden, `/api/leads*` 404, no outbound calls |
| Wrong/missing DOC token | Marketing returns 401; DOC treats as unavailable and logs a config error |

No new alert. A failed write-back is visible on the mission page, and per ADR-0037
gate Q1/Q2 it isn't worth paging over.

## 7. Testing

- **Marketing**: route tests — token required and the global token rejected; website-only
  filter; `cold-*` key 404; open/closed filter; `q` search; `won` idempotency and the notes
  append not duplicating.
- **DOC backend**: mocked `lead_source` — feature-off 404s; prefill payload mapping;
  email match case-insensitive; write-back runs after commit and its failure leaves the
  mission saved with `lead_writeback_at` null; retry sets it; migration idempotent on a
  fresh DB and on an already-migrated DB.
- **DOC frontend**: picker hidden when feature off; prefill populates fields; banner
  default selection.
- **Live verification** (done = verified live): on `droneops.barnardhq.com`, create a
  mission from a real website lead; confirm the lead shows `won` in the portal, the "View
  lead" link lands on the highlighted row, and the demo instance shows no picker.
  Visual check by eye at desktop and phone widths.

## 8. Documentation

- DOC: ADR-0050 "Website lead prefill + won write-back via marketing API" (amends
  ADR-0016's `source_ref` note), CHANGELOG, ROADMAP row.
- marketing: ADR for the DOC integration endpoints + scoped token, CHANGELOG.
- Token stored in 1Password Fleet vault ("DOC ↔ marketing leads token"), placed in both
  services' `.env` (chmod 600) on BOS-HQ.

## 9. Out of scope

Cold-campaign leads; manual lead entry; reporting revenue back to the lead
(`lead_pipeline.deal_value`) or to the Google Ads conversion export; attribution snapshots
on missions; one-click convert from the portal.
