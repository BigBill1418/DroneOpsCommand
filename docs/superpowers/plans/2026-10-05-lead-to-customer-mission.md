# Website Lead → DOC Customer/Mission Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the operator pick a website lead in DOC's new-mission and new-customer forms, prefill the fields from it, link the record to the lead, and mark the lead `won` in the marketing pipeline when a mission is created from it.

**Architecture:** The marketing API (Express, BOS-HQ) gains three DOC-token-scoped routes over its existing unified-leads data. DOC's backend (FastAPI) gets a small httpx client, a `/api/leads` proxy router, one Alembic migration, and a post-commit background write-back in the mission create/update endpoints. DOC's frontend (React + Mantine) gets a shared `LeadPicker` used by the mission modal and the customer form, plus a "View lead" link / retry banner on the mission Hub.

**Tech Stack:** FastAPI 0.115.6, SQLAlchemy async, Alembic, httpx 0.28.1, pytest + pytest-asyncio (asyncio_mode=auto); React + Mantine + @mantine/form, axios, Vitest + Testing Library + msw; Express + better-sqlite3, `node:test`; marketing dashboard Vitest (pure-lib tests only).

**Spec:** `docs/superpowers/specs/2026-10-05-lead-to-customer-mission-design.md` (DroneOpsCommand repo). Read it first.

## Global Constraints

- **Repos and branches.** DOC work: worktree `~/wt-doc-leads`, branch `feat/lead-to-mission-spec` (rename not required). Marketing work: worktree `~/wt-mkt-leads` (currently detached at `origin/main`) — first command there is `git switch -c feat/doc-leads-integration`. **Never** work in `~/droneops` or `~/marketing` (shared checkouts — concurrent sessions).
- **Lead key pattern:** `^(web|cold)-\d{1,12}$`, always the portal's canonical id. "Website lead" = `sources` includes `'website'`.
- **Stage written:** exactly `won`.
- **Feature switch:** DOC env `LEADS_API_BASE` + `LEADS_API_TOKEN`; both required, else feature off. Marketing env `DOC_LEADS_TOKEN`; unset → every `/api/doc/*` route returns 401.
- **Never configure the leads env on the DOC demo instance** (`~/droneops-demo`, `command-demo.barnardhq.com`).
- **No PII in logs** — log lead keys, mission ids, outcomes only. Never names, emails, phones, details.
- **Write-back never fails a mission save** and never runs inside the request's DB transaction.
- **DOC version** bumps `2.96.0 → 2.97.0` in all three places: `backend/app/version.py`, `backend/app/main.py` (`version=`), `frontend/package.json`. `backend/tests/test_app_version_parity.py` enforces parity.
- **Marketing VERSION is NOT hand-bumped** — the NOC deployer bumps it.
- **Commit subjects:** docs-only commits carry `[skip-deploy]` in the **subject line**. Code commits do not.
- **Commit trailer** on every commit:
  ```
  Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01PUcQC7c2MwDNfirUjqM5u8
  ```
- **Builds** that are heavy (frontend `npm run build`) run via `ssh localhost` — the workspace cgroup SIGKILLs at ~2.5 GB.

## Review Focus

1. **A website lead merged with a cold contact** (canonical key `cold-N`, `sources: ['cold_campaign','website']`) must be listed, fetchable, and markable `won` — and a pure cold lead must 404 on all three. Pinned in Task 1 tests.
2. **Existing DOC frontend tests use msw `onUnhandledRequest: 'error'`** — the new `GET /api/leads/status` call in `MissionCreateModal` will break `MissionCreateModal.test.tsx` unless that file gets a status handler. Pinned in Task 7.
3. **Email matching is case- and whitespace-insensitive** (`Bob@X.com ` vs `bob@x.com`). Pinned in Task 5 tests.
4. **Lead with an empty organization and a `general`/empty service** must still produce a sane mission title (`"Website lead — <name>"`), never `" — "` or `"general — ..."`. Pinned in Tasks 1 and 6.
5. **Marketing API down while the operator is mid-form** — picker shows "Leads unavailable" and the rest of the form still submits. Pinned in Task 6 (component) and Task 5 (503 mapping).

---

## File Structure

**marketing repo (`~/wt-mkt-leads`)**
- Create `api/doc-leads.js` — pure helpers + `registerDocLeadsRoutes(app, deps)`. One responsibility: the DOC integration surface.
- Create `api/doc-leads.test.js` — `node:test` unit + route tests.
- Modify `api/server.js` — one `import` and one registrar call before `app.use(authMiddleware)`.
- Modify `docker-compose.yml` — `DOC_LEADS_TOKEN` env line on the `api` service.
- Modify `env.example` — narrative entry for `DOC_LEADS_TOKEN`.
- Create `dashboard/src/lib/lead-focus.ts` + `dashboard/src/lib/lead-focus.test.ts` — parse `?lead=`.
- Modify `dashboard/src/pages/outbound/AllLeads.tsx` — highlight + scroll to the focused lead.
- Create `docs/adr/0110-doc-leads-integration-endpoints.md`.

**DOC repo (`~/wt-doc-leads`)**
- Modify `backend/app/config.py` — two settings.
- Modify `docker-compose.yml` — `extra_hosts` on `backend`.
- Create `backend/app/services/lead_source.py` — HTTP client + key pattern + `is_enabled()`.
- Create `backend/app/services/lead_writeback.py` — `should_write_back()` + `run_lead_writeback()`.
- Create `backend/alembic/versions/0013_lead_integration.py`.
- Modify `backend/app/models/customer.py`, `backend/app/models/mission.py` — one column each.
- Modify `backend/app/schemas/customer.py`, `backend/app/schemas/mission.py`, `backend/app/routers/customers.py` (`_serialize_customer`).
- Create `backend/app/routers/leads.py`; modify `backend/app/main.py` (import + include + version).
- Modify `backend/app/routers/missions.py` — `BackgroundTasks` + write-back scheduling in create and update.
- Tests: `backend/tests/test_lead_source.py`, `test_lead_writeback.py`, `test_leads_router.py`, `test_missions_lead_writeback.py`, `test_lead_integration_schema.py`.
- Frontend: create `frontend/src/api/leads.ts`, `frontend/src/components/LeadPicker.tsx`, `frontend/src/components/__tests__/LeadPicker.test.tsx`, `frontend/src/components/MissionLeadStatus.tsx`, `frontend/src/components/__tests__/MissionLeadStatus.test.tsx`; modify `MissionCreateModal.tsx` (+ its test), `pages/Customers.tsx`, `pages/MissionDetail.tsx`, `api/types.ts`.
- Docs: `docs/adr/0050-website-lead-prefill-and-won-writeback.md`, `docs/adr/README.md`, `CHANGELOG.md`, `ROADMAP.md`, `backend/app/version.py`, `frontend/package.json`.

---

## Part A — marketing repo

### Task 1: DOC leads module (helpers + routes) with tests

**Files:**
- Create: `api/doc-leads.js`
- Test: `api/doc-leads.test.js`

**Interfaces:**
- Consumes: nothing from other tasks. At runtime, `server.js`'s `getDb()`, `buildUnifiedLeads(db)`, `ensureLeadPipeline(db)` (injected in Task 2).
- Produces (HTTP contract DOC relies on):
  - `GET /api/doc/leads?q=&include_closed=0|1&limit=` → `200 { leads: DocLead[] }`
  - `GET /api/doc/leads/:key` → `200 DocLead` | `404 { error }`
  - `POST /api/doc/leads/:key/won` body `{ mission_ref?: string }` → `200 { key, stage: 'won' }` | `404`
  - all → `401 { error: 'unauthorized' }` without `Authorization: Bearer <DOC_LEADS_TOKEN>`
  - `DocLead = { key, name, email, phone, organization, service, details, created_at, stage, is_open }`
- Produces (JS exports): `DOC_LEAD_KEY_RE`, `docTokenOk(authHeader, expected)`, `isWebsiteLead(l)`, `toDocLead(l)`, `selectWebsiteLeads(unified, { q, includeClosed, limit })`, `markWon(db, key, missionRef, ensureLeadPipeline)`, `registerDocLeadsRoutes(app, { getDb, buildUnifiedLeads, ensureLeadPipeline, token })`.

- [ ] **Step 1: Create the branch**

```bash
cd ~/wt-mkt-leads && git switch -c feat/doc-leads-integration
```

- [ ] **Step 2: Write the failing tests** — `api/doc-leads.test.js`

```js
// Run: node --test api/doc-leads.test.js
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import express from 'express';
import Database from 'better-sqlite3';
import {
  DOC_LEAD_KEY_RE, docTokenOk, isWebsiteLead, toDocLead, selectWebsiteLeads, markWon,
  registerDocLeadsRoutes,
} from './doc-leads.js';

// Same DDL as server.js ensureLeadPipeline (kept in sync by hand; it is 1 table).
function ensureLeadPipeline(db) {
  db.prepare(`CREATE TABLE IF NOT EXISTS lead_pipeline (
    lead_key TEXT PRIMARY KEY, stage TEXT NOT NULL DEFAULT 'new', owner TEXT,
    next_follow_up_at TEXT, last_touched_at TEXT, notes TEXT, deal_value REAL,
    sla_alerted_at TEXT, created_at TEXT DEFAULT (datetime('now')), updated_at TEXT)`).run();
}

const UNIFIED = [
  { id: 'web-1', source: 'website', sources: ['website'], name: 'Ann Open', email: 'ann@a.com',
    phone: '541', org: 'Acme', details: 'Roof survey', interest: 'inspection',
    created_at: '2026-10-01T10:00:00Z', stage: 'new', is_open: true },
  { id: 'web-2', source: 'website', sources: ['website'], name: 'Ben Won', email: 'ben@b.com',
    phone: '', org: '', details: '', interest: 'general',
    created_at: '2026-09-01T10:00:00Z', stage: 'won', is_open: false },
  { id: 'cold-7', source: 'cold_campaign', sources: ['cold_campaign', 'website'], name: 'Cal Merged',
    email: 'cal@c.com', phone: '', org: 'Fire Dist', details: 'Thermal', interest: 'reply: interested',
    created_at: '2026-10-03T10:00:00Z', stage: 'qualified', is_open: true },
  { id: 'cold-8', source: 'cold_campaign', sources: ['cold_campaign'], name: 'Dee Cold',
    email: 'dee@d.com', phone: '', org: 'Agency', details: '', interest: 'cold outreach',
    created_at: '2026-10-04T10:00:00Z', stage: 'new', is_open: true },
];

test('DOC_LEAD_KEY_RE accepts web/cold ints only', () => {
  assert.ok(DOC_LEAD_KEY_RE.test('web-12'));
  assert.ok(DOC_LEAD_KEY_RE.test('cold-7'));
  for (const bad of ['web-', 'web-1a', 'x-1', 'web-1; DROP', 'web-1234567890123']) {
    assert.equal(DOC_LEAD_KEY_RE.test(bad), false, bad);
  }
});

test('docTokenOk: exact bearer match only; empty expected always false', () => {
  assert.equal(docTokenOk('Bearer s3cret', 's3cret'), true);
  assert.equal(docTokenOk('Bearer wrong', 's3cret'), false);
  assert.equal(docTokenOk('s3cret', 's3cret'), false);
  assert.equal(docTokenOk(undefined, 's3cret'), false);
  assert.equal(docTokenOk('Bearer ', ''), false);
  assert.equal(docTokenOk('Bearer anything', undefined), false);
});

test('isWebsiteLead uses sources, so merged cold-* leads count', () => {
  assert.equal(isWebsiteLead(UNIFIED[2]), true);
  assert.equal(isWebsiteLead(UNIFIED[3]), false);
});

test('toDocLead maps fields and blanks the "general" interest', () => {
  assert.deepEqual(toDocLead(UNIFIED[0]), {
    key: 'web-1', name: 'Ann Open', email: 'ann@a.com', phone: '541', organization: 'Acme',
    service: 'inspection', details: 'Roof survey', created_at: '2026-10-01T10:00:00Z',
    stage: 'new', is_open: true,
  });
  assert.equal(toDocLead(UNIFIED[1]).service, '');
});

test('selectWebsiteLeads: open only by default, newest first, website only', () => {
  const keys = selectWebsiteLeads(UNIFIED, {}).map((l) => l.key);
  assert.deepEqual(keys, ['cold-7', 'web-1']);
});

test('selectWebsiteLeads: include_closed, q search, limit clamp', () => {
  assert.deepEqual(selectWebsiteLeads(UNIFIED, { includeClosed: true }).map((l) => l.key),
    ['cold-7', 'web-1', 'web-2']);
  assert.deepEqual(selectWebsiteLeads(UNIFIED, { q: 'ACME' }).map((l) => l.key), ['web-1']);
  assert.deepEqual(selectWebsiteLeads(UNIFIED, { q: 'cal@' }).map((l) => l.key), ['cold-7']);
  assert.equal(selectWebsiteLeads(UNIFIED, { includeClosed: true, limit: 1 }).length, 1);
  assert.equal(selectWebsiteLeads(UNIFIED, { includeClosed: true, limit: 0 }).length, 1);
  assert.equal(selectWebsiteLeads(UNIFIED, { includeClosed: true, limit: 999 }).length, 3);
});

test('markWon: inserts, is idempotent, appends mission note once', () => {
  const db = new Database(':memory:');
  markWon(db, 'web-1', 'm-1', ensureLeadPipeline);
  markWon(db, 'web-1', 'm-1', ensureLeadPipeline);
  const row = db.prepare('SELECT * FROM lead_pipeline WHERE lead_key=?').get('web-1');
  assert.equal(row.stage, 'won');
  assert.equal(row.notes, 'DOC mission m-1');
  markWon(db, 'web-1', 'm-2', ensureLeadPipeline);
  assert.equal(db.prepare('SELECT notes FROM lead_pipeline WHERE lead_key=?').get('web-1').notes,
    'DOC mission m-1\nDOC mission m-2');
});

test('markWon: keeps existing notes and owner', () => {
  const db = new Database(':memory:');
  ensureLeadPipeline(db);
  db.prepare("INSERT INTO lead_pipeline (lead_key, stage, owner, notes) VALUES ('web-1','qualified','bill','called 10/2')").run();
  markWon(db, 'web-1', null, ensureLeadPipeline);
  const row = db.prepare('SELECT * FROM lead_pipeline WHERE lead_key=?').get('web-1');
  assert.equal(row.stage, 'won');
  assert.equal(row.owner, 'bill');
  assert.equal(row.notes, 'called 10/2');
});

// ---- routes ----
let server; let base; let db;
before(async () => {
  db = new Database(':memory:');
  const app = express();
  app.use(express.json());
  registerDocLeadsRoutes(app, {
    getDb: () => db,
    buildUnifiedLeads: () => UNIFIED,
    ensureLeadPipeline,
    token: 'doc-token',
    closeDb: false,
  });
  await new Promise((r) => { server = app.listen(0, '127.0.0.1', r); });
  base = `http://127.0.0.1:${server.address().port}`;
});
after(() => server.close());

const H = { Authorization: 'Bearer doc-token', 'Content-Type': 'application/json' };

test('routes reject missing/wrong token with 401', async () => {
  for (const [method, path] of [['GET', '/api/doc/leads'], ['GET', '/api/doc/leads/web-1'], ['POST', '/api/doc/leads/web-1/won']]) {
    const r1 = await fetch(base + path, { method });
    assert.equal(r1.status, 401, `${method} ${path} no token`);
    const r2 = await fetch(base + path, { method, headers: { Authorization: 'Bearer changeme' } });
    assert.equal(r2.status, 401, `${method} ${path} wrong token`);
  }
});

test('GET /api/doc/leads lists open website leads', async () => {
  const r = await fetch(`${base}/api/doc/leads`, { headers: H });
  assert.equal(r.status, 200);
  const body = await r.json();
  assert.deepEqual(body.leads.map((l) => l.key), ['cold-7', 'web-1']);
});

test('GET /api/doc/leads?include_closed=1&q=ben finds a won lead', async () => {
  const r = await fetch(`${base}/api/doc/leads?include_closed=1&q=ben`, { headers: H });
  assert.deepEqual((await r.json()).leads.map((l) => l.key), ['web-2']);
});

test('GET /api/doc/leads/:key — website (incl. merged) 200, pure cold and bad keys 404', async () => {
  assert.equal((await fetch(`${base}/api/doc/leads/cold-7`, { headers: H })).status, 200);
  assert.equal((await fetch(`${base}/api/doc/leads/web-2`, { headers: H })).status, 200);
  assert.equal((await fetch(`${base}/api/doc/leads/cold-8`, { headers: H })).status, 404);
  assert.equal((await fetch(`${base}/api/doc/leads/web-99`, { headers: H })).status, 404);
  assert.equal((await fetch(`${base}/api/doc/leads/nope`, { headers: H })).status, 404);
});

test('POST won — 200 for website lead, 404 for pure cold', async () => {
  const ok = await fetch(`${base}/api/doc/leads/cold-7/won`, {
    method: 'POST', headers: H, body: JSON.stringify({ mission_ref: 'abc' }),
  });
  assert.equal(ok.status, 200);
  assert.deepEqual(await ok.json(), { key: 'cold-7', stage: 'won' });
  assert.equal(db.prepare('SELECT stage FROM lead_pipeline WHERE lead_key=?').get('cold-7').stage, 'won');
  const no = await fetch(`${base}/api/doc/leads/cold-8/won`, { method: 'POST', headers: H, body: '{}' });
  assert.equal(no.status, 404);
  assert.equal(db.prepare('SELECT 1 FROM lead_pipeline WHERE lead_key=?').get('cold-8'), undefined);
});
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd ~/wt-mkt-leads && node --test api/doc-leads.test.js`
Expected: FAIL — `Cannot find module ... doc-leads.js`. (If `better-sqlite3`/`express` are missing, run `npm ci` in `api/` first; the API's deps live in `api/package.json`.)

- [ ] **Step 4: Implement** — `api/doc-leads.js`

```js
// DOC (DroneOpsCommand) lead integration — ADR-0110.
//
// DOC's new-mission / new-customer forms prefill from WEBSITE leads and mark the
// lead `won` when a mission is created from it. These routes are the only surface
// DOC touches. They authenticate with a dedicated token (DOC_LEADS_TOKEN) that is
// accepted nowhere else, and they are registered BEFORE the global authMiddleware,
// the same way the mobile pairing routes are.
//
// "Website lead" means the unified lead's `sources` includes 'website'. A website
// lead merged with a cold contact of the same email has canonical key `cold-<id>`
// (buildUnifiedLeads, ADR-0030 #4); stage writes must land on that canonical key.
import { createHash, timingSafeEqual } from 'crypto';

export const DOC_LEAD_KEY_RE = /^(web|cold)-\d{1,12}$/;
const DEFAULT_LIMIT = 25;
const MAX_LIMIT = 100;

export function docTokenOk(authHeader, expected) {
  if (!expected) return false;
  const m = /^Bearer (.+)$/.exec(authHeader || '');
  if (!m) return false;
  // Hash both sides so lengths match and timingSafeEqual cannot throw.
  const a = createHash('sha256').update(m[1]).digest();
  const b = createHash('sha256').update(expected).digest();
  return timingSafeEqual(a, b);
}

export function isWebsiteLead(l) {
  return Array.isArray(l.sources) ? l.sources.includes('website') : l.source === 'website';
}

export function toDocLead(l) {
  const interest = l.interest || '';
  return {
    key: l.id,
    name: l.name || '',
    email: l.email || '',
    phone: l.phone || '',
    organization: l.org || '',
    service: interest === 'general' ? '' : interest,
    details: l.details || '',
    created_at: l.created_at || null,
    stage: l.stage || 'new',
    is_open: l.is_open !== false,
  };
}

export function selectWebsiteLeads(unified, { q, includeClosed = false, limit = DEFAULT_LIMIT } = {}) {
  const needle = (q || '').trim().toLowerCase();
  const n = Math.min(Math.max(Number(limit) || 1, 1), MAX_LIMIT);
  return unified
    .filter(isWebsiteLead)
    .filter((l) => includeClosed || l.is_open !== false)
    .filter((l) => !needle || [l.name, l.email, l.org].some((v) => (v || '').toLowerCase().includes(needle)))
    .sort((a, b) => (Date.parse(b.created_at) || 0) - (Date.parse(a.created_at) || 0))
    .slice(0, n)
    .map(toDocLead);
}

export function markWon(db, key, missionRef, ensureLeadPipeline) {
  ensureLeadPipeline(db);
  const now = new Date().toISOString();
  const existing = db.prepare('SELECT notes FROM lead_pipeline WHERE lead_key = ?').get(key);
  const prior = existing?.notes || null;
  const note = missionRef ? `DOC mission ${missionRef}` : null;
  let notes = prior;
  if (note && !(prior || '').split('\n').includes(note)) notes = prior ? `${prior}\n${note}` : note;
  db.prepare(`
    INSERT INTO lead_pipeline (lead_key, stage, notes, last_touched_at, sla_alerted_at, created_at, updated_at)
    VALUES (@key, 'won', @notes, @now, NULL, @now, @now)
    ON CONFLICT(lead_key) DO UPDATE SET
      stage = 'won', notes = @notes, last_touched_at = @now, sla_alerted_at = NULL, updated_at = @now
  `).run({ key, notes, now });
  return { key, stage: 'won' };
}

export function registerDocLeadsRoutes(app, {
  getDb, buildUnifiedLeads, ensureLeadPipeline,
  token = process.env.DOC_LEADS_TOKEN, closeDb = true,
}) {
  const guard = (req, res, next) => (docTokenOk(req.headers.authorization, token)
    ? next() : res.status(401).json({ error: 'unauthorized' }));

  // Run fn(db) and always close the per-request connection (server.js getDb()
  // opens a new better-sqlite3 handle per call).
  const withDb = (res, fn) => {
    const db = getDb();
    try { return fn(db); } catch (e) {
      console.error(JSON.stringify({ event: 'doc_leads_error', error: e.message }));
      return res.status(500).json({ error: 'internal error' });
    } finally { if (closeDb) db.close(); }
  };
  // Direct scan (not selectWebsiteLeads, which clamps to MAX_LIMIT) so a lead
  // older than the newest 100 is still found.
  const findWebsiteLead = (db, key) => {
    if (!DOC_LEAD_KEY_RE.test(key)) return null;
    const l = buildUnifiedLeads(db).find((u) => u.id === key);
    return l && isWebsiteLead(l) ? toDocLead(l) : null;
  };

  app.get('/api/doc/leads', guard, (req, res) => withDb(res, (db) => res.json({
    leads: selectWebsiteLeads(buildUnifiedLeads(db), {
      q: req.query.q,
      includeClosed: req.query.include_closed === '1',
      limit: req.query.limit ?? DEFAULT_LIMIT,
    }),
  })));

  app.get('/api/doc/leads/:key', guard, (req, res) => withDb(res, (db) => {
    const lead = findWebsiteLead(db, req.params.key);
    return lead ? res.json(lead) : res.status(404).json({ error: 'lead not found' });
  }));

  app.post('/api/doc/leads/:key/won', guard, (req, res) => withDb(res, (db) => {
    const lead = findWebsiteLead(db, req.params.key);
    if (!lead) return res.status(404).json({ error: 'lead not found' });
    const ref = typeof req.body?.mission_ref === 'string' ? req.body.mission_ref.slice(0, 64) : null;
    const out = markWon(db, lead.key, ref, ensureLeadPipeline);
    console.log(JSON.stringify({ event: 'doc_lead_won', key: lead.key }));
    return res.json(out);
  }));
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd ~/wt-mkt-leads && node --test api/doc-leads.test.js`
Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
cd ~/wt-mkt-leads && git add api/doc-leads.js api/doc-leads.test.js
git commit -m "feat(leads): DOC integration routes for website leads + won write-back (ADR-0110)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PUcQC7c2MwDNfirUjqM5u8"
```

### Task 2: Wire routes into server.js, env, ADR

**Files:**
- Modify: `api/server.js` (import block near line 47; registrar before line 407 `app.use(authMiddleware)`)
- Modify: `docker-compose.yml` (`api` service `environment:` list, near line 40)
- Modify: `env.example`
- Create: `docs/adr/0110-doc-leads-integration-endpoints.md`

**Interfaces:**
- Consumes: `registerDocLeadsRoutes` from Task 1; `getDb`, `buildUnifiedLeads`, `ensureLeadPipeline` already defined in `server.js` (function declarations — hoisted, so callable from the registrar).
- Produces: the live routes on the running API.

- [ ] **Step 1: Add the import** next to the other route-module imports (after `import { initAuthTables, authMiddleware, registerAuthRoutes } from './auth.js';`):

```js
import { registerDocLeadsRoutes } from './doc-leads.js';
```

- [ ] **Step 2: Register before the global auth middleware.** Immediately after `registerMobileAuthRoutes(app, { authMiddleware });` add:

```js
// DOC (DroneOpsCommand) lead prefill + won write-back (ADR-0110). Own token
// (DOC_LEADS_TOKEN), accepted nowhere else; must sit before app.use(authMiddleware).
registerDocLeadsRoutes(app, { getDb, buildUnifiedLeads, ensureLeadPipeline });
```

- [ ] **Step 3: Pass the env var.** In `docker-compose.yml`, `api` service `environment:`, after `- VITE_AUTH_TOKEN=${VITE_AUTH_TOKEN:-changeme}`:

```yaml
      - DOC_LEADS_TOKEN=${DOC_LEADS_TOKEN:-}
```

- [ ] **Step 4: Document the env var** in `env.example` (narrative file — follow its existing entry style):

```
DOC_LEADS_TOKEN — bearer token DroneOpsCommand presents to /api/doc/leads* (ADR-0110).
  Unset → those routes return 401. Value lives in 1Password Fleet
  "DOC ↔ marketing leads token" and in DOC's LEADS_API_TOKEN.
```

- [ ] **Step 4b: Fix the PATCH connection leak found during planning.** `PATCH /api/leads/:lead_key` (server.js ~3839) calls `db.close()` only on the success path; its `catch` leaks the better-sqlite3 handle. Restructure it to `const db = getDb(); try { ...existing body without db.close()... } catch (e) { res.status(500).json({ error: e.message }); } finally { db.close(); }` — the `getDb()` call moves above the `try`. Behaviour is otherwise unchanged. Record it in the ADR-0110 Consequences as "fixed in passing: PATCH /api/leads leaked a DB handle on error".

- [ ] **Step 5: Smoke the wiring** (syntax + registration, no server start):

Run: `cd ~/wt-mkt-leads && node --check api/server.js && grep -n "registerDocLeadsRoutes" api/server.js`
Expected: no syntax error; two matches (import + call), the call line number lower than the `app.use(authMiddleware)` line.

- [ ] **Step 6: Write the ADR** — `docs/adr/0110-doc-leads-integration-endpoints.md`:

```markdown
# ADR-0110: DOC lead integration endpoints (scoped token)

**Status:** Accepted — 2026-10-05
**Consumer:** DroneOpsCommand ADR-0050

## Context
DroneOpsCommand prefills new missions/customers from website leads and marks the
lead `won` when a mission is created from it (DOC spec
`docs/superpowers/specs/2026-10-05-lead-to-customer-mission-design.md`). The lead
pipeline lives here (`lead_pipeline`, ADR-0030). DOC runs on the same host (BOS-HQ)
and reaches this API on `:3002` via `host.docker.internal`, not through the
Cloudflare Access front door.

## Decision
Three routes in `api/doc-leads.js`, registered before `app.use(authMiddleware)`:
`GET /api/doc/leads`, `GET /api/doc/leads/:key`, `POST /api/doc/leads/:key/won`.
- Auth: `Authorization: Bearer $DOC_LEADS_TOKEN`, constant-time compare; the token is
  accepted on these routes only, and the global `VITE_AUTH_TOKEN` is NOT accepted here.
- Scope: website leads only = unified lead whose `sources` include `website`
  (merged leads keep their canonical `cold-<id>` key). Pure cold-campaign leads 404 on
  all three routes, so DOC can never move one.
- Write: `won` only, via the same upsert shape as `PATCH /api/leads/:lead_key`; appends
  `DOC mission <id>` to notes once.

## Alternatives considered
- Reuse `VITE_AUTH_TOKEN` — rejected: full API authority for a 3-route need.
- DOC reads Cloudflare D1 directly — rejected: the pipeline stage lives here, and the
  `cfat_` token is IP-restricted (CF 7403 from BOS egress).

## Consequences
- Rotating the token = new value in 1Password + both `.env` files, then
  `docker compose up -d api` here and `up -d backend` in DOC (`restart` does not reload `.env`).
- Failover: the routes are stateless over the existing SQLite DB; no new replication surface.
- Fixed in passing: `PATCH /api/leads/:lead_key` leaked a DB handle on its error path; it now closes in `finally`.
```

- [ ] **Step 7: Commit**

```bash
cd ~/wt-mkt-leads && git add api/server.js docker-compose.yml env.example docs/adr/0110-doc-leads-integration-endpoints.md
git commit -m "feat(leads): register DOC lead routes before global auth; DOC_LEADS_TOKEN env; fix PATCH lead db leak (ADR-0110)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PUcQC7c2MwDNfirUjqM5u8"
```

### Task 3: Portal `?lead=` focus

**Files:**
- Create: `dashboard/src/lib/lead-focus.ts`, `dashboard/src/lib/lead-focus.test.ts`
- Modify: `dashboard/src/pages/outbound/AllLeads.tsx` (imports; `LeadRow` root at line ~56; list render at ~265)

**Interfaces:**
- Produces: URL contract `https://marketing.barnardhq.com/outbound/leads?lead=<key>` — DOC Task 8 links to it.

- [ ] **Step 1: Failing test** — `dashboard/src/lib/lead-focus.test.ts`

```ts
import { describe, it, expect } from 'vitest';
import { readFocusLeadKey } from './lead-focus';

describe('readFocusLeadKey', () => {
  it('returns a valid key', () => {
    expect(readFocusLeadKey('?lead=web-12')).toBe('web-12');
    expect(readFocusLeadKey('?x=1&lead=cold-7')).toBe('cold-7');
  });
  it('rejects missing or malformed keys', () => {
    expect(readFocusLeadKey('')).toBeNull();
    expect(readFocusLeadKey('?lead=')).toBeNull();
    expect(readFocusLeadKey('?lead=web-1%22%3E')).toBeNull();
    expect(readFocusLeadKey('?lead=foo-1')).toBeNull();
  });
});
```

- [ ] **Step 2: Run** `cd ~/wt-mkt-leads/dashboard && npx vitest run src/lib/lead-focus.test.ts` — Expected: FAIL (module missing).

- [ ] **Step 3: Implement** — `dashboard/src/lib/lead-focus.ts`

```ts
// `?lead=<key>` deep link from DroneOpsCommand's "View lead" (marketing ADR-0110).
const KEY_RE = /^(web|cold)-\d{1,12}$/;

export function readFocusLeadKey(search: string): string | null {
  const v = new URLSearchParams(search).get('lead');
  return v && KEY_RE.test(v) ? v : null;
}
```

- [ ] **Step 4: Run** the same command — Expected: PASS.

- [ ] **Step 5: Wire into `AllLeads.tsx`.**
  1. Add `useRef` to the React import and `import { readFocusLeadKey } from '@/lib/lead-focus';`.
  2. In `AllLeads()` after the existing `useState` lines (~169) add:

```tsx
  // DOC "View lead" deep link — show every stage so a won lead is visible, then scroll to it.
  const focusKey = useMemo(() => readFocusLeadKey(window.location.search), []);
  const scrolledRef = useRef(false);
  useEffect(() => {
    if (!focusKey || scrolledRef.current || !data) return;
    const el = document.getElementById(`lead-${focusKey}`);
    if (el) { el.scrollIntoView({ behavior: 'smooth', block: 'center' }); scrolledRef.current = true; }
  }, [focusKey, data]);
```

  The existing `stageFilter` default is already `'all'`, so a `won` lead is listed; leave `needsOnly` default `false` untouched.
  3. Give `LeadRow` a `focused` prop and an id. Change the signature to `function LeadRow({ lead, onPatch, busy, focused }: { ...existing types...; focused?: boolean })` and wrap its returned `GlassCard` in:

```tsx
    <div id={`lead-${lead.id}`} className={focused ? 'rounded-xl ring-2 ring-[#00d4ff]' : undefined}>
      {/* existing <GlassCard ...> ... </GlassCard> unchanged */}
    </div>
```

  4. In the list render pass it: `<LeadRow key={l.id} lead={l} busy={busyKey === l.id} onPatch={onPatch} focused={l.id === focusKey} />`.

- [ ] **Step 6: Type-check** — `cd ~/wt-mkt-leads/dashboard && npx tsc --noEmit -p .` — Expected: no errors.

- [ ] **Step 7: Commit**

```bash
cd ~/wt-mkt-leads && git add dashboard/src/lib/lead-focus.ts dashboard/src/lib/lead-focus.test.ts dashboard/src/pages/outbound/AllLeads.tsx
git commit -m "feat(leads): ?lead=<key> deep link highlights a lead in All Leads (ADR-0110)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PUcQC7c2MwDNfirUjqM5u8"
```

---

## Part B — DOC repo backend

### Task 4: Config, network path, lead-source client

**Files:**
- Modify: `backend/app/config.py` (add after `opendronelog_url: str = ""`, line ~28)
- Modify: `docker-compose.yml` (`backend` service)
- Create: `backend/app/services/lead_source.py`
- Test: `backend/tests/test_lead_source.py`

**Interfaces:**
- Produces:
  - `settings.leads_api_base: str`, `settings.leads_api_token: str`
  - `LEAD_KEY_RE: re.Pattern` (`^(web|cold)-\d{1,12}$`)
  - `class LeadSourceUnavailable(Exception)`, `class LeadNotFound(Exception)`
  - `def is_enabled() -> bool`
  - `class LeadSourceClient(base: str, token: str, *, transport: httpx.AsyncBaseTransport | None = None, timeout: float = 5.0)` with
    `async list_leads(q: str | None = None, include_closed: bool = False, limit: int = 25) -> list[dict]`,
    `async get_lead(key: str) -> dict`,
    `async mark_won(key: str, mission_ref: str | None = None) -> dict`
  - `def get_client() -> LeadSourceClient` (raises `LeadSourceUnavailable` when disabled)

- [ ] **Step 1: Failing tests** — `backend/tests/test_lead_source.py`

```python
"""Website-lead client (ADR-0050)."""
import httpx
import pytest

from app.services import lead_source
from app.services.lead_source import (
    LEAD_KEY_RE, LeadNotFound, LeadSourceClient, LeadSourceUnavailable,
)


def _client(handler):
    return LeadSourceClient("http://mkt:3002/", "tok", transport=httpx.MockTransport(handler))


def test_lead_key_pattern():
    assert LEAD_KEY_RE.match("web-1") and LEAD_KEY_RE.match("cold-42")
    for bad in ["web-", "x-1", "web-1/../x", "web-1234567890123", ""]:
        assert not LEAD_KEY_RE.match(bad)


def test_is_enabled_needs_both(monkeypatch):
    monkeypatch.setattr(lead_source.settings, "leads_api_base", "http://x")
    monkeypatch.setattr(lead_source.settings, "leads_api_token", "")
    assert lead_source.is_enabled() is False
    monkeypatch.setattr(lead_source.settings, "leads_api_token", "t")
    assert lead_source.is_enabled() is True


def test_get_client_disabled_raises(monkeypatch):
    monkeypatch.setattr(lead_source.settings, "leads_api_base", "")
    with pytest.raises(LeadSourceUnavailable):
        lead_source.get_client()


async def test_list_leads_sends_auth_and_params():
    seen = {}

    def handler(req: httpx.Request):
        seen["url"] = str(req.url)
        seen["auth"] = req.headers.get("authorization")
        return httpx.Response(200, json={"leads": [{"key": "web-1"}]})

    out = await _client(handler).list_leads(q="acme", include_closed=True, limit=10)
    assert out == [{"key": "web-1"}]
    assert seen["auth"] == "Bearer tok"
    assert seen["url"].startswith("http://mkt:3002/api/doc/leads?")
    assert "q=acme" in seen["url"] and "include_closed=1" in seen["url"] and "limit=10" in seen["url"]


async def test_get_lead_404_raises_not_found():
    with pytest.raises(LeadNotFound):
        await _client(lambda r: httpx.Response(404, json={})).get_lead("web-9")


async def test_get_lead_rejects_bad_key_without_request():
    called = False

    def handler(req):
        nonlocal called
        called = True
        return httpx.Response(200, json={})

    with pytest.raises(LeadNotFound):
        await _client(handler).get_lead("../admin")
    assert called is False


@pytest.mark.parametrize("status", [401, 500, 503])
async def test_non_404_errors_are_unavailable(status):
    with pytest.raises(LeadSourceUnavailable):
        await _client(lambda r: httpx.Response(status, json={})).list_leads()


async def test_transport_error_is_unavailable():
    def handler(req):
        raise httpx.ConnectError("refused", request=req)

    with pytest.raises(LeadSourceUnavailable):
        await _client(handler).list_leads()


async def test_mark_won_posts_mission_ref():
    seen = {}

    def handler(req: httpx.Request):
        seen["path"] = req.url.path
        seen["body"] = req.content
        return httpx.Response(200, json={"key": "web-1", "stage": "won"})

    out = await _client(handler).mark_won("web-1", "m-1")
    assert out == {"key": "web-1", "stage": "won"}
    assert seen["path"] == "/api/doc/leads/web-1/won"
    assert b'"mission_ref":"m-1"' in seen["body"].replace(b" ", b"")
```

- [ ] **Step 2: Run** `cd ~/wt-doc-leads/backend && pytest tests/test_lead_source.py -v` — Expected: FAIL (`ModuleNotFoundError: app.services.lead_source`).

- [ ] **Step 3: Settings** — in `backend/app/config.py`, after `opendronelog_url: str = ""`:

```python
    # ADR-0050 — website-lead prefill. Marketing API base (host-local on BOS-HQ:
    # http://host.docker.internal:3002) + its DOC_LEADS_TOKEN. Both empty = feature
    # off. NEVER set on the demo instance (real prospects' personal details).
    leads_api_base: str = ""
    leads_api_token: str = ""
```

- [ ] **Step 4: Client** — `backend/app/services/lead_source.py`

```python
"""Website-lead source: the marketing API's DOC routes (ADR-0050 / marketing ADR-0110).

Logs carry lead keys and outcomes only — never names, emails or phone numbers.
"""
from __future__ import annotations

import logging
import re

import httpx

from app.config import settings

logger = logging.getLogger("doc.leads")

LEAD_KEY_RE = re.compile(r"^(web|cold)-\d{1,12}$")


class LeadSourceUnavailable(Exception):
    """Marketing API unreachable, misconfigured, or erroring."""


class LeadNotFound(Exception):
    """No website lead with that key."""


def is_enabled() -> bool:
    return bool(settings.leads_api_base and settings.leads_api_token)


class LeadSourceClient:
    def __init__(
        self,
        base: str,
        token: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 5.0,
    ) -> None:
        self._base = base.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"}
        self._transport = transport
        self._timeout = timeout

    async def _request(self, method: str, path: str, **kw) -> dict:
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, transport=self._transport, headers=self._headers
            ) as c:
                resp = await c.request(method, f"{self._base}{path}", **kw)
        except httpx.HTTPError as exc:
            logger.warning("[LEADS] %s %s transport error: %s", method, path, type(exc).__name__)
            raise LeadSourceUnavailable(type(exc).__name__) from exc
        if resp.status_code == 404:
            raise LeadNotFound(path)
        if resp.status_code == 401:
            logger.error("[LEADS] marketing API rejected the DOC token (401) — check LEADS_API_TOKEN")
        if resp.status_code >= 400:
            raise LeadSourceUnavailable(f"HTTP {resp.status_code}")
        return resp.json()

    async def list_leads(
        self, q: str | None = None, include_closed: bool = False, limit: int = 25
    ) -> list[dict]:
        params: dict[str, str | int] = {"include_closed": "1" if include_closed else "0", "limit": limit}
        if q:
            params["q"] = q
        data = await self._request("GET", "/api/doc/leads", params=params)
        return list(data.get("leads", []))

    async def get_lead(self, key: str) -> dict:
        if not LEAD_KEY_RE.match(key):
            raise LeadNotFound(key)
        return await self._request("GET", f"/api/doc/leads/{key}")

    async def mark_won(self, key: str, mission_ref: str | None = None) -> dict:
        if not LEAD_KEY_RE.match(key):
            raise LeadNotFound(key)
        return await self._request(
            "POST", f"/api/doc/leads/{key}/won", json={"mission_ref": mission_ref}
        )


def get_client() -> LeadSourceClient:
    if not is_enabled():
        raise LeadSourceUnavailable("leads integration not configured")
    return LeadSourceClient(settings.leads_api_base, settings.leads_api_token)
```

- [ ] **Step 5: Run** `cd ~/wt-doc-leads/backend && pytest tests/test_lead_source.py -v` — Expected: PASS.

- [ ] **Step 6: Network path** — in `docker-compose.yml`, under the `backend:` service, add (merge into an existing `extra_hosts:` if one appears; there is none today):

```yaml
    # ADR-0050 — reach the marketing API on the same host (BOS-HQ :3002) without
    # the Cloudflare Access front door. Harmless where LEADS_API_BASE is unset.
    extra_hosts:
      - "host.docker.internal:host-gateway"
```

Run: `cd ~/wt-doc-leads && docker compose -f docker-compose.yml config -q && echo ok` — Expected: `ok`. (If `docker` is unavailable in the workspace, run `python3 -c "import yaml,sys;yaml.safe_load(open('docker-compose.yml'))" && echo ok`.)

- [ ] **Step 7: Commit**

```bash
cd ~/wt-doc-leads && git add backend/app/config.py backend/app/services/lead_source.py backend/tests/test_lead_source.py docker-compose.yml
git commit -m "feat(leads): marketing lead-source client + LEADS_API_* settings (ADR-0050)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PUcQC7c2MwDNfirUjqM5u8"
```

### Task 5: Schema (migration + models + schemas) and `/api/leads` router

**Files:**
- Create: `backend/alembic/versions/0013_lead_integration.py`
- Modify: `backend/app/models/customer.py` (after `notes`, line ~23), `backend/app/models/mission.py` (after `source_ref`, line ~94)
- Modify: `backend/app/schemas/customer.py` (`CustomerCreate`, `CustomerUpdate`, `CustomerResponse`), `backend/app/schemas/mission.py` (`MissionResponse`)
- Modify: `backend/app/routers/customers.py` (`_serialize_customer`, add `source_ref=customer.source_ref,`)
- Create: `backend/app/services/lead_writeback.py`, `backend/app/routers/leads.py`
- Modify: `backend/app/main.py` (router import line 23 + `app.include_router(leads.router)`)
- Test: `backend/tests/test_lead_integration_schema.py`, `backend/tests/test_lead_writeback.py`, `backend/tests/test_leads_router.py`

**Interfaces:**
- Consumes: Task 4 (`LEAD_KEY_RE`, `LeadSourceClient`, `LeadNotFound`, `LeadSourceUnavailable`, `is_enabled`, `get_client`).
- Produces:
  - DB: `customers.source_ref VARCHAR(255) NULL`, `missions.lead_writeback_at TIMESTAMP NULL`
  - `CustomerCreate/Update/Response.source_ref: str | None`; `MissionResponse.lead_writeback_at: datetime | None`
  - `app.services.lead_writeback.should_write_back(source_ref: str | None) -> bool`
  - `app.services.lead_writeback.run_lead_writeback(mission_id: UUID, lead_key: str, *, client: LeadSourceClient | None = None, session_factory=async_session) -> bool`
  - HTTP: `GET /api/leads/status → {enabled: bool}`; `GET /api/leads?q=&include_closed= → {leads: DocLead[]}`; `GET /api/leads/{key} → {lead: DocLead, matching_customer: {id, name} | null}`; `POST /api/leads/{key}/mark-won?mission_id=<uuid> → {ok: true}`; disabled → 404; marketing down → 503 `{"detail": "Leads unavailable"}`; write-back retry failure → 502.

- [ ] **Step 1: Failing schema tests** — `backend/tests/test_lead_integration_schema.py`

```python
"""ADR-0050 schema surface: customers.source_ref, missions.lead_writeback_at."""
from datetime import datetime

from app.models.customer import Customer
from app.models.mission import Mission
from app.schemas.customer import CustomerCreate, CustomerUpdate
from app.schemas.mission import MissionResponse


def test_models_have_columns():
    assert "source_ref" in Customer.__table__.columns
    assert "lead_writeback_at" in Mission.__table__.columns
    assert Customer.__table__.columns["source_ref"].nullable
    assert Mission.__table__.columns["lead_writeback_at"].nullable


def test_customer_schemas_accept_source_ref():
    assert CustomerCreate(name="A", source_ref="web-1").source_ref == "web-1"
    assert CustomerUpdate(source_ref="cold-2").source_ref == "cold-2"


def test_mission_response_exposes_lead_writeback_at():
    assert "lead_writeback_at" in MissionResponse.model_fields
    assert MissionResponse.model_fields["lead_writeback_at"].default is None


def test_migration_0013_chains_from_0012():
    import importlib.util, pathlib
    p = pathlib.Path(__file__).parents[1] / "alembic/versions/0013_lead_integration.py"
    spec = importlib.util.spec_from_file_location("m0013", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert m.down_revision == "0012_cf_access_ident"
    assert m.revision == "0013_lead_integration"
```

- [ ] **Step 2: Run** `cd ~/wt-doc-leads/backend && pytest tests/test_lead_integration_schema.py -v` — Expected: FAIL.

- [ ] **Step 3: Migration** — `backend/alembic/versions/0013_lead_integration.py`

```python
"""ADR-0050 — website-lead link columns.

customers.source_ref      — lead key the customer was created from (web-N / cold-N)
missions.lead_writeback_at — when the lead was marked `won` in the marketing pipeline

Idempotent: on a fresh install 0001's live-models create_all already built both
columns (the v2.80.2 fresh-install trap), so each add is guarded.
"""
import sqlalchemy as sa
from alembic import op

revision = "0013_lead_integration"
down_revision = "0012_cf_access_ident"
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "source_ref" not in {c["name"] for c in insp.get_columns("customers")}:
        op.add_column("customers", sa.Column("source_ref", sa.String(length=255), nullable=True))
    if "lead_writeback_at" not in {c["name"] for c in insp.get_columns("missions")}:
        op.add_column("missions", sa.Column("lead_writeback_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "lead_writeback_at" in {c["name"] for c in insp.get_columns("missions")}:
        op.drop_column("missions", "lead_writeback_at")
    if "source_ref" in {c["name"] for c in insp.get_columns("customers")}:
        op.drop_column("customers", "source_ref")
```

- [ ] **Step 4: Models.** `backend/app/models/customer.py`, after the `notes` column:

```python
    # ADR-0050 — website lead key this customer was created from (web-N / cold-N).
    source_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
```

`backend/app/models/mission.py`, after `source_ref`:

```python
    # ADR-0050 — set when the lead in `source_ref` was marked `won` in the
    # marketing pipeline; NULL + a lead-key source_ref = write-back pending/failed.
    lead_writeback_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
```

- [ ] **Step 5: Schemas.** In `backend/app/schemas/customer.py` add `source_ref: str | None = None` as the last field of `CustomerCreate`, `CustomerUpdate` and `CustomerResponse`. In `backend/app/schemas/mission.py` `MissionResponse`, after `source_ref: str | None = None`, add `lead_writeback_at: datetime | None = None` (`datetime` is already imported there — confirm with `grep -n "^from datetime" backend/app/schemas/mission.py`; add `from datetime import datetime` if not). In `backend/app/routers/customers.py` `_serialize_customer`, add `source_ref=customer.source_ref,` after `notes=customer.notes,`.

- [ ] **Step 6: Run** `pytest tests/test_lead_integration_schema.py -v` — Expected: PASS. Then the full existing customer/mission suites to catch serializer fallout: `pytest tests -q -k "customer or mission"` — Expected: PASS.

- [ ] **Step 7: Failing write-back service tests** — `backend/tests/test_lead_writeback.py`

```python
"""ADR-0050 won write-back service."""
import uuid

import pytest

from app.services import lead_source, lead_writeback
from app.services.lead_source import LeadNotFound, LeadSourceUnavailable


class FakeClient:
    def __init__(self, exc=None):
        self.exc, self.calls = exc, []

    async def mark_won(self, key, mission_ref=None):
        self.calls.append((key, mission_ref))
        if self.exc:
            raise self.exc
        return {"key": key, "stage": "won"}


class FakeSession:
    executed: list = []
    committed = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, stmt):
        FakeSession.executed.append(stmt)

    async def commit(self):
        FakeSession.committed += 1


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    FakeSession.executed, FakeSession.committed = [], 0
    monkeypatch.setattr(lead_source.settings, "leads_api_base", "http://x")
    monkeypatch.setattr(lead_source.settings, "leads_api_token", "t")


def test_should_write_back(monkeypatch):
    assert lead_writeback.should_write_back("web-1")
    assert lead_writeback.should_write_back("cold-3")
    assert not lead_writeback.should_write_back(None)
    assert not lead_writeback.should_write_back("invoice-9")
    monkeypatch.setattr(lead_source.settings, "leads_api_token", "")
    assert not lead_writeback.should_write_back("web-1")


async def test_success_stamps_mission():
    mid = uuid.uuid4()
    client = FakeClient()
    ok = await lead_writeback.run_lead_writeback(mid, "web-1", client=client, session_factory=FakeSession)
    assert ok is True
    assert client.calls == [("web-1", str(mid))]
    assert len(FakeSession.executed) == 1 and FakeSession.committed == 1


@pytest.mark.parametrize("exc", [LeadSourceUnavailable("down"), LeadNotFound("gone")])
async def test_failure_returns_false_and_does_not_stamp(exc):
    ok = await lead_writeback.run_lead_writeback(
        uuid.uuid4(), "web-1", client=FakeClient(exc), session_factory=FakeSession
    )
    assert ok is False
    assert FakeSession.executed == [] and FakeSession.committed == 0


async def test_disabled_without_client_is_noop(monkeypatch):
    monkeypatch.setattr(lead_source.settings, "leads_api_base", "")
    ok = await lead_writeback.run_lead_writeback(uuid.uuid4(), "web-1", session_factory=FakeSession)
    assert ok is False and FakeSession.executed == []
```

- [ ] **Step 8: Run** `pytest tests/test_lead_writeback.py -v` — Expected: FAIL (module missing).

- [ ] **Step 9: Implement** — `backend/app/services/lead_writeback.py`

```python
"""Mark a website lead `won` after a mission is created from it (ADR-0050).

Runs AFTER the mission's transaction commits (FastAPI BackgroundTasks, or the
manual retry route). Never raises: a failure leaves missions.lead_writeback_at
NULL, which the mission Hub shows as "Lead not marked won — Retry".
"""
from __future__ import annotations

import logging
from datetime import datetime
from uuid import UUID

from sqlalchemy import update

from app.database import async_session
from app.models.mission import Mission
from app.services.lead_source import (
    LEAD_KEY_RE, LeadNotFound, LeadSourceClient, LeadSourceUnavailable, get_client, is_enabled,
)

logger = logging.getLogger("doc.leads")


def should_write_back(source_ref: str | None) -> bool:
    return bool(source_ref) and bool(LEAD_KEY_RE.match(source_ref)) and is_enabled()


async def run_lead_writeback(
    mission_id: UUID,
    lead_key: str,
    *,
    client: LeadSourceClient | None = None,
    session_factory=async_session,
) -> bool:
    if client is None:
        if not is_enabled():
            return False
        client = get_client()
    try:
        await client.mark_won(lead_key, str(mission_id))
    except (LeadSourceUnavailable, LeadNotFound) as exc:
        logger.warning("[LEAD-WRITEBACK] mission=%s lead=%s failed: %s", mission_id, lead_key, type(exc).__name__)
        return False
    async with session_factory() as session:
        await session.execute(
            update(Mission).where(Mission.id == mission_id).values(lead_writeback_at=datetime.utcnow())
        )
        await session.commit()
    logger.info("[LEAD-WRITEBACK] mission=%s lead=%s marked won", mission_id, lead_key)
    return True
```

- [ ] **Step 10: Run** `pytest tests/test_lead_writeback.py -v` — Expected: PASS.

- [ ] **Step 11: Failing router tests** — `backend/tests/test_leads_router.py`

```python
"""ADR-0050 /api/leads router."""
import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.services import lead_source
from app.services.lead_source import LeadNotFound, LeadSourceUnavailable

LEAD = {"key": "web-1", "name": "Ann", "email": "Ann@Acme.com ", "phone": "", "organization": "Acme",
        "service": "inspection", "details": "Roof", "created_at": "2026-10-01T00:00:00Z",
        "stage": "new", "is_open": True}


class _Result:
    def __init__(self, value):
        self._v = value

    def scalars(self):
        return self

    def first(self):
        return self._v


class _FakeSession:
    def __init__(self, results: list[Any]):
        self._results = list(results)
        self.statements = []

    async def execute(self, stmt):
        self.statements.append(stmt)
        return _Result(self._results.pop(0) if self._results else None)


class FakeClient:
    def __init__(self, exc=None, leads=None, lead=None):
        self.exc, self.leads, self.lead = exc, leads or [LEAD], lead or LEAD
        self.list_args = None

    async def list_leads(self, q=None, include_closed=False, limit=25):
        if self.exc:
            raise self.exc
        self.list_args = (q, include_closed, limit)
        return self.leads

    async def get_lead(self, key):
        if self.exc:
            raise self.exc
        return self.lead


def _app(monkeypatch, *, enabled=True, client=None, db_results=None, writeback=None):
    from app.auth.jwt import get_current_user
    from app.database import get_db
    from app.routers import leads as leads_router

    monkeypatch.setattr(lead_source.settings, "leads_api_base", "http://x" if enabled else "")
    monkeypatch.setattr(lead_source.settings, "leads_api_token", "t" if enabled else "")
    monkeypatch.setattr(leads_router, "get_client", lambda: client or FakeClient())
    if writeback is not None:
        monkeypatch.setattr(leads_router, "run_lead_writeback", writeback)
    app = FastAPI()
    app.include_router(leads_router.router)
    fake_db = _FakeSession(db_results or [])

    async def _db():
        yield fake_db

    async def _user():
        return SimpleNamespace(username="op@test", id=uuid.uuid4())

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user] = _user
    return TestClient(app), fake_db


def test_status_reports_enabled(monkeypatch):
    c, _ = _app(monkeypatch, enabled=False)
    assert c.get("/api/leads/status").json() == {"enabled": False}
    c, _ = _app(monkeypatch, enabled=True)
    assert c.get("/api/leads/status").json() == {"enabled": True}


@pytest.mark.parametrize("path", ["/api/leads", "/api/leads/web-1"])
def test_disabled_routes_404(monkeypatch, path):
    c, _ = _app(monkeypatch, enabled=False)
    assert c.get(path).status_code == 404


def test_list_passes_filters(monkeypatch):
    fc = FakeClient()
    c, _ = _app(monkeypatch, client=fc)
    r = c.get("/api/leads", params={"q": "acme", "include_closed": "true"})
    assert r.status_code == 200 and r.json() == {"leads": [LEAD]}
    assert fc.list_args == ("acme", True, 25)


def test_list_unavailable_is_503(monkeypatch):
    c, _ = _app(monkeypatch, client=FakeClient(exc=LeadSourceUnavailable("down")))
    r = c.get("/api/leads")
    assert r.status_code == 503 and r.json() == {"detail": "Leads unavailable"}


def test_get_lead_with_matching_customer(monkeypatch):
    cust = SimpleNamespace(id=uuid.uuid4(), name="Ann Existing")
    c, db = _app(monkeypatch, db_results=[cust])
    body = c.get("/api/leads/web-1").json()
    assert body["lead"]["key"] == "web-1"
    assert body["matching_customer"] == {"id": str(cust.id), "name": "Ann Existing"}
    # The lookup normalises case + whitespace ("Ann@Acme.com " → "ann@acme.com").
    compiled = str(db.statements[0].compile(compile_kwargs={"literal_binds": True}))
    assert "lower(customers.email)" in compiled and "'ann@acme.com'" in compiled


def test_get_lead_without_email_skips_match(monkeypatch):
    c, db = _app(monkeypatch, client=FakeClient(lead={**LEAD, "email": ""}))
    assert c.get("/api/leads/web-1").json()["matching_customer"] is None
    assert db.statements == []


def test_get_lead_bad_key_404(monkeypatch):
    c, _ = _app(monkeypatch)
    assert c.get("/api/leads/drop-table").status_code == 404


def test_get_lead_not_found_404(monkeypatch):
    c, _ = _app(monkeypatch, client=FakeClient(exc=LeadNotFound("x")))
    assert c.get("/api/leads/web-9").status_code == 404


def test_mark_won_retry(monkeypatch):
    calls = []

    async def ok(mission_id, key, **kw):
        calls.append((mission_id, key))
        return True

    c, _ = _app(monkeypatch, writeback=ok)
    mid = uuid.uuid4()
    r = c.post(f"/api/leads/web-1/mark-won", params={"mission_id": str(mid)})
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert calls == [(mid, "web-1")]


def test_mark_won_retry_failure_502(monkeypatch):
    async def fail(mission_id, key, **kw):
        return False

    c, _ = _app(monkeypatch, writeback=fail)
    r = c.post("/api/leads/web-1/mark-won", params={"mission_id": str(uuid.uuid4())})
    assert r.status_code == 502
```

- [ ] **Step 12: Run** `pytest tests/test_leads_router.py -v` — Expected: FAIL (router missing).

- [ ] **Step 13: Implement** — `backend/app/routers/leads.py`

```python
"""Website-lead prefill for missions/customers (ADR-0050).

Proxies the marketing API's DOC routes. Off (404) unless LEADS_API_BASE and
LEADS_API_TOKEN are set — never configured on the demo instance. Logs keys and
outcomes only, never contact details.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import get_current_user
from app.database import get_db
from app.models.customer import Customer
from app.models.user import User
from app.services.lead_source import (
    LEAD_KEY_RE, LeadNotFound, LeadSourceUnavailable, get_client, is_enabled,
)
from app.services.lead_writeback import run_lead_writeback

router = APIRouter(prefix="/api/leads", tags=["leads"])


def _require_enabled() -> None:
    if not is_enabled():
        raise HTTPException(status_code=404, detail="Not found")


def _check_key(key: str) -> None:
    if not LEAD_KEY_RE.match(key):
        raise HTTPException(status_code=404, detail="Lead not found")


@router.get("/status")
async def leads_status(_user: User = Depends(get_current_user)):
    return {"enabled": is_enabled()}


@router.get("", dependencies=[Depends(_require_enabled)])
async def list_leads(
    q: str | None = None,
    include_closed: bool = False,
    _user: User = Depends(get_current_user),
):
    try:
        leads = await get_client().list_leads(q=q, include_closed=include_closed, limit=25)
    except LeadSourceUnavailable:
        raise HTTPException(status_code=503, detail="Leads unavailable")
    return {"leads": leads}


@router.get("/{key}", dependencies=[Depends(_require_enabled)])
async def get_lead(
    key: str,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    _check_key(key)
    try:
        lead = await get_client().get_lead(key)
    except LeadNotFound:
        raise HTTPException(status_code=404, detail="Lead not found")
    except LeadSourceUnavailable:
        raise HTTPException(status_code=503, detail="Leads unavailable")
    email = (lead.get("email") or "").strip().lower()
    match = None
    if email:
        result = await db.execute(
            select(Customer)
            .where(func.lower(Customer.email) == email)
            .order_by(Customer.created_at)
            .limit(1)
        )
        cust = result.scalars().first()
        if cust is not None:
            match = {"id": str(cust.id), "name": cust.name}
    return {"lead": lead, "matching_customer": match}


@router.post("/{key}/mark-won", dependencies=[Depends(_require_enabled)])
async def mark_won(
    key: str,
    mission_id: UUID,
    _user: User = Depends(get_current_user),
):
    _check_key(key)
    if not await run_lead_writeback(mission_id, key):
        raise HTTPException(status_code=502, detail="Lead not marked won")
    return {"ok": True}
```

Note on the email match: `Customer.email` is stored as typed; `func.lower(Customer.email)` handles case but not stray whitespace on the stored side. Stored values come through the customer form, which does not pad. Accepted.

- [ ] **Step 14: Register the router** — `backend/app/main.py` line 23: append `, leads` to the `from app.routers import ...` list; after `app.include_router(missions.router)` add `app.include_router(leads.router)`.

- [ ] **Step 15: Run** `pytest tests/test_leads_router.py tests/test_lead_writeback.py tests/test_lead_integration_schema.py -v` — Expected: PASS. Then `pytest -q` (full backend suite) — Expected: PASS, no regressions.

- [ ] **Step 16: Commit**

```bash
cd ~/wt-doc-leads && git add backend/alembic/versions/0013_lead_integration.py backend/app/models backend/app/schemas backend/app/routers/customers.py backend/app/routers/leads.py backend/app/services/lead_writeback.py backend/app/main.py backend/tests/test_lead_integration_schema.py backend/tests/test_lead_writeback.py backend/tests/test_leads_router.py
git commit -m "feat(leads): /api/leads router, won write-back service, migration 0013 (ADR-0050)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PUcQC7c2MwDNfirUjqM5u8"
```

### Task 6: Schedule the write-back from mission create/update

**Files:**
- Modify: `backend/app/routers/missions.py` — `create_mission` (line ~259) and `update_mission` (line ~516)
- Test: `backend/tests/test_missions_lead_writeback.py`

**Interfaces:**
- Consumes: `should_write_back`, `run_lead_writeback` from Task 5.
- Produces: after a successful create (or a `source_ref` change on update) to a lead key, the mission transaction is committed and `run_lead_writeback(mission.id, source_ref)` is scheduled as a background task; on update the stale `lead_writeback_at` is cleared.

- [ ] **Step 1: Read the existing harness** — `backend/tests/test_missions_post_rejects_id_in_body.py` lines 60-160 (`_FakeSession`, `_build_app`). The new test copies that `_FakeSession` and adds `commit()`.

- [ ] **Step 2: Failing tests** — `backend/tests/test_missions_lead_writeback.py`

```python
"""ADR-0050 — mission create/update schedules the lead `won` write-back after commit."""
import uuid
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.services import lead_source


class _Result:
    def __init__(self, v):
        self._v = v

    def scalar(self):
        return 0

    def scalar_one(self):
        return self._v

    def scalar_one_or_none(self):
        return self._v


class _FakeSession:
    def __init__(self):
        self.added: list[Any] = []
        self.events: list[str] = []

    def add(self, obj):
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()
        self.added.append(obj)

    async def flush(self):
        self.events.append("flush")

    async def commit(self):
        self.events.append("commit")

    async def refresh(self, obj):
        pass

    async def execute(self, stmt):
        return _Result(self.added[-1] if self.added else None)


def _mission_obj(**over):
    now = datetime(2026, 10, 5)
    base = dict(id=uuid.uuid4(), customer_id=None, title="T", mission_type="other", description=None,
                mission_date=None, location_name=None, area_coordinates=None, status="draft",
                is_billable=False, source="website", source_ref="web-1", unas_folder_path=None,
                download_link_url=None, download_link_expires_at=None, client_notes=None,
                lead_writeback_at=None, created_at=now, updated_at=now, flights=[], images=[])
    base.update(over)
    return SimpleNamespace(**base)


@pytest.fixture
def harness(monkeypatch):
    from app.auth.jwt import get_current_user
    from app.database import get_db
    from app.routers import missions as missions_router

    monkeypatch.setattr(lead_source.settings, "leads_api_base", "http://x")
    monkeypatch.setattr(lead_source.settings, "leads_api_token", "t")
    calls = []

    async def fake_writeback(mission_id, key, **kw):
        calls.append((mission_id, key))
        return True

    monkeypatch.setattr(missions_router, "run_lead_writeback", fake_writeback)
    app = FastAPI()
    app.include_router(missions_router.router)
    db = _FakeSession()

    async def _db():
        yield db

    async def _user():
        return SimpleNamespace(username="op@test", id=uuid.uuid4())

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user] = _user
    return TestClient(app), db, calls, missions_router


def test_create_with_lead_key_commits_then_schedules(harness):
    client, db, calls, _ = harness
    r = client.post("/api/missions", json={"title": "Roof — Acme", "source": "website", "source_ref": "web-1"})
    assert r.status_code == 201, r.text
    assert "commit" in db.events
    assert len(calls) == 1 and calls[0][1] == "web-1"


def test_create_without_lead_key_does_not_schedule(harness):
    client, db, calls, _ = harness
    r = client.post("/api/missions", json={"title": "Walk-in", "source": "phone"})
    assert r.status_code == 201, r.text
    assert calls == [] and "commit" not in db.events


def test_create_with_feature_off_does_not_schedule(harness, monkeypatch):
    client, db, calls, _ = harness
    monkeypatch.setattr(lead_source.settings, "leads_api_token", "")
    r = client.post("/api/missions", json={"title": "X", "source_ref": "web-1"})
    assert r.status_code == 201 and calls == []


def test_update_changing_source_ref_schedules_and_clears_stamp(harness):
    client, db, calls, _ = harness
    existing = _mission_obj(source_ref=None, lead_writeback_at=datetime(2026, 1, 1))
    db.added.append(existing)
    r = client.put(f"/api/missions/{existing.id}", json={"source_ref": "cold-7"})
    assert r.status_code == 200, r.text
    assert existing.lead_writeback_at is None
    assert calls == [(existing.id, "cold-7")]


def test_update_same_source_ref_does_not_reschedule(harness):
    client, db, calls, _ = harness
    existing = _mission_obj(source_ref="web-1", lead_writeback_at=datetime(2026, 1, 1))
    db.added.append(existing)
    r = client.put(f"/api/missions/{existing.id}", json={"source_ref": "web-1", "title": "Renamed"})
    assert r.status_code == 200, r.text
    assert calls == []
```

If serializing the created ORM `Mission` fails because `created_at`/`updated_at`/`status` are `None` (the fake session never runs DB defaults), set them in `_FakeSession.add` exactly the way `test_missions_post_rejects_id_in_body.py`'s fake does — copy its approach rather than inventing one. If `MissionResponse.model_validate` rejects the `SimpleNamespace` fields (e.g. `mission_type` must be the enum), use `mission_type=MissionType.OTHER` and `status=MissionStatus.DRAFT` imported from `app.models.mission` — check `test_missions_post_rejects_id_in_body.py` for the exact values it uses and match them.

- [ ] **Step 3: Run** `pytest tests/test_missions_lead_writeback.py -v` — Expected: FAIL (`run_lead_writeback` not an attribute of `app.routers.missions`, nothing scheduled).

- [ ] **Step 4: Implement in `missions.py`.**
  1. Imports: add `BackgroundTasks` to the `from fastapi import ...` line; add `from app.services.lead_writeback import run_lead_writeback, should_write_back`.
  2. `create_mission` signature — add `background_tasks: BackgroundTasks,` as the first parameter after `request: Request,`.
  3. In `create_mission`, after the portal-email block (`if mission.customer_id: await _send_portal_email_for_mission(...)`) and before the return, add:

```python
        # ADR-0050 — mark the website lead `won`. Commit FIRST so the lead is never
        # marked for a mission that then fails to persist; the write-back runs after
        # the response and can never fail this request.
        if should_write_back(mission.source_ref):
            await db.commit()
            background_tasks.add_task(run_lead_writeback, mission.id, mission.source_ref)
```

  4. `update_mission` signature — add `background_tasks: BackgroundTasks,` after `data: MissionUpdate,`.
  5. In `update_mission`, right after `old_download_link_url = mission.download_link_url` add `old_source_ref = mission.source_ref`. After the `for key, value in update_fields.items(): ...` loop add:

```python
        # ADR-0050 — a new lead link re-arms the `won` write-back.
        lead_changed = (
            "source_ref" in update_fields
            and (mission.source_ref or None) != (old_source_ref or None)
            and should_write_back(mission.source_ref)
        )
        if lead_changed:
            mission.lead_writeback_at = None
```

  6. Just before `return _serialize_mission(mission)` in `update_mission` add:

```python
        if lead_changed:
            await db.commit()
            background_tasks.add_task(run_lead_writeback, mission.id, mission.source_ref)
```

- [ ] **Step 5: Run** `pytest tests/test_missions_lead_writeback.py -v` — Expected: PASS. Then `pytest -q` — Expected: full suite PASS (existing mission tests never set a lead-key `source_ref`, so their fake sessions never see `commit()`).

- [ ] **Step 6: Commit**

```bash
cd ~/wt-doc-leads && git add backend/app/routers/missions.py backend/tests/test_missions_lead_writeback.py
git commit -m "feat(leads): schedule lead won write-back after mission commit (ADR-0050)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PUcQC7c2MwDNfirUjqM5u8"
```

---

## Part C — DOC repo frontend

### Task 7: Lead API helpers + `LeadPicker` component

**Files:**
- Create: `frontend/src/api/leads.ts`
- Modify: `frontend/src/api/types.ts` (`Customer` +`source_ref: string | null`; `Mission` +`lead_writeback_at?: string | null`)
- Create: `frontend/src/components/LeadPicker.tsx`
- Test: `frontend/src/components/__tests__/LeadPicker.test.tsx`

**Interfaces:**
- Consumes: Task 5 HTTP contract.
- Produces:
  - `interface DocLead { key: string; name: string; email: string; phone: string; organization: string; service: string; details: string; created_at: string | null; stage: string; is_open: boolean }`
  - `interface LeadDetail { lead: DocLead; matching_customer: { id: string; name: string } | null }`
  - `fetchLeadsEnabled(): Promise<boolean>` (false on any error)
  - `listLeads(q: string, includeClosed: boolean): Promise<DocLead[]>`
  - `getLead(key: string): Promise<LeadDetail>`
  - `retryLeadWon(key: string, missionId: string): Promise<void>`
  - `missionTitleFromLead(lead: DocLead): string`
  - `leadPortalUrl(key: string): string`
  - `LEAD_KEY_RE: RegExp`
  - `<LeadPicker onPick={(detail: LeadDetail) => void} />` — renders nothing when the feature is off.

- [ ] **Step 1: Failing tests** — `frontend/src/components/__tests__/LeadPicker.test.tsx`

```tsx
import { describe, it, expect, beforeAll, afterAll, afterEach, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { setupServer } from 'msw/node';
import { http, HttpResponse } from 'msw';

import LeadPicker from '../LeadPicker';
import { missionTitleFromLead, leadPortalUrl } from '../../api/leads';
import TestProviders from '../../test/TestProviders';

const LEAD = {
  key: 'web-1', name: 'Ann Open', email: 'ann@acme.com', phone: '5415550100',
  organization: 'Acme', service: 'inspection', details: 'Roof survey',
  created_at: '2026-10-01T10:00:00Z', stage: 'new', is_open: true,
};
const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

describe('missionTitleFromLead', () => {
  it('uses service and organization', () => {
    expect(missionTitleFromLead(LEAD)).toBe('Inspection — Acme');
  });
  it('falls back to name and a generic service', () => {
    expect(missionTitleFromLead({ ...LEAD, organization: '', service: '' })).toBe('Website lead — Ann Open');
  });
  it('builds the portal deep link', () => {
    expect(leadPortalUrl('cold-7')).toBe('https://marketing.barnardhq.com/outbound/leads?lead=cold-7');
  });
});

describe('LeadPicker', () => {
  it('renders nothing when the feature is off', async () => {
    server.use(http.get('*/api/leads/status', () => HttpResponse.json({ enabled: false })));
    const { container } = render(<TestProviders><LeadPicker onPick={vi.fn()} /></TestProviders>);
    await waitFor(() => expect(container.querySelector('[data-testid="lead-picker"]')).toBeNull());
  });

  it('lists leads and returns the detail on pick', async () => {
    const onPick = vi.fn();
    server.use(
      http.get('*/api/leads/status', () => HttpResponse.json({ enabled: true })),
      http.get('*/api/leads', () => HttpResponse.json({ leads: [LEAD] })),
      http.get('*/api/leads/web-1', () =>
        HttpResponse.json({ lead: LEAD, matching_customer: null })),
    );
    render(<TestProviders><LeadPicker onPick={onPick} /></TestProviders>);
    const input = await screen.findByLabelText(/start from a lead/i);
    await userEvent.click(input);
    await userEvent.click(await screen.findByText(/Ann Open — Acme/));
    await waitFor(() => expect(onPick).toHaveBeenCalledWith({ lead: LEAD, matching_customer: null }));
  });

  it('shows "Leads unavailable" when the list call fails', async () => {
    server.use(
      http.get('*/api/leads/status', () => HttpResponse.json({ enabled: true })),
      http.get('*/api/leads', () => HttpResponse.json({ detail: 'Leads unavailable' }, { status: 503 })),
    );
    render(<TestProviders><LeadPicker onPick={vi.fn()} /></TestProviders>);
    expect(await screen.findByText(/leads unavailable/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run** `cd ~/wt-doc-leads/frontend && npx vitest run src/components/__tests__/LeadPicker.test.tsx` — Expected: FAIL (modules missing).

- [ ] **Step 3: API helpers** — `frontend/src/api/leads.ts`

```ts
// ADR-0050 — website-lead prefill. Thin wrappers over DOC's /api/leads proxy.
import api from './client';

export interface DocLead {
  key: string;
  name: string;
  email: string;
  phone: string;
  organization: string;
  service: string;
  details: string;
  created_at: string | null;
  stage: string;
  is_open: boolean;
}

export interface LeadDetail {
  lead: DocLead;
  matching_customer: { id: string; name: string } | null;
}

export const LEAD_KEY_RE = /^(web|cold)-\d{1,12}$/;

export async function fetchLeadsEnabled(): Promise<boolean> {
  try {
    const r = await api.get('/leads/status');
    return r.data?.enabled === true;
  } catch {
    return false;
  }
}

export async function listLeads(q: string, includeClosed: boolean): Promise<DocLead[]> {
  const r = await api.get('/leads', { params: { q: q || undefined, include_closed: includeClosed } });
  return r.data.leads as DocLead[];
}

export async function getLead(key: string): Promise<LeadDetail> {
  const r = await api.get(`/leads/${encodeURIComponent(key)}`);
  return r.data as LeadDetail;
}

export async function retryLeadWon(key: string, missionId: string): Promise<void> {
  await api.post(`/leads/${encodeURIComponent(key)}/mark-won`, null, { params: { mission_id: missionId } });
}

function titleCase(s: string): string {
  return s.replace(/[_-]+/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

export function missionTitleFromLead(lead: DocLead): string {
  const what = lead.service.trim() ? titleCase(lead.service.trim()) : 'Website lead';
  const who = lead.organization.trim() || lead.name.trim() || lead.email.trim();
  return who ? `${what} — ${who}` : what;
}

export function leadPortalUrl(key: string): string {
  return `https://marketing.barnardhq.com/outbound/leads?lead=${encodeURIComponent(key)}`;
}
```

- [ ] **Step 4: Types** — `frontend/src/api/types.ts`: add `source_ref: string | null;` to `Customer` (after `notes`), and `lead_writeback_at?: string | null;` to `Mission` (after its `source_ref`, line ~96).

- [ ] **Step 5: Component** — `frontend/src/components/LeadPicker.tsx`

```tsx
/**
 * LeadPicker (ADR-0050) — "Start from a lead" for the new-mission modal and the
 * new-customer form. Renders nothing unless GET /api/leads/status says enabled.
 * Open website leads by default; "Show closed" includes won/lost ones.
 */
import { useEffect, useState } from 'react';
import { Alert, Group, Select, Switch, Text } from '@mantine/core';
import { useDebouncedValue } from '@mantine/hooks';

import { fetchLeadsEnabled, getLead, listLeads, type DocLead, type LeadDetail } from '../api/leads';

interface Props {
  onPick: (detail: LeadDetail) => void;
}

function ago(iso: string | null): string {
  if (!iso) return '';
  const d = Math.floor((Date.now() - Date.parse(iso)) / 86400000);
  return Number.isFinite(d) ? (d <= 0 ? 'today' : `${d} d ago`) : '';
}

export function leadLabel(l: DocLead): string {
  return [l.name, l.organization, l.service, ago(l.created_at)].filter(Boolean).join(' — ');
}

export default function LeadPicker({ onPick }: Props) {
  const [enabled, setEnabled] = useState(false);
  const [leads, setLeads] = useState<DocLead[]>([]);
  const [search, setSearch] = useState('');
  const [debounced] = useDebouncedValue(search, 300);
  const [includeClosed, setIncludeClosed] = useState(false);
  const [error, setError] = useState(false);
  const [value, setValue] = useState<string | null>(null);

  useEffect(() => {
    fetchLeadsEnabled().then(setEnabled);
  }, []);

  useEffect(() => {
    if (!enabled) return;
    listLeads(debounced, includeClosed)
      .then((l) => { setLeads(l); setError(false); })
      .catch(() => { setLeads([]); setError(true); });
  }, [enabled, debounced, includeClosed]);

  if (!enabled) return null;

  const pick = async (key: string | null) => {
    setValue(key);
    if (!key) return;
    try {
      onPick(await getLead(key));
    } catch {
      setError(true);
    }
  };

  return (
    <div data-testid="lead-picker">
      {error && (
        <Alert color="yellow" variant="light" mb="xs">
          Leads unavailable — fill the form in by hand.
        </Alert>
      )}
      <Select
        label="Start from a lead (optional)"
        placeholder="Search website leads"
        data={leads.map((l) => ({ value: l.key, label: leadLabel(l) }))}
        searchable
        clearable
        searchValue={search}
        onSearchChange={setSearch}
        filter={({ options }) => options}
        value={value}
        onChange={pick}
        nothingFoundMessage={error ? 'Leads unavailable' : 'No matching leads'}
      />
      <Group justify="flex-end" mt={4}>
        <Switch
          size="xs"
          label={<Text size="xs">Show closed leads</Text>}
          checked={includeClosed}
          onChange={(e) => setIncludeClosed(e.currentTarget.checked)}
        />
      </Group>
    </div>
  );
}
```

(`filter={({ options }) => options}` disables Mantine's client-side filter because the server already filtered by `q`. Verify `@mantine/hooks` is a dependency: `grep -n '"@mantine/hooks"' frontend/package.json`.)

- [ ] **Step 6: Run** the LeadPicker test — Expected: PASS. If the Select option text differs because of `ago()`, the test's `/Ann Open — Acme/` regex still matches the prefix.

- [ ] **Step 7: Commit**

```bash
cd ~/wt-doc-leads && git add frontend/src/api/leads.ts frontend/src/api/types.ts frontend/src/components/LeadPicker.tsx frontend/src/components/__tests__/LeadPicker.test.tsx
git commit -m "feat(leads): LeadPicker + /api/leads client helpers (ADR-0050)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PUcQC7c2MwDNfirUjqM5u8"
```

### Task 8: New-mission modal prefill (existing or new customer)

**Files:**
- Modify: `frontend/src/components/MissionCreateModal.tsx`
- Modify: `frontend/src/components/__tests__/MissionCreateModal.test.tsx`

**Interfaces:**
- Consumes: `LeadPicker`, `LeadDetail`, `missionTitleFromLead` (Task 7); backend `POST /customers` accepting `source_ref` (Task 5); `POST /missions` with `description`, `source`, `source_ref` (existing).
- Produces: request sequence on submit from a lead without a matching customer: `POST /api/customers {name,email,phone,company,source_ref}` → `POST /api/missions {title, mission_type, customer_id, description, source:'website', source_ref}`. With a matching customer (default): only `POST /api/missions` with `customer_id = match.id`.

- [ ] **Step 1: Keep the existing tests green first.** In `MissionCreateModal.test.tsx` add to the `setupServer(...)` handler list:

```tsx
  http.get('*/api/leads/status', () => HttpResponse.json({ enabled: false })),
```

Run `npx vitest run src/components/__tests__/MissionCreateModal.test.tsx` — Expected: still PASS (status handler only; modal unchanged yet).

- [ ] **Step 2: Add failing tests** to the same file (new `describe` block). They override the status handler per test with `server.use(...)`:

```tsx
describe('MissionCreateModal — start from a lead (ADR-0050)', () => {
  const LEAD = {
    key: 'web-1', name: 'Ann Open', email: 'ann@acme.com', phone: '5415550100',
    organization: 'Acme', service: 'inspection', details: 'Roof survey',
    created_at: '2026-10-01T10:00:00Z', stage: 'new', is_open: true,
  };
  let customerBody: any = null;

  function useLeadHandlers(match: { id: string; name: string } | null) {
    server.use(
      http.get('*/api/leads/status', () => HttpResponse.json({ enabled: true })),
      http.get('*/api/leads', () => HttpResponse.json({ leads: [LEAD] })),
      http.get('*/api/leads/web-1', () => HttpResponse.json({ lead: LEAD, matching_customer: match })),
      http.post('*/api/customers', async ({ request }) => {
        customerBody = await request.json();
        return HttpResponse.json({ id: 'cust-new', name: customerBody.name }, { status: 201 });
      }),
    );
  }

  async function pickLead() {
    await userEvent.click(await screen.findByLabelText(/start from a lead/i));
    await userEvent.click(await screen.findByText(/Ann Open — Acme/));
  }

  afterEach(() => { customerBody = null; });

  it('reuses the matching customer by default', async () => {
    useLeadHandlers({ id: 'cust-1', name: 'Casey Operator' });
    render(<TestProviders><MissionCreateModal opened onClose={vi.fn()} /></TestProviders>);
    await pickLead();
    expect(await screen.findByText(/matches existing customer casey operator/i)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /create mission/i }));
    await waitFor(() => expect(lastBody).not.toBeNull());
    expect(lastBody).toMatchObject({
      title: 'Inspection — Acme', customer_id: 'cust-1', description: 'Roof survey',
      source: 'website', source_ref: 'web-1',
    });
    expect(customerBody).toBeNull();
    expect(lastBody).not.toHaveProperty('id');
  });

  it('creates the customer first when there is no match', async () => {
    useLeadHandlers(null);
    render(<TestProviders><MissionCreateModal opened onClose={vi.fn()} /></TestProviders>);
    await pickLead();
    await userEvent.click(await screen.findByRole('button', { name: /create mission/i }));
    await waitFor(() => expect(lastBody).not.toBeNull());
    expect(customerBody).toEqual({
      name: 'Ann Open', email: 'ann@acme.com', phone: '5415550100', company: 'Acme', source_ref: 'web-1',
    });
    expect(lastBody.customer_id).toBe('cust-new');
  });

  it('operator can choose "Create new" despite a match', async () => {
    useLeadHandlers({ id: 'cust-1', name: 'Casey Operator' });
    render(<TestProviders><MissionCreateModal opened onClose={vi.fn()} /></TestProviders>);
    await pickLead();
    await userEvent.click(await screen.findByRole('radio', { name: /create new customer/i }));
    await userEvent.click(screen.getByRole('button', { name: /create mission/i }));
    await waitFor(() => expect(lastBody).not.toBeNull());
    expect(customerBody).not.toBeNull();
    expect(lastBody.customer_id).toBe('cust-new');
  });
});
```

- [ ] **Step 3: Run** — Expected: the three new tests FAIL (no picker in the modal).

- [ ] **Step 4: Implement in `MissionCreateModal.tsx`.**
  1. Imports: add `Alert, Radio, Textarea` to the `@mantine/core` import; add `import LeadPicker from './LeadPicker';` and `import { missionTitleFromLead, type LeadDetail } from '../api/leads';`.
  2. State: `const [lead, setLead] = useState<LeadDetail | null>(null);`
  3. `initialValues` gains: `description: ''`, `source_ref: ''`, `customer_mode: 'existing' as 'existing' | 'new'`, `new_name: ''`, `new_email: ''`, `new_phone: ''`, `new_company: ''`.
  4. In the open `useEffect`, after `form.reset();` add `setLead(null);`.
  5. Pick handler:

```tsx
  const applyLead = (d: LeadDetail) => {
    setLead(d);
    const l = d.lead;
    form.setValues({
      title: missionTitleFromLead(l),
      description: l.details,
      source: 'website',
      source_ref: l.key,
      customer_mode: d.matching_customer ? 'existing' : 'new',
      customer_id: d.matching_customer?.id ?? '',
      new_name: l.name,
      new_email: l.email,
      new_phone: l.phone,
      new_company: l.organization,
    });
  };
```

  6. In `handleSubmit`, before building `payload`, resolve the customer:

```tsx
      let customerId = values.customer_id;
      if (lead && values.customer_mode === 'new') {
        const c = await api.post('/customers', {
          name: values.new_name.trim() || lead.lead.email,
          email: values.new_email.trim() || null,
          phone: values.new_phone.replace(/\D/g, '') || null,
          company: values.new_company.trim() || null,
          source_ref: values.source_ref,
        });
        customerId = c.data?.id;
      }
```

  then use `if (customerId) payload.customer_id = customerId;` in place of the existing `values.customer_id` line, and add:

```tsx
      if (values.description.trim()) payload.description = values.description.trim();
      if (values.source_ref) payload.source_ref = values.source_ref;
```

  7. JSX — first child of the `<Stack>`: `<LeadPicker onPick={applyLead} />`. After the Title input, when `lead` is set, render:

```tsx
          {lead && lead.matching_customer && (
            <Alert color="cyan" variant="light">
              Matches existing customer {lead.matching_customer.name}
            </Alert>
          )}
          {lead && (
            <Radio.Group
              label="Customer"
              value={form.values.customer_mode}
              onChange={(v) => form.setFieldValue('customer_mode', v as 'existing' | 'new')}
            >
              <Group mt={4}>
                {lead.matching_customer && <Radio value="existing" label={`Use ${lead.matching_customer.name}`} />}
                <Radio value="new" label="Create new customer" />
              </Group>
            </Radio.Group>
          )}
          {lead && form.values.customer_mode === 'new' && (
            <Group grow>
              <TextInput label="Name" {...form.getInputProps('new_name')} />
              <TextInput label="Email" {...form.getInputProps('new_email')} />
              <TextInput label="Phone" {...form.getInputProps('new_phone')} />
              <TextInput label="Company" {...form.getInputProps('new_company')} />
            </Group>
          )}
          {lead && (
            <Textarea label="Description" autosize minRows={2} {...form.getInputProps('description')} />
          )}
```

  and wrap the existing customer `<Select>` in `{(!lead || form.values.customer_mode === 'existing') && (...)}` so the operator can still pick a different existing customer.

  Use `Stack` instead of `Group grow` for the four new-customer inputs if they overflow at 375 px — Task 10 checks this by eye.

- [ ] **Step 5: Run** `npx vitest run src/components/__tests__/MissionCreateModal.test.tsx` — Expected: all (old + new) PASS.

- [ ] **Step 6: Commit**

```bash
cd ~/wt-doc-leads && git add frontend/src/components/MissionCreateModal.tsx frontend/src/components/__tests__/MissionCreateModal.test.tsx
git commit -m "feat(leads): start a mission from a website lead (ADR-0050)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PUcQC7c2MwDNfirUjqM5u8"
```

### Task 9: New-customer form + mission Hub lead status

**Files:**
- Modify: `frontend/src/pages/Customers.tsx` (form `initialValues` line ~90, `handleSubmit` ~149, modal JSX ~425)
- Create: `frontend/src/components/MissionLeadStatus.tsx`
- Test: `frontend/src/components/__tests__/MissionLeadStatus.test.tsx`
- Modify: `frontend/src/pages/MissionDetail.tsx` (`detailsSummary`, line ~247)

**Interfaces:**
- Consumes: `LeadPicker`, `leadPortalUrl`, `retryLeadWon`, `LEAD_KEY_RE` (Task 7).
- Produces: `<MissionLeadStatus missionId sourceRef leadWritebackAt onRetried />`.

- [ ] **Step 1: Failing tests** — `frontend/src/components/__tests__/MissionLeadStatus.test.tsx`

```tsx
import { describe, it, expect, beforeAll, afterAll, afterEach, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { setupServer } from 'msw/node';
import { http, HttpResponse } from 'msw';

import MissionLeadStatus from '../MissionLeadStatus';
import TestProviders from '../../test/TestProviders';

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

describe('MissionLeadStatus', () => {
  it('renders nothing for a non-lead source_ref', () => {
    const { container } = render(<TestProviders>
      <MissionLeadStatus missionId="m1" sourceRef="INV-9" leadWritebackAt={null} onRetried={vi.fn()} />
    </TestProviders>);
    expect(container.querySelector('[data-testid="mission-lead-status"]')).toBeNull();
  });

  it('shows the View lead link and no warning once written back', () => {
    render(<TestProviders>
      <MissionLeadStatus missionId="m1" sourceRef="web-1" leadWritebackAt="2026-10-05T00:00:00Z" onRetried={vi.fn()} />
    </TestProviders>);
    expect(screen.getByRole('link', { name: /view lead/i })).toHaveAttribute(
      'href', 'https://marketing.barnardhq.com/outbound/leads?lead=web-1');
    expect(screen.queryByText(/not marked won/i)).toBeNull();
  });

  it('retries and calls onRetried on success', async () => {
    let hit = '';
    server.use(http.post('*/api/leads/web-1/mark-won', ({ request }) => {
      hit = request.url;
      return HttpResponse.json({ ok: true });
    }));
    const onRetried = vi.fn();
    render(<TestProviders>
      <MissionLeadStatus missionId="m1" sourceRef="web-1" leadWritebackAt={null} onRetried={onRetried} />
    </TestProviders>);
    expect(screen.getByText(/lead not marked won/i)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /retry/i }));
    await waitFor(() => expect(onRetried).toHaveBeenCalled());
    expect(hit).toContain('mission_id=m1');
  });
});
```

- [ ] **Step 2: Run** — Expected: FAIL (component missing).

- [ ] **Step 3: Implement** — `frontend/src/components/MissionLeadStatus.tsx`

```tsx
/** ADR-0050 — mission Hub: link back to the website lead + won write-back status. */
import { useState } from 'react';
import { Anchor, Button, Group, Text } from '@mantine/core';
import { notifications } from '@mantine/notifications';

import { LEAD_KEY_RE, leadPortalUrl, retryLeadWon } from '../api/leads';

interface Props {
  missionId: string;
  sourceRef: string | null | undefined;
  leadWritebackAt: string | null | undefined;
  onRetried: () => void;
}

export default function MissionLeadStatus({ missionId, sourceRef, leadWritebackAt, onRetried }: Props) {
  const [busy, setBusy] = useState(false);
  if (!sourceRef || !LEAD_KEY_RE.test(sourceRef)) return null;

  const retry = async () => {
    setBusy(true);
    try {
      await retryLeadWon(sourceRef, missionId);
      notifications.show({ title: 'Lead updated', message: 'Marked won in the marketing pipeline', color: 'cyan' });
      onRetried();
    } catch {
      notifications.show({ title: 'Still not marked', message: 'Marketing API unavailable — try again later', color: 'red' });
    } finally {
      setBusy(false);
    }
  };

  return (
    <Group gap="xs" wrap="wrap" data-testid="mission-lead-status">
      <Anchor href={leadPortalUrl(sourceRef)} target="_blank" rel="noopener noreferrer" size="xs">
        View lead
      </Anchor>
      {!leadWritebackAt && (
        <>
          <Text c="yellow" size="xs">Lead not marked won</Text>
          <Button size="compact-xs" variant="light" color="yellow" loading={busy} onClick={retry}>
            Retry
          </Button>
        </>
      )}
    </Group>
  );
}
```

- [ ] **Step 4: Run** — Expected: PASS.

- [ ] **Step 5: Mount on the Hub.** In `MissionDetail.tsx` import `MissionLeadStatus from '../components/MissionLeadStatus';` and, inside `detailsSummary`'s `<Stack gap={2}>` after the customer block, add:

```tsx
      <MissionLeadStatus
        missionId={mission.id}
        sourceRef={mission.source_ref}
        leadWritebackAt={mission.lead_writeback_at}
        onRetried={reload}
      />
```

  The mission fixture in `MissionDetail.hub.test.tsx` has no `source_ref`, so the component renders nothing there; run `npx vitest run src/pages/__tests__/MissionDetail.hub.test.tsx` — Expected: PASS unchanged.

- [ ] **Step 6: Customer form.** In `Customers.tsx`:
  1. `import LeadPicker from '../components/LeadPicker';` and `import { leadPortalUrl, type LeadDetail } from '../api/leads';`
  2. Add `source_ref: ''` to the form `initialValues`, and state `const [leadMatch, setLeadMatch] = useState<LeadDetail['matching_customer']>(null);`.
  3. Handler:

```tsx
  const applyLead = (d: LeadDetail) => {
    const l = d.lead;
    form.setValues({
      ...form.values,
      name: l.name, email: l.email, phone: formatPhone(l.phone), company: l.organization,
      source_ref: l.key,
    });
    setLeadMatch(d.matching_customer);
  };
```

  4. In `handleSubmit`, change the payload line to `const payload = { ...values, phone: values.phone.replace(/\D/g, '') || null, source_ref: values.source_ref || null };`, and after the save also `setLeadMatch(null)`.
  5. In the modal JSX, first child of `<Stack gap="sm">`, only for a new customer:

```tsx
            {!editingId && <LeadPicker onPick={applyLead} />}
            {!editingId && leadMatch && (
              <Alert color="cyan" variant="light">
                Matches existing customer {leadMatch.name}.{' '}
                <Anchor
                  component="button"
                  type="button"
                  onClick={() => {
                    const existing = customers.find((c) => c.id === leadMatch.id);
                    if (existing) { setLeadMatch(null); handleEdit(existing); }
                  }}
                >
                  Edit that customer instead
                </Anchor>
              </Alert>
            )}
            {editingId && form.values.source_ref && (
              <Anchor href={leadPortalUrl(form.values.source_ref)} target="_blank" rel="noopener noreferrer" size="xs">
                View lead
              </Anchor>
            )}
```

  Add `Alert`, `Anchor` to the `@mantine/core` import if not present. In `handleEdit`, include `source_ref: customer.source_ref ?? ''` in `form.setValues({...})`. On closing the modal (`onClose`), also `setLeadMatch(null)`.

  Decision recorded: on the customer form, "reuse is the default" is expressed as the banner's **Edit that customer instead** action, because a customer form has nothing to attach to — saving still creates a new customer if the operator ignores the banner.

- [ ] **Step 7: Type-check + full frontend tests** — `cd ~/wt-doc-leads/frontend && npx tsc --noEmit && npx vitest run` — Expected: no type errors, all tests PASS.

- [ ] **Step 8: Commit**

```bash
cd ~/wt-doc-leads && git add frontend/src/components/MissionLeadStatus.tsx frontend/src/components/__tests__/MissionLeadStatus.test.tsx frontend/src/pages/MissionDetail.tsx frontend/src/pages/Customers.tsx
git commit -m "feat(leads): customer form lead prefill + mission Hub lead link/retry (ADR-0050)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PUcQC7c2MwDNfirUjqM5u8"
```

---

## Part D — docs, ship, verify

### Task 10: DOC docs + version, PRs, deploy, live verification

**Files:**
- Create: `docs/adr/0050-website-lead-prefill-and-won-writeback.md`
- Modify: `docs/adr/README.md` (new row; also correct the stale "alembic head is 0011" note to `0013_lead_integration`), `CHANGELOG.md` (new top entry under `# Changelog`), `ROADMAP.md` (LD-1 status), `backend/app/version.py`, `backend/app/main.py`, `frontend/package.json`

- [ ] **Step 1: ADR-0050** — sections: Status (Accepted 2026-10-05), Context (retyping leads; ADR-0016 reserved `source_ref`), Decision (summary of spec §3–§6 incl. canonical `cold-*` keys and the host-gateway path), Alternatives (D1 direct; one-click convert; attribution snapshot — each with the reason from the spec), Consequences (demo must never get `LEADS_API_*`; token rotation steps; `lead_writeback_at` semantics), Failover self-check (feature degrades to manual entry if the marketing API or BOS-HQ marketing stack is down; no new replicated state beyond two nullable columns).

- [ ] **Step 2: Index row** in `docs/adr/README.md`, matching the existing row format:

```
| [0050](0050-website-lead-prefill-and-won-writeback.md) | Website-lead prefill for missions/customers + `won` write-back | Accepted — v2.97.0 | 2026-10-05 | Amends [0016](0016-mission-source-attribution.md) (`source_ref` now carries the lead key); counterpart marketing ADR-0110 |
```

- [ ] **Step 3: CHANGELOG** top entry `## 2026-10-05 — v2.97.0: start missions/customers from a website lead (ADR-0050)` with bullets: picker in both forms; customer email match; `won` write-back + Hub retry; migration `0013_lead_integration`; feature off unless `LEADS_API_*` set (never on demo).

- [ ] **Step 4: Version** `2.96.0 → 2.97.0` in `backend/app/version.py`, `backend/app/main.py`, `frontend/package.json`. Run `cd backend && pytest tests/test_app_version_parity.py -v` — Expected: PASS.

- [ ] **Step 5: ROADMAP** — LD-1 heading status `**SHIPPED v2.97.0 — verifying live**` (final wording set in Step 13).

- [ ] **Step 6: Full test runs**
  - `cd ~/wt-doc-leads/backend && pytest -q` — PASS
  - `cd ~/wt-doc-leads/frontend && npx vitest run && npx tsc --noEmit` — PASS
  - `ssh localhost 'cd ~/wt-doc-leads/frontend && npm run build'` — build succeeds
  - `cd ~/wt-mkt-leads && node --test api/doc-leads.test.js && (cd dashboard && npx vitest run && npx tsc --noEmit -p .)` — PASS

- [ ] **Step 7: Commit docs + version, push both branches, open PRs.**

```bash
cd ~/wt-doc-leads && git add docs ROADMAP.md CHANGELOG.md backend/app/version.py backend/app/main.py frontend/package.json
git commit -m "docs(leads): ADR-0050, CHANGELOG, ROADMAP; v2.97.0

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PUcQC7c2MwDNfirUjqM5u8"
git push -u origin feat/lead-to-mission-spec
cd ~/wt-mkt-leads && git push -u origin feat/doc-leads-integration
```

Open one PR per repo with `gh pr create` (body ends with the Claude Code attribution block). **Merge only when every check on the PR is green** (pending ≠ green).

- [ ] **Step 8: Mint and place the token (before either merge deploys).**

```bash
T=$(openssl rand -hex 32)
op item create --vault Fleet --category "API Credential" --title "DOC ↔ marketing leads token" "credential=$T" >/dev/null
# Token travels on stdin, never on a remote command line.
printf 'DOC_LEADS_TOKEN=%s\n' "$T" | ssh -i ~/.ssh/deploy-bos bbarnard065@10.99.0.4 'cd ~/marketing && grep -q "^DOC_LEADS_TOKEN=" .env || cat >> .env; chmod 600 .env'
printf 'LEADS_API_BASE=http://host.docker.internal:3002\nLEADS_API_TOKEN=%s\n' "$T" | ssh -i ~/.ssh/deploy-bos bbarnard065@10.99.0.4 'cd ~/droneops && grep -q "^LEADS_API_TOKEN=" .env || cat >> .env; chmod 600 .env'
unset T
```

Confirm `~/droneops-demo/.env` has **no** `LEADS_API_` lines: `ssh ... "grep -c LEADS_API_ ~/droneops-demo/.env || true"` → `0`.

- [ ] **Step 9: Merge marketing PR first**, wait for the NOC deployer, then verify the API container picked up the env and code:

```bash
ssh -i ~/.ssh/deploy-bos bbarnard065@10.99.0.4 'docker exec barnardhq-api sh -c "test -n \"\$DOC_LEADS_TOKEN\" && echo token-set; grep -c registerDocLeadsRoutes /app/server.js 2>/dev/null || grep -rc registerDocLeadsRoutes /app/*.js | head -3"'
```

If the token is not set in the container, run `docker compose up -d api` in `~/marketing` on BOS (not `restart` — it does not reload `.env`).

- [ ] **Step 10: Merge the DOC PR**, wait for deploy, verify: running backend image contains the code, version and env, and migration head:

```bash
ssh -i ~/.ssh/deploy-bos bbarnard065@10.99.0.4 'docker exec droneops-backend-1 sh -c "python -c \"from app.version import APP_VERSION;print(APP_VERSION)\"; test -n \"\$LEADS_API_TOKEN\" && echo token-set; alembic current 2>/dev/null | tail -1; getent hosts host.docker.internal"'
```

Expected: `2.97.0`, `token-set`, `0013_lead_integration (head)`, a host IP. If env is missing: `docker compose up -d backend` in `~/droneops`.

- [ ] **Step 11: End-to-end from inside DOC's backend** (key-only output, no PII printed):

```bash
ssh -i ~/.ssh/deploy-bos bbarnard065@10.99.0.4 'docker exec droneops-backend-1 python -c "
import asyncio
from app.services.lead_source import get_client
async def main():
    leads = await get_client().list_leads(include_closed=True, limit=5)
    print(len(leads), [l[\"key\"] for l in leads])
asyncio.run(main())"'
```

Expected: a count ≥ 1 and keys like `web-N` / `cold-N`.

- [ ] **Step 12: Live UI verification on `https://droneops.barnardhq.com`** with Playwright, by eye, at 1440×900 and 375×812:
  1. New Mission → the picker lists open website leads; pick one → title/description/customer fill as specified; banner appears when the email matches.
  2. Create the mission → the Hub shows **View lead**, no warning after a few seconds (reload); the link opens `marketing.barnardhq.com/outbound/leads?lead=<key>` with that row highlighted and stage **won**.
  3. Customers → New Customer → picker prefills; matching banner offers "Edit that customer instead".
  4. `https://command-demo.barnardhq.com` → New Mission shows **no** picker.

  The test lead must be one Bill names — ask him which lead to use rather than marking an arbitrary real prospect `won`. If he prefers a synthetic one, submit the website contact form with a clearly-labelled test identity, then after the check set its stage back and delete the DOC test mission/customer.

- [ ] **Step 13: Close out.** Update ROADMAP LD-1 to `**DONE — live v2.97.0, verified <date>**` with the verification evidence in one line; CHANGELOG entry gets a "Verified live" line. Commit with `[skip-deploy]` in the subject, push, and `git status` both worktrees (clean). Remove the worktrees: `git -C ~/droneops worktree remove ~/wt-doc-leads` and `git -C ~/marketing worktree remove ~/wt-mkt-leads` after branches are merged.
