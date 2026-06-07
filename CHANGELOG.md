> **Maintained automatically by NOC doc-autogen.** This file is refreshed twice daily (04:00 + 16:00 UTC) by `~/noc-master/scripts/doc-autogen.py`, which summarizes recent commits via Claude Haiku 4.5 and commits with a `[skip-deploy]` trailer so no container rebuilds are triggered. See [NOC-Master ADR-0013](https://github.com/BigBill1418/NOC-Master-Control-SWARM/blob/main/docs/decisions/ADR-0013-docs-only-deploy-skip.md). Manual edits are preserved — the generator diffs against existing content before writing.

# Changelog

Notable changes to DroneOpsCommand. Dates are absolute (YYYY-MM-DD, UTC).

## 2026-06-07 — fix(auth): single-flight token refresh — stop the dashboard "cycling" on token expiry — v2.68.2

The operator dashboard visibly blanked → re-authenticated → repopulated in one "blink" roughly every 30 minutes (the reported "portal cycling"). Root cause: when the 30-minute access token expired, the dashboard's parallel polls (missions, customers, batteries, weather, maintenance ×2, flight-library) all 401'd in the same tick, and the axios response interceptor (`frontend/src/api/client.ts`) fired a **separate** `/api/auth/refresh` for *each* 401 — a 7-way refresh storm — before retrying each request.

- **Single-flight refresh.** Concurrent 401s now coalesce onto one shared in-flight refresh promise; the first caller starts it, the rest await it, then every original request retries with the new token. Eliminates the storm and the visible full-dashboard reload.
- **Latent logout-loop risk removed.** With the old per-request refresh, the moment the server starts rotating/invalidating refresh tokens (single-use), the concurrent refreshes would race a now-stale token → 401 → forced `window.location.href = '/login'` on a loop. Coalescing to one refresh closes that off before it can bite.
- **No longer swallows refresh failures.** Refresh errors are now `console.warn`'d before the `/login` redirect (per the v2.38.x lesson in CLAUDE.md — the silent-swallow interceptor that made a login-lockout bug hard to diagnose).
- **Test:** `frontend/src/api/__tests__/client.refresh.test.ts` — proves 5 simultaneous 401s trigger exactly one refresh and that all five requests retry to 200, plus that a later expiry starts a fresh refresh (single-flight is per-event, not a permanent latch).
- Version markers reconciled to 2.68.2 across README / package.json / main.py / AppShell (README + AppShell had drifted to 2.68.0).

_Backend `/api/auth/refresh` is unchanged (stateless, non-rotating) — this is a pure frontend fix._

## 2026-06-07 — docs(incidents): log 2026-06-05 CF health flap as external (BOS firewall), not a DroneOps defect

The droneops-api-health CF healthcheck flapped ~10 minutes on 2026-06-05 08:52–09:02 PDT due to BOS-HQ host-firewall maintenance severing the cloudflared→backend origin (CF origin=530), **NOT a DroneOps backend defect**. No code change; the incident has been logged to prevent future sessions from chasing a phantom backend bug. Full RCA and durable fix documented in infrastructure-hardening ADR-0001.

## 2026-06-06 — chore(compose): memory limits + health-check tuning (BOS-HQ best-practices sweep)

Memory resource management hardened across the shared BOS-HQ host to cap OOM blast radius. All limits sized from steady-state RSS observations (2.5× or category floor, err high):

- `db` 1G, `redis` 256M, `ollama` 10G (above the ~6 GB loaded-model footprint; sits over the 8G reservation), `backend` 768M–1G, `worker` 1.5G, `beat` 512M, `flight-parser` 256M, `frontend` 256M, `cloudflared` 256M.
- Health-check intervals: `redis` and `flight-parser` widened 30s → 45s (non-critical paths); `backend`/`worker`/`frontend` held at 30s (customer-facing / probed edge); `ollama` kept at 30s (inference health).
- Applied to both `docker-compose.yml` (primary instance + shared-base) and `docker-compose.demo.yml` (demo inheritance).
- BOS-local `docker-compose.override.yml` (untracked) neutralizes `db` to sleeping alpine; the real primary `droneops-standby-db` is capped separately in that override.

**Watchtower service removed (ADR-0088).** Image updates on deployer-managed hosts flow exclusively through the swarmpilot deployer pipeline; in-stack auto-update is redundant and a supply-chain risk. The BOS-HQ container (`droneops-watchtower-1`, already `Exited(2)`) was removed during the sweep; this commit ensures it cannot return on a future manual `docker compose up`. Mirrors the earlier `infrawatch-watchtower` removal.

## 2026-06-05 — chore: add per-service mem_limit + widen non-critical healthchecks (BOS-HQ sweep)

Caps OOM blast radius on the shared BOS host (no service was previously
memory-bounded). Limits sized from steady RSS (2.5x or category floor, err
high): `db` 1G, `redis` 256M, `ollama` 10G (above the ~6 GB loaded-model
footprint; sits over the 8G reservation), `backend` 1G, `worker` 1.5G,
`beat` 512M, `flight-parser` 256M, `frontend` 256M, `cloudflared` 256M.

Healthcheck intervals widened 30s → 45s on `redis` and `flight-parser`;
`backend`/`worker`/`frontend` kept at 30s (customer-facing / probed :3080
edge), `ollama` kept at 30s (inference health). This base file is shared by
the `droneops-demo` checkout, which inherits the same limits/intervals.

On BOS the gitignored `docker-compose.override.yml` neutralizes `db` to a
sleeping alpine; the BOS-promoted primary `droneops-standby-db` is capped to
1G directly in that override (not tracked here).

## 2026-06-05 — chore: remove Watchtower service (ADR-0088, no auto-updater on deployer-managed hosts)

Removed the `watchtower` service from `docker-compose.yml`. Image updates on
this fleet flow exclusively through the swarmpilot deployer pipeline; an
in-stack auto-updater is redundant and a supply-chain risk on a
deployer-managed host (NOC-Master ADR-0088). The BOS-HQ container
(`droneops-watchtower-1`, already `Exited(2)`) was removed during the
2026-06-05 best-practices sweep; this commit ensures it cannot return on a
future manual `docker compose up`. Mirrors the earlier `infrawatch-watchtower`
removal.

## 2026-06-05 — chore: widen sub-30s healthcheck intervals to 30s (BOS-HQ exec-storm cleanup)

Follow-up to the 2026-06-04 BOS-HQ high-load work. BOS dockerd/containerd were
burning ~2.3 cores on a healthcheck-exec storm (~108 `container/exec_die` per
30s fleet-wide). Widened every sub-30s healthcheck interval in this stack to
Docker's 30s default — roughly halves the per-container exec cadence with only
tens-of-seconds slower failure detection (acceptable for DB/redis/app checks).

- `docker-compose.yml`: `db` 5s→30s, `redis` 5s→30s, `ollama` 15s→30s,
  `backend` 15s→30s, `flight-parser` 15s→30s, `frontend` 15s→30s
  (`worker` already 30s, unchanged). This base file is shared by the
  `droneops-demo` checkout, so the demo stack inherits the same intervals.
- The BOS-local `docker-compose.override.yml` (untracked; neutralizes the `db`
  service to a sleeping alpine on BOS while replication owns the real primary)
  carried a 2s interval on the trivial `["CMD","true"]` check — widened to 30s
  in place on the host. Not committed here because the override is BOS-local.

No other compose settings changed.

## 2026-06-02 — chore: retire orphaned per-repo autopull; deploy path is the NOC fleet deployer (ADR-0018)

Removed the dead per-repo autopull scaffolding that survived commit `e4610b5`
(which deleted the `update.sh` they depended on). `autopull.sh` still called the
non-existent `update.sh`; `droneops-autopull.timer` / `.service` were inactive on
every host but present on disk; `setup-server.sh` still installed/enabled the
timer and `chmod`'d the deleted `update.sh`. These corpses advertised a second,
non-functional deploy path and misled the 2026-06-02 stale-deploy investigation.

- **Deleted:** `autopull.sh`, `droneops-autopull.timer`, `droneops-autopull.service`,
  the stale tracked `autopull.log`, and the now-dead `.gitignore` entries.
- **Reconciled** `setup-server.sh`: installs only `droneops.service` (boot-time
  `docker compose up -d` — NOT a deploy path); dropped all autopull install/enable
  logic and the `update.sh` chmod; added a banner pointing to the fleet deployer.
- **Deploy path of record:** the NOC Master Control fleet deployer
  (`swarmpilot_deployer` on HSH-HQ) — polls `main`, rebuilds + recreates on
  BOS-HQ. History: https://noc-mastercontrol.barnardhq.com/deploys

The companion deployer-side fix (the image-digest gate now observes this repo's
`build:`-only services, which was the actual cause of the silent stale deploy) is
recorded in NOC-Master-Control ADR-0079. See ADR-0018.

## [2.68.1] — 2026-06-02 — fix(flights): flight date stamped in operator timezone, not UTC (ADR-0017)

An evening flight flown **2026-06-01 20:27 PDT** displayed (and was named) as
**2026-06-02**. Root cause confirmed against the live production row: the
instant was captured correctly (`start_time 2026-06-02 03:27 UTC` *is*
`2026-06-01 20:27 PDT`), but the UTC instant was reduced to a calendar date in
UTC in two places — the serialized timestamp carried no `Z` (so the frontend
misread naive-UTC as browser-local), and `_generate_flight_name` ran
`strftime` directly on the UTC value.

- **New:** `backend/app/utils/timezone.py` — single source of truth for
  UTC↔operator-local conversion. Operator TZ is `settings.operator_timezone`
  (default `America/Los_Angeles`, env `OPERATOR_TIMEZONE`).
- **Wire format:** all flight datetimes now serialize **UTC-aware** (`+00:00`)
  via `iso_utc` — `FlightResponse` field serializer plus every manual
  `.isoformat()` site in `flight_library`, `missions`, `pilots`. A naive
  flight timestamp can no longer reach a client.
- **Display:** new `frontend/src/lib/datetime.ts` formats every flight
  date/time pinned to the operator timezone — **viewer-timezone-independent**
  — and defensively coerces offset-less timestamps to UTC. Wired into
  `Flights`, `Dashboard`, `FlightReplay`, `FlightVideoExporter`.
- **Name generation:** `_generate_flight_name` derives `YYYYMMDD` via
  `local_date_compact` (operator-local).
- **Backfill:** `scripts/backfill_flight_local_dates.py` (dry-run by default,
  idempotent, collision-safe) rewrites the date token in existing
  auto-generated names; e.g. `..._20260602_0001` → `..._20260601_0001`.
- **Tests:** `backend/tests/test_flight_timezone.py` covers the evening-Pacific
  boundary, the UTC-aware serialization, and the midday no-regression case.
- Displayed dates self-correct retroactively (derived live from the correct
  UTC instant); no data migration needed for display. Client-facing PDFs were
  unaffected — they use the operator-entered `mission_date`, not `start_time`.

## [2.68.0] — 2026-05-25 — feat(missions): lead-source attribution (ADR-0016)

Answers the operator's question "how much of my job revenue came from the website."
Nothing previously recorded where a job originated.

- **Schema:** two additive nullable columns on `missions` — `source VARCHAR(50)`
  (allowed: `website`/`referral`/`repeat_client`/`phone`/`social`/`other`, NULL =
  unknown) and `source_ref VARCHAR(255)` (optional external lead id). Applied via the
  repo's idempotent `_add_missing_columns()` startup hook (the repo has no Alembic
  env), matching the ADR-0009 additive-nullable pattern. Failover-safe: standby
  promotion runs the same idempotent ALTER; no PK/FK/index/enum-type change.
- **API:** `source` + `source_ref` added to `MissionCreate` / `MissionUpdate` /
  `MissionResponse` (validated against the `MissionSource` enum → 422 on bad values,
  stored as the plain value not a PG enum). `GET /api/financials/summary` gains a
  `revenue_by_source` block — collected (`paid`) + billed (`total`) revenue and
  mission count per source, sorted by collected desc, NULL → `unknown`. Each summary
  `missions` row now carries `source`.
- **Frontend:** "Lead Source" dropdown on the mission create modal and details
  editor; "Collected Revenue by Lead Source" panel on Financials.
- **Backfill:** "Bella / Banks Missing Dog" (mission `11083323…e46f`, invoice
  `BARNARDHQ-2026-0002`, $1,216.36 paid) set to `source='website'` — it came in
  through the barnardhq.com contact form.
- **Tests:** new `test_mission_source_attribution.py` (9 tests) covering schema
  validate/serialize, enum coercion, and the `revenue_by_source` rollup. Full suite:
  290 passed (2 pre-existing Stripe-probe env failures unrelated).

## [unreleased] — 2026-05-25 — fix(pdf-preview): default report PDF zoom 120% → 60%

`PdfViewer` opened report previews at 120% (`scale=1.2`), too zoomed-in. Default
is now 60% (`scale=0.6`); the reset-zoom control matches. Within the existing
0.5–3.0 zoom range; frontend-only.

## [unreleased] — 2026-05-25 — fix(report-gen): Celery async-loop bug broke AI report generation

**Bug:** AI report generation was flaky/failing. The Celery tasks that run async
DB work (`generate_report`, `send_payment_reminders`) spun up a fresh event loop
per invocation but reused the **module-global `async_session`**, whose asyncpg
connection pool is bound to the *previous* task's now-closed loop. So every such
task after the worker's first raised `RuntimeError: got Future attached to a
different loop` / `Event loop is closed`. `generate_report` (bind=True) only
recovered on Celery's 15s retry and hard-failed after `max_retries=3`;
`send_payment_reminders` has no retry, so the daily dunning sweep silently failed
(the Banks reminder due 2026-05-26 would not have fired).

**Fix:** new `app/tasks/async_db.py` (`new_task_loop_session()` /
`task_event_loop()`) gives each task its own fresh loop + a task-local **NullPool**
async engine (no cross-loop connection reuse), disposed on exit. Both affected
tasks now use it. `send_report_email` was already safe (async SMTP, no DB session)
and is unchanged. 3 regression tests pin the fresh-loop / NullPool / not-global-engine
invariants. Full suite: 279 passed (2 pre-existing Stripe-probe env failures unrelated).

**Also — per-instance model selection:** `claude_model` is now read from DB system
settings (mirroring `anthropic_api_key`), falling back to config. This instance is
set to **`claude-opus-4-7`** (top-tier) for client-facing reports; managed customer
instances keep the config default (`claude-sonnet-4-6`) unless their own DB overrides.
Opus 4.x deprecated the `temperature` parameter (the API 400s if sent), so the
Claude call now gates `temperature=0.3` behind `_supports_temperature(model)` —
omitted for Opus 4.x, kept for Sonnet/Haiku. (This surfaced only after the loop fix
stopped masking it.)

**Failover / blue-green / replication impact:** none — worker-side task plumbing +
a read-only settings lookup; no schema change, no migration.

## [unreleased] — 2026-05-24 — fix(business-signals): tz-aware vs naive datetime zeroed every windowed metric

**Bug:** `GET /api/v1/business-signals` computed its 30/90-day window bounds with
`datetime.now(timezone.utc)` (tz-**aware**), then compared them against
`paid_at` / `updated_at` / `created_at` / `mission_date` columns, which are
stored tz-**naive** (`datetime.utcnow()`). asyncpg rejects that comparison
(`DataError: can't subtract offset-naive and offset-aware datetimes`), so every
windowed query raised and was silently swallowed by `_safe_scalar` → the
endpoint always returned `0` / `null` for `invoice_paid_usd`, `missions_*`,
`new_customers`, etc. Project J.A.R.V.I.S. (the consumer) has been getting
zeroed innovation signals, and the value was unusable for the marketing
revenue bridge (which routed around it via `financials/summary`).

**Fix:** new `_utc_windows()` helper returns the `(now, now-30d, now-90d)` triple
as tz-**naive** UTC (matching the columns), coercing any tz-aware input to naive
UTC defensively. The endpoint uses it. `generated_at` now carries an explicit
`Z` suffix. Verified live on BOS-HQ: `invoice_paid_usd` (30d) `0 → 1216.36`,
`missions_completed` `null → 1`.

**Failover / blue-green / replication impact:** none. Read-only query fix; no
schema change, no writes, no migration. Both blue and green versions read the
same data correctly post-fix; safe to deploy under the standby-first flow.

**Tests:** `tests/test_business_signals_windows.py` pins the naive-UTC invariant
(3 tests). Full suite: 276 passed (2 pre-existing Stripe-probe env failures
unrelated to this change).

## [unreleased] — 2026-05-24 — fix(nginx): re-resolve backend DNS per request so backend rebuilds don't 502 the API

**Incident (resolved):** after rebuilding + recreating the `backend` container, the
`frontend` nginx kept proxying `/api/` to the backend's **old** container IP and
returned **502 for the entire API** (`connect() failed (111: Connection refused)
... upstream: http://172.19.0.11:8000`, while the live backend was at `172.19.0.9`).
The customer portal surfaced this as "link expired or invalid" because the SPA treats
a failed `/api/client/auth/validate` call as an invalid token. Immediate recovery was
an `nginx -s reload`.

**Root cause:** `nginx.conf` used `proxy_pass http://backend:8000;` with no `resolver`
directive, so nginx resolved the `backend` name **once at startup** and pinned the IP.
Any backend container recreate (every image rebuild / real deploy) changes that IP and
strands nginx on the dead address until it is reloaded.

### Fixed

- **`frontend/nginx.conf`** — added `resolver 127.0.0.11 valid=10s ipv6=off;` (Docker
  embedded DNS) and `set $backend_upstream backend;` at server scope, and switched all
  six `proxy_pass` directives to `http://$backend_upstream:8000`. Using a variable in
  `proxy_pass` forces nginx to re-resolve the name per request (cached 10s), so it picks
  up a recreated backend's new IP within 10s instead of 502-ing until a manual reload.
  Requires a frontend image rebuild to take effect.

## [unreleased] — 2026-05-24 — fix(client-portal): correct broken `/client/missions/` redirect route + sign-off

**Two latent bugs in the customer-facing portal URLs**, surfaced while verifying the
dunning pay-link. The frontend page route is `/client/mission/:missionId` (singular)
and the magic-link route is `/client/:token` — but two backend code paths emitted a
**plural** `/client/missions/{id}` URL that matches no route, so the browser fell
through to the operator login screen.

### Fixed

- **`backend/app/routers/client_portal.py`** — `_client_redirect_urls` (the Stripe
  Checkout `success_url` / `cancel_url`) used the plural `/client/missions/{id}` route
  → a customer returning from a successful payment landed on the operator login instead
  of their mission page. Also the cancel URL sent `?payment=cancelled` while the
  frontend only handles `?payment=cancel`. Both corrected to
  `/client/mission/{id}?payment=success|cancel`.
- **`backend/app/services/dunning.py`** — `_build_pay_url`'s no-token fallback used the
  same broken plural route. (The happy path already returns the correct
  `/client/{jwt}` magic-link; only the edge-case fallback was wrong.) Now
  `/client/mission/{id}`.

### Changed

- **dunning email templates** — sign-off changed from the templated
  `{{ company_name }} {{ company_tagline }}` to a fixed **"Bill Barnard — BarnardHQ"**
  in both `payment_reminder_email.html` and `payment_final_notice_email.html`.

## [unreleased] — 2026-05-24 — style(dunning): theme the reminder/final-notice emails to match the project

The dunning emails shipped as bare 14-16 line layouts (logo + name only, hard-coded
teal/red, no table structure) — off-brand next to the rest of the project's emails.
Rebuilt both `payment_reminder_email.html` and `payment_final_notice_email.html` on
the same template as `payment_received_email.html`: dark `#0e1117/#161b22` theme, the
shared `_brand_header.html` / `_brand_footer.html` partials, `brand_accent_color`
(cyan) for the reminder and red for the final notice, a mono amount-due card, the
gold pay CTA, and a past-due badge on the final notice. Render-verified with the
branding dict the send functions already use.
