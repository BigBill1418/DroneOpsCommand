> **Maintained automatically by NOC doc-autogen.** This file is refreshed twice daily by `~/noc-master/scripts/doc-autogen.py` (HSH-HQ user timer `doc-autogen.timer`, `OnCalendar` 04:00 + 16:00 — **local time, i.e. America/Los_Angeles, since the 2026-08-25 fleet timezone change**; the unit's own comment still says UTC and is stale). It summarizes recent commits via Claude Haiku 4.5 and commits with a `[skip-deploy]` trailer so no container rebuilds are triggered. See [NOC-Master DEC-0013](https://github.com/BigBill1418/NOC-Master-Control-SWARM/blob/main/docs/decisions/DEC-0013-docs-only-deploy-skip.md). Manual edits are preserved — the generator diffs against existing content before writing.

# Changelog

Notable changes to DroneOpsCommand. Dates are absolute (YYYY-MM-DD, UTC).

## 2026-09-22 — The two things that must exist before the password can be retired (ADR-0048) — v2.95.0

Both mechanisms ship **inert**. Nothing changes for a self-hosted/OSS install, for the public
demo instance, or for BarnardHQ production as currently configured. `LOCAL_LOGIN_DISABLED` is
untouched and remains an operator flip on the host.

### Added

- **`SERVICE_ACCOUNT_USERNAMES`** (`backend/app/config.py`, wired through `docker-compose.yml`,
  documented in `.env.example`) — a comma-separated, **empty-by-default** allow-list of
  usernames permitted to keep using `POST /api/auth/login` and `POST /api/auth/refresh` while
  `LOCAL_LOGIN_DISABLED=true`. It exists because two repos authenticate to this API over the
  WireGuard mesh with a username and password and cannot hold a Cloudflare Access cookie:
  `droneopsmap-bridge` (`~/DroneOpsMap/.../doc_client.py`) and `marketing-bridge`
  (`~/marketing/api/droneops-financials.js`). ADR-0047 Amendment 2 showed the flag was true in
  production for ~6 h on 2026-09-21 and silently killed the second one; this closes that
  ADR's **precondition 5**.
  The exemption is from the "local login is retired" 403 and **nothing else** — wrong password
  is still 401, the per-IP lockout still fires, `is_active` is still enforced, and
  `POST /api/auth/setup` + `PUT /api/auth/account` stay hard-blocked for every name on the list.
  Blank or malformed values parse to an empty set, which exempts nobody.
- **`POST /api/auth/sso-exchange`** — trades a cryptographically verified
  `Cf-Access-Jwt-Assertion` for the same `{access_token, refresh_token, token_type}` pair
  `/api/auth/login` returns. ADR-0047 Amendment 1 established that `/api/intake/*` and
  `/api/tos/*` sit behind Access apps with **bypass** policies, so the edge injects no assertion
  and the 8 operator-only endpoints there accept only a local bearer — which, once Step B is on,
  nothing could mint. This is that mint, and it is deliberately **not** gated by the Step B
  switch. It verifies the RS256 signature against Cloudflare's JWKS with an explicit algorithm
  allow-list and required `aud`/`iss`/`exp`, honours `CF_ACCESS_ALLOWED_EMAILS`, and resolves
  identity through the existing `resolve_cf_access_user()` mapping table — no second identity
  mechanism, no privilege grant (`users` has no role column). It answers **404** when
  `CF_ACCESS_TEAM_DOMAIN`/`CF_ACCESS_AUD` are empty, so it is invisible on any install without
  Cloudflare Access.

### Changed

- `_require_local_login_enabled()` takes an optional `exempt_username`. Omitting it is the
  fail-closed default and is what `setup` and `update_account` deliberately do.
- **One behavioural change, on a path no real caller takes:** the Step B guard moved below the
  token decode in `POST /api/auth/refresh`, because it now needs a subject only the decode can
  establish (taken from the signed `sub`, never a client field). So with Step B on, an
  *undecodable* refresh token answers **401** instead of 403. The token failed signature
  verification, so nothing about any user is revealed. Pinned by a test.

### Tests

- **952 passed, 23 skipped** (`cd backend && pytest -q`), against a **907 passed, 23 skipped**
  baseline measured on the same checkout before the change. 45 new tests:
  `backend/tests/test_local_login_disabled.py` (+26) and `backend/tests/test_sso_exchange.py`
  (19, new). Every security claim was falsified by mutating the implementation and confirming
  the specific test goes red — including making `sso-exchange` trust the header unverified,
  which turns 9 tests red. The Access tests do not mock the verifier: a real RSA keypair signs
  every assertion and only the JWKS transport is stubbed.

### Documentation

- `docs/adr/0048-service-account-allowlist-and-sso-bearer-exchange.md` — full rationale, the
  mutation-testing table, the operator enable procedure, and the two method traps found on the
  way (the `@limiter.limit` decorator binds the module-level `Limiter` at import time, so
  per-app limiters do not isolate the budget; and two layers answer 429 on `/api/auth/login`).
- `docs/adr/README.md` — index reconciled: it still claimed `0001`–`0046` with `0047` next, and
  had no row for ADR-0047. Now `0001`–`0048`, next `0049`, both rows present.

### Operator action required before this does anything

`SERVICE_ACCOUNT_USERNAMES=marketing-bridge,droneopsmap-bridge` on the BOS-HQ `.env`, then
recreate the backend and verify **with Step B still off** that both bridges keep working. See
ADR-0048 §"To enable".

## 2026-09-22 — Operator Cloudflare Access SSO (ADR-0047 Step A) ROLLED BACK — onboarding outage

### Fixed

- **`POST /api/intake/initiate` returned `401` for the operator for ~9.5 h**
  (2026-09-21 ~16:57 PDT → 2026-09-22 02:31 PDT), blocking the "Initiate Services" /
  "GENERATE INTAKE LINK" flow that starts every customer onboarding. Each failed click also
  bounced the browser to `/login`, which is why the password field reappeared — one defect,
  two symptoms. Resolved by blanking `CF_ACCESS_TEAM_DOMAIN` and `CF_ACCESS_AUD` in
  `/home/bbarnard065/droneops/.env` on BOS-HQ and recreating `droneops-backend-1`
  (`.env.bak-adr0047-rollback-20260922-*` holds the pre-rollback file). No code change, no
  data loss, no customer-facing impact.

### Changed

- ADR-0047 Step A (Cloudflare Access JWT verification) is **dark again**.
  `GET /api/auth/setup-status` reports `sso_configured:false`; the app is back to
  bcrypt password + HS256 bearer as the only operator credential. The v2.94.0 code remains
  merged on `main` and is inert while both env vars are empty. Step B
  (`LOCAL_LOGIN_DISABLED`) read `false` throughout and never took effect.

### Root cause

Two defects that only bite together: (1) `frontend/src/hooks/useAuth.ts` short-circuits
`tryLocalToken()` when its SSO probe succeeds, so with Access armed the SPA never obtains a
bearer token and holds exactly one credential — the `Cf-Access-Jwt-Assertion` header
Cloudflare injects; (2) `/api/intake/*` is a deliberately customer-reachable prefix, so that
header is absent there while present on `/api/customers`, `/api/missions` and
`/api/auth/account`. Dashboard GETs therefore returned `200` and looked healthy while the one
operator POST under that prefix carried no credential at all. Full timeline, the evidence for
each claim, the hypotheses excluded, and the four preconditions for re-enabling are in
`docs/adr/0047-operator-cloudflare-access-sso.md` §"2026-09-22 incident".

### Documentation

- `CHANGELOG.md`, `PROGRESS.md` and ADR-0047 all described this work as "NOT DEPLOYED" while
  it was live in production from 2026-09-21 16:40 PDT. All three are corrected here. The
  deploy was authorized by the operator; the **soak** the ADR required and the **doc update**
  were what got skipped.

## 2026-09-21 — Repo-wide documentation freshness pass [skip-deploy]

Every tracked Markdown file (90), plus `.env.example`, compose comments and
script headers, read in full and verified against git history and the running
BOS-HQ stack. Two ledgers record one row per file with the evidence used:
`docs/reports/2026-09-21-docs-freshness-ledger-A.md` (core docs, ops files)
and `…-ledger-B.md` (ADRs, plans, reports, incidents). New: `docs/adr/README.md`
(index of ADR-0001…0046 with current status, contiguous, no gaps) and
`docs/plans/data/README.md`. Material corrections include: `.env.example`
named an Ollama model nothing pulls and was missing 13 env vars the code reads
(now programmatically symmetric with the code); the Cloudflare tunnel guide
pointed at `frontend:80` (nginx listens on 8080); README still described
Watchtower (removed 2026-06-05), a Python flight-parser (it is Rust), the
`/missions/new` wizard (a redirect since v2.67.0) and ~30 missing API routes;
ADR-0023 read "Proposed — no code shipped" three months after both legs went
live; ADR-0028 §H1 had no pointer to its supersession by ADR-0029; the basemap
probe's first scheduled run is **Mon 2026-09-28 15:47 UTC**, not 09-22 (a
Tuesday). ADR-0013's 4xx-burst customer-endpoint alert is recorded as **never
built** (genuine gap, now in the open-items inventory §3.7). Deliberately left:
three code comments with drifted line-number cites (`backend/app/main.py`
`_create_hot_indexes` docstring, `backend/tests/test_db_migrations.py`
"ADR-0035" → 0036) — comment-only edits would trigger a production rebuild;
fold them into the next code change.

## 2026-09-21 — v2.94.0 — Operator Cloudflare Access SSO, Step B kill switch (ADR-0047)

**CORRECTION (2026-09-22): this entry originally read "NOT DEPLOYED". It was wrong.**
v2.94.0 was deployed to BOS-HQ on 2026-09-21 at 16:40 PDT and Cloudflare Access verification
was activated at 16:55 PDT. It broke customer onboarding and was rolled back on 2026-09-22 —
see the 2026-09-22 entry below. `LOCAL_LOGIN_DISABLED` is accurate as written: OFF by default
everywhere, including `docker-compose.bos-prod.yml`, and it never took effect.

### Added

- `LOCAL_LOGIN_DISABLED=true` now actually gates `POST /api/auth/login`,
  `POST /api/auth/setup`, `PUT /api/auth/account`, and `POST /api/auth/refresh` — all four
  return `403` and mint no tokens. `GET /api/auth/setup-status` reports `needs_setup: false`
  unconditionally when set (same shape as the existing `managed_instance` short-circuit).
- **Deliberately NOT gated:** `GET /api/auth/account` (read-only identity info, and the exact
  endpoint the frontend's silent SSO probe calls — gating it would lock an operator out even
  while genuinely authenticated via a working Access session) and `get_current_user`'s
  bearer-token verification logic itself (only the routes that *mint* new local credentials
  are gated, so re-enabling local login is a single env-var flip, not a code revert).
- Self-hosted/OSS installs and the public demo instance are unaffected: the flag defaults to
  `false` and neither deployment topology has any reason to ever set it.

### Verification

Full backend suite: 907 passed, 23 skipped. 9 new tests in
`backend/tests/test_local_login_disabled.py` cover both states: the default (false) is proven
a byte-identical no-op for all four routes, and the gated (true) state is proven to 403 all
four while leaving `GET /account` reachable. `test_app_version_parity.py` green at 2.94.0.

Full cutover runbook (enable Step A, soak, then enable this) and per-step rollback:
`docs/adr/0047-operator-cloudflare-access-sso.md`.

## 2026-09-21 — v2.93.1 — Login screen modernized for SSO (ADR-0047 Part 2)

**NOT DEPLOYED — `feat/operator-sso` worktree branch only.**

### Changed

- **Email fixed** on the login and setup screens: `me@barnardHQ.com` -> `Bill@BarnardHQ.com`
  (exact capitalization, both the `mailto:` href and the visible text).
- **Login footer now matches CallSignLane's pattern** — an anchor to `https://www.barnardhq.com`
  wrapping "A software solution by:" plus the real BarnardHQ wordmark
  (`frontend/public/barnardhq-logo.svg`, copied byte-for-byte from CallSignPublic).
- **Silent SSO.** `useAuth` now reads `sso_configured` off `GET /api/auth/setup-status`
  (extended to carry it, alongside `local_login_disabled`, in this commit) and, when true,
  probes `GET /api/auth/account` with no bearer token via a bare `axios` call before ever
  showing the password form — Cloudflare Access injects its JWT header automatically on every
  proxied request, so a real operator browsing to `droneops.barnardhq.com` never types a
  password. Fully inert (zero extra network round trip) when `sso_configured` is false — the
  self-hosted/OSS and public-demo default.
- Login screen now renders an "Access" card above the password form when SSO is configured
  (Step A additive state) and hides the password form entirely once an operator also sets
  `local_login_disabled` (Step B).

### Verification

Full frontend suite: 91 passed (14 new: `useAuth` 7, `Login` 6, `Setup` 1 — up from the 77
baseline). `npx tsc --noEmit` clean. Backend: 898 passed, 23 skipped (`setup-status` SSO-flag
coverage; local-login gating itself is a separate commit).

## 2026-09-21 — v2.93.0 — Operator Cloudflare Access SSO, Step A (ADR-0047)

**NOT DEPLOYED — lands on `feat/operator-sso` in a dedicated worktree, per operator
instruction. No push, no merge, no Cloudflare API calls.**

### Added

- **`backend/app/auth/cf_access.py`** — RS256/JWKS verification of Cloudflare Access's
  `Cf-Access-Jwt-Assertion` header, ported from the marketing pilot
  (`~/marketing/api/cf-access.js`, ADR-0099). Fail-closed on every path (unreachable JWKS
  denies, never falls back to a stale keyset past its TTL); 33 tests using a real generated
  RSA keypair.
- **`cf_access_identities` table** (migration `0012_cf_access_ident`) — Cloudflare Access
  email -> local `users.id` mapping, populated only by `resolve_cf_access_user()`. Fixes,
  pre-emptively, the exact "silently adopts a pre-existing local account by username match"
  defect a security review found in the marketing pilot's first draft — and this app's
  `PUT /api/auth/account` has no username charset restriction at all, making that collision
  surface live here rather than theoretical. 6 tests against a real disposable Postgres
  container prove the non-adoption invariant directly.
- **`get_current_user`** (the single dependency all 25 operator routers use) now accepts a
  verified Access identity as an additional credential alongside the existing session-token
  path — additive, same shape as the marketing pilot's `authMiddleware`. Structurally dark
  until an operator sets both `CF_ACCESS_TEAM_DOMAIN` and `CF_ACCESS_AUD` — zero behavior
  change for self-hosted/OSS installs and the public demo instance by construction.
- `LOCAL_LOGIN_DISABLED` kill switch (`backend/app/config.py`) — implemented but **off by
  default everywhere**, gating `login`/`setup`/`account`/`refresh` when an operator
  explicitly enables it after the Step A soak. Full cutover + rollback runbook in
  `docs/adr/0047-operator-cloudflare-access-sso.md`.

### Verification

Full backend suite: 895 passed, 23 skipped (up from the 855/17 baseline verified at session
start; the 8 new skips are the real-Postgres identity-resolution tier, opt-in via
`DOC_TEST_PG_URL`). `test_app_version_parity.py` green at 2.93.0.

## 2026-09-21 — v2.92.1 — Sentry release tags read the source-of-truth version

### Fixed

- **Sentry/GlitchTip release drift (ROADMAP H-1).** Both halves tagged errors
  with the `APP_VERSION` env var: the compose defaults sat at `2.67.3` and the
  BOS-HQ host `.env` pinned `2.67.4`, so production errors have been grouped
  under a ~25-minor-versions-stale release since ~v2.67. `backend/app/
  observability/sentry.py` now uses `app.version.APP_VERSION`;
  `frontend/src/lib/sentry.ts` now uses the vite-defined `__APP_VERSION__`
  (from `package.json`). Both are covered by `test_app_version_parity.py`, so
  the tag can no longer lag a bump. `VITE_APP_VERSION` is no longer read.
- The stale `APP_VERSION=2.67.4` line was removed from BOS-HQ `~/droneops/.env`
  (a backup copy was left beside it). Compose defaults bumped to 2.92.1 for
  tidiness; CLAUDE.md now says they are not load-bearing.

**Verification:** `GET /openapi.json` → 2.92.1 after deploy; backend log line
`sentry.initialized release=droneops@2.92.1` (only when a DSN is set — prod
runs with GlitchTip DSN; check `docker logs droneops-backend-1 | grep sentry`).

## 2026-09-21 — Stale-docs sweep + demo stack to v2.92.x + backup-cutover gate fix [skip-deploy]

Documentation and ops reconciliation after three things shipped the same day.
**No application code changed in this commit**, so it carried no version bump:
the app was 2.92.0 and the parser 1.2.0 at the time.
*(Status 2026-09-21: v2.92.1 shipped later the same day — see the entry above.
Live is **2.92.1**; the parser is unchanged at **1.2.0**.)*

### Ops

- **Demo stack `~/droneops-demo` on BOS-HQ updated by hand, 14:57 PDT —
  v2.80.4 → v2.92.0 (14:57 PDT), then → v2.92.1 (15:23 PDT).** It is **not** deployer-managed (the NOC deployer targets
  prod only), so it was `git pull --ff-only` to `d153623` then
  `compose up -d --build --no-deps frontend backend flight-parser`.
  `cloudflared`, `db` and `redis` were deliberately left alone (4-week uptime
  intact) and the demo **worker + beat stay stopped on purpose** — a running
  demo beat is the dunning-email hazard recorded in ADR-0042. Verified: all
  three rebuilt containers healthy, served bundle carries the Esri endpoints
  and **zero `cartocdn`**; the demo backend read 2.92.0 at 14:57 PDT and
  **2.92.1** after the 15:23 PDT second update. The CHAD-HQ demo is a
  different clone, still on `dfad0a3`, and remains open.
- **Backup-cutover gate rewritten** in `scripts/droneops-backup-cutover.sh`.
  The 2026-08-28 automatic attempt aborted with `only 5/6 completed runs in
  last 3 days` — **the backup lane was green twice daily throughout**; the gate
  counted `done.` lines out of **journald**, and journald on BOS-HQ retains
  under three days, so the oldest completion had rotated out before the gate
  read it. It now counts the lane's own output: `restic snapshots --tag db
  --json` filtered to the last 72 h, where `forget --keep-daily` makes **≥3
  snapshots in 72 h** exactly "three consecutive green days". The journald
  figure is still printed as context but can no longer abort a run, and the
  same `snapshots` call now serves the later gate too (one restic call instead
  of two). Recorded as ADR-0041 **Amendment 2**.
  *Rule it generalises to: a gate asserting "N events in the last T" must read
  a store whose retention exceeds T.*
- **Compose `APP_VERSION` defaults 2.67.3 → 2.92.0** — five locations
  (`docker-compose.yml` ×4, `docker-compose.demo.yml` ×1). Not the app's
  reported version, but at the time what tagged the **Sentry/GlitchTip
  release** on both halves; they had been stale for ~25 minor versions because
  nothing checks them. **The host `.env` on BOS-HQ overrode the default and was
  itself stale (`APP_VERSION=2.67.4`), so this did not by itself fix
  production** — both halves were ROADMAP `H-1`.
  *(Status 2026-09-21: `H-1` is **CLOSED**. v2.92.1 removed the dependency
  entirely — releases now come from `backend/app/version.py` and
  `__APP_VERSION__`. The stale `APP_VERSION=2.67.4` line was removed from the
  BOS-HQ `.env`; the host now reports `APP_VERSION=2.92.1` and nothing reads
  it. Compose defaults are 2.92.1 and cosmetic. The Login/Setup footers were
  never affected — they always rendered `__APP_VERSION__`.)*

### Docs

- **Phase 7 (ADR-0045) reconciled as merged + deployed** — `d30eb5b` 13:53 PDT,
  v2.91.0 live 13:58 PDT. `P7-6` (post-deploy client-IP check) stays open: the
  public URL sits behind Cloudflare Access, so only Bill's own browser session
  produces a log line with a real external IP.
- **Maps (ADR-0046) reconciled as deployed** — v2.92.0 live 14:52 PDT, probe
  `ok: true / layers_ok: 5`, probe ntfy off by design until MP-2.
- **ADR-0041 §5.7 cutover recorded as EXECUTED** (~14:55 PDT, `d153623`),
  including that the deleted plaintext R2 prefix had **already been
  copy-forwarded into immutable Backblaze B2** by the fleet's second-provider
  lane (noc-master ADR-0232) — so an `s3 rm` here no longer reaches every copy.
- **`.deployer-disabled` misconception corrected in 7 documents** (ADR-0033,
  ADR-0034, ADR-0035, ADR-0038, two 2026-07-03 plans, the 2026-05-14 incident).
  Dated correction notes appended; no history rewritten.
- **ADR-0043 + the FP-1 plan** un-staled: P0/P1 are live, the §8 log-inventory
  hunt was filled in 2026-09-04/05, and **P7 is no longer blocked on it**. Both
  now say to re-derive log counts from the database rather than read the prose.
- **ROADMAP** — closed items (`BK-2`, `FU-2`, `FU-7`, `FU-8`,
  `FU-AI-RUNTIME-GATE`, `FU-AI-2`) moved verbatim under a new **Completed**
  section so the open surface is only open work; FU-8's superseded six-item
  list deleted in favour of its own 2026-08-03 correction; the empty "Older
  roadmap items" placeholder removed; `FU-AI-RUNTIME-GATE` corrected from
  "no deploy yet" to live (re-verified against the running system); new items
  `BK-3` (Grafana rule description, now unblocked, lives in `~/noc-master`),
  `BK-4` (n8n sqlite disposal) and `H-1` (compose-default parity).
- **PROGRESS.md** — closed history older than 2026-08-01 moved verbatim to
  `docs/archive/PROGRESS-2026-H1.md`; the spent FP-1 reminder-cron paragraph
  deleted; two four-month-old "IN-FLIGHT" headings corrected against the
  running system; the 2026-04-24 "awaiting operator action" item closed against
  the telemetry it named (`M4TD.last_used_at` = 2026-09-21 20:03 UTC).
- **README** — the demo's "24-hour auto-reset" replaced with the real
  mechanism: `scripts/demo-nightly-reset.sh` from the BOS crontab at **02:23
  PT** (`23 2 * * *`; hosts run `America/Los_Angeles` since 2026-08-25).
- **CLAUDE.md** — version-bump list is now **6 files / 7 locations**
  (`backend/app/version.py` added by ADR-0046), plus the compose-default rule.
- **New:** `docs/reports/2026-09-21-open-items-inventory.md` — the authoritative
  open-items and operator to-do list as of today.

## 2026-09-21 — Keyless basemap registry + tile-health probe — v2.92.0 (ADR-0046)

**Every map's default "Dark" layer was serving a watermarked tile and had been
since ~2026-08-28.** CARTO began stamping "API KEY REQUIRED" into its keyless
raster basemaps. The tiles came back **HTTP 200**, `image/png`, `ACAO: *`,
256x256, plausible byte size — every header healthy, the image degraded. No
status check, `tileerror` handler, uptime monitor or tile cache could see it;
a cache would have made it worse by serving the watermark for its TTL. It went
unnoticed for 24 days. Research: `docs/reports/2026-09-21-basemap-provider-eval.md`.

### Changed

- **One basemap registry** — `frontend/src/lib/basemaps.ts` + `<BasemapLayers/>`.
  All five call sites (`FlightMap`, `Telemetry`, `FlightReplay`, `Airspace`,
  `FlightVideoExporter`) import one component; none holds a tile URL. A provider
  swap is now a one-line edit instead of a five-file hunt.
- **Keyless Esri ArcGIS raster + OSM.** Dark = `World_Dark_Gray_Base` +
  `World_Transportation` overlay (the overlay carries real data to z19, so roads
  and street names stay crisp exactly where the z16 base goes soft); Satellite =
  `World_Imagery`; Hybrid = imagery + place names + roads; Street = OSM.
  `maxNativeZoom` per layer is **measured**, not the LOD 23 the services
  advertise — past their real data every one returns a blank filler tile.
- **Attribution now renders on every map.** Four call sites passed bare
  `"Esri"`/`"OSM"` and `FlightMap` rendered none; both providers require real
  attribution. Strings are verbatim from each MapServer's `copyrightText`.
- **OSM `{s}` subdomains dropped** — OSMF specifies the bare
  `tile.openstreetmap.org` host and warns other subdomains may be withdrawn.
- **Video exporter reworked.** CARTO served `@2x` retina tiles; Esri serves
  none. Density now comes from fetching z+1 and drawing at half scale (clamped
  to each layer's native ceiling, capped at 180 tiles/layer), compositing the
  full Dark stack so exports keep the app's look.
- **Report renderer sends an identifying User-Agent.** `staticmap` 0.5.7
  defaults to `User-Agent: StaticMap` — a library default UA, which the OSMF
  Tile Usage Policy names as a thing you must not do. Verified on the wire.

### Added

- **Tile-health probe** (`probe_basemap_tiles`, Celery beat, Mondays 15:47 UTC).
  Fetches one fixed tile per provider, computes two 64-bit perceptual hashes
  with Pillow (no new dependency — `imagehash` would pull numpy + scipy), and
  compares against a checked-in baseline captured today. Validated against the
  real defect class: a watermark stamped into each live tile moves the hashes
  10-35 bits against a threshold of 8. Result persisted to `system_settings` and
  emitted as a structured log line.
- **Admin endpoints** — `GET /api/admin/basemap/tile-health`,
  `POST .../run` (60s cooldown), `PUT .../ntfy`.
- **ntfy publishing is wired but DEFAULT OFF** (`basemap_probe_ntfy_enabled`).
  The thresholds are starting points, not measurements; observe-only for two
  weeks first (ROADMAP MP-2, earliest 2026-10-05).
- `backend/app/version.py` — `APP_VERSION` + the outbound `USER_AGENT`. A
  **seventh version location**, guarded by `tests/test_app_version_parity.py`.

### Known risk, accepted

Esri's keyless `server.arcgisonline.com` endpoints are a **gray-zone
dependency** — Esri has pushed developers toward a keyed service since 2022 and
these endpoints are four years past their own stated migration deadline. This is
structurally the same bet as CARTO. Taken with eyes open because swap cost is
now one line, detection is ≤7 days, and both exits are documented: Stadia at
$20/month, or Protomaps PMTiles self-hosted on R2 (ROADMAP MP-1).

## 2026-09-21 — Correction: Phase 7's trusted-proxy fix only trusted one hop (ADR-0045)

**Same-day correction to the entry directly below.** `app/utils/client_ip.py`
(item 1 of the Phase 7 hardening pass) reproduced the exact defect it was
meant to close, live-verified on BOS-HQ. Real production chain is two proxy
hops (`cloudflared` → `frontend`/nginx → uvicorn), not one; the original
code only ever trusted one configured hostname, so walking
`X-Forwarded-For` rightmost-first stopped at `cloudflared`'s constant
container IP and returned that as "the client" for every internet request
— one shared bucket for every rate limiter and the login lockout, just
relocated one hop from where item 1 found it. Root-caused by
`noc-master/docs/plans/2026-09-21-marketing-droneops-interaction-map.md`
§2.1 (live `docker logs` + `docker network inspect` against BOS-HQ, not
`docker compose config` — the method the original fix was verified
against, which confirms syntax renders, not that the live topology
matches).

- `TRUSTED_PROXY_HOSTNAME` now accepts a **comma-separated list** of
  hostnames (was a single string), each independently DNS-resolved.
  Default for this repo's compose topology: `"frontend,cloudflared"` — no
  operator action needed, matches the live-verified two-hop chain.
- Also designed and documented (not code-fixable from this repo) for the
  **managed-tenant** topology, a third distinct trust boundary found during
  the same review: `droneops-managed/templates/Caddyfile.client` (BOS-HQ,
  outside this repo) routes `/api/*` from a per-tenant `caddy` sidecar
  straight to that tenant's backend, bypassing `frontend` entirely — see
  `docs/managed-hosting.md` for the required operator config
  (`TRUSTED_PROXY_HOSTNAME=caddy` + `FORWARDED_ALLOW_IPS=<gateway CIDR>`)
  and the explicit statement that an unconfigured managed tenant fails
  closed, not open.
- Full correction narrative, including why the original verification
  method missed this: `docs/adr/0045-phase7-customer-surface-hardening.md`
  → "Correction — 2026-09-21" section (appended, original ADR body
  unchanged).
- Tests: 11 new cases in `backend/tests/test_client_ip.py`
  (`TestTwoHopNginxCloudflaredChain`, `TestManagedCaddyDirectChain`),
  modeling both chains with a spoofed header from an untrusted peer and a
  client-injected leftmost hop through genuinely trusted proxies, for each
  topology. `cd backend && pytest -q` → **795 passed, 17 skipped** (was
  784 passed, 17 skipped before this correction).
- ~~Still shipped to worktree branch `security/phase7-customer-hardening`
  only — **not merged, not deployed.**~~ **MERGED AND DEPLOYED 2026-09-21:**
  Bill merged the branch at `d30eb5b` (13:53 PDT) and this correction
  (`a226c93`) landed in the same range; the fleet deployer built and recreated
  the stack, **v2.91.0 live on BOS-HQ at 13:58 PDT**.

## 2026-09-21 — Phase 7 customer-surface hardening — v2.91.0 (ADR-0045)

Fleet SSO-conversion fan-out Wave 2B (noc-master
`docs/plans/2026-09-21-sso-fanout-dispatch.md`). Customer logins stay
app-local permanently (ADR-0246 decision 5) — this closes four concrete
defects underneath, not an SSO migration. ~~**Shipped to worktree branch
`security/phase7-customer-hardening`, not merged/deployed** — operator-
gated, prepared for review.~~ **MERGED `d30eb5b` 13:53 PDT and DEPLOYED —
v2.91.0 live on BOS-HQ at 13:58 PDT, same day.**

- **Root cause underneath three of the four findings:** every rate limiter
  and the login lockout keyed on `request.client.host`, which is nginx's
  own container IP on every request (nginx always sits in front of
  uvicorn) — one shared global bucket fleet-wide. A stranger's failed
  logins could lock the operator out of their own instance. New
  `app/utils/client_ip.get_trusted_client_ip` trusts `X-Forwarded-For`
  only from a verified proxy (dynamic DNS resolution of `frontend`, no
  compose changes needed, + optional static `FORWARDED_ALLOW_IPS`) and
  takes the rightmost non-client-controlled hop.
  *(Status 2026-09-21: superseded hours later by `a226c93` — a single
  hostname reproduced the bug against the real two-hop chain.
  `TRUSTED_PROXY_HOSTNAME` is now a comma-separated list defaulting to
  `frontend,cloudflared`. See the correction entry above.)* Wired into `main.py`,
  `auth.py`, `tos.py`, `client_portal.py`, `intake.py`.
- **`POST /api/tos/accept` unauthenticated write closed.** `customer_id` is
  now resolved FROM a validated, unexpired `intake_token` (mirrors
  `intake.py`'s own pattern) rather than trusted from the caller —
  previously any caller who knew a customer UUID could write a signed TOS
  acceptance and overwrite that customer's name/email with attacker-
  supplied data.
- **Signed-TOS download link bounded + rate-limited.** Was permanently
  valid with no rate limit; now `TOS_SIGNED_DOWNLOAD_EXPIRE_DAYS` (default
  730) from `accepted_at`, `10/minute` limit added.
- **Revoked client-portal links now actually revoke.** `get_current_client`
  previously verified only the JWT signature + `exp`; `DELETE
  .../client-link/{token_id}` stamping `revoked_at` had zero effect on
  auth. `token_hash` was already populated at issuance for exactly this
  lookup — now read at auth time. Auditing this surfaced a real would-have-
  shipped regression: `client_login` (password-based repeat-customer
  login) minted a fresh aggregated JWT with no matching `ClientAccessToken`
  row, which would now 401 on first use — fixed in the same commit, with a
  dedicated round-trip regression test.
- **CS-Public's unauthenticated origin search (`cs-api.barnardhq.com`)
  patch prepared, not landed** — separate repo (`~/repos/CallSignPublic`,
  confirmed not `~/callsign`), delivered as a reviewed patch per dispatch
  instruction not to create a worktree there. Fail-closed design (mirrors
  that repo's `ingest.py`): merging without first provisioning
  `ORIGIN_SEARCH_SECRET` (Worker) + `search.worker_origin_token` (origin
  settings) 503s all archive search, including the legitimate path.

Tests: `test_client_ip.py`, `test_auth_lockout_per_client.py`,
`test_tos_accept_forgery_prevention.py`, `test_tos_signed_download_expiry.py`,
`test_client_portal_revocation.py`, `test_client_login_issues_valid_token.py`
(all new) + `test_tos_customer_sync.py` / `test_tos_accept_route_body.py`
(updated — their fixtures exercised exactly the insecure shape this closes).
Full suite: 784 passed, 17 skipped (quoted, not inferred from exit code).
Full reasoning, consequences, rollout and rollback: `docs/adr/0045-phase7-customer-surface-hardening.md`.

## 2026-09-12 — the backup lane had one provider; it now has two (noc-master ADR-0232) [skip-deploy]

Docs only. No script, unit, credential, retention setting or schedule in this repo changed, and
no version bump (nothing under application code was touched).

ADR-0041 closed seven gaps in a lane that lands in **one Cloudflare account**, where R2 has **no
object versioning** — so a delete or corrupted overwrite there replaces the only copy. noc-master
**ADR-0232** (2026-09-11/12) adds a second provider, and DroneOps configures none of it:

- **`fleetbackup-r2-mirror`** (BOS, 07:00 PT) — `rclone copy`, **never `sync`**, of every R2
  bucket, so `droneops-backups` (the same encrypted restic repository) and the legacy
  `obs-glitchtip-backups/droneops/` tree are copied into B2 `barnardhq-fleet-nightly`. Object
  Lock **compliance 90 d**, keep-all-versions, never pruned — so **snapshots this repo's
  `forget --prune` has already removed remain in B2.**
- **`fleetbackup-bos`** (09:15 PT) — BOS-HQ's whole root filesystem, so `~/droneops/`,
  **`~/.droneops-secrets/restic-droneops.env`** and the `droneops_app_data` volume have a copy
  behind a *different* restic password: a second independent route to the credential ADR-0041
  D1 names as the single unrecoverable secret.

New runbook **§13** and **ADR-0041 Amendment 1**. Both spend as much space on what this does
**not** change as on what it does, because the easy misreading is expensive:

- It does **not** make `droneops_standby_pgdata` a valid backup. The whole-root lane captures
  that volume, but only crash-consistently (WAL replay). **D3 stands** — the logical
  `pg_dump -Fc` is still the correct restore artifact.
- It does **not** change RPO. **D5 stands**: 12 h from this repo's own timer, no PITR, and
  `pg_receivewal` → a fifth lane is still the path if RPO must be minutes. The B2 lane is
  nightly.
- It does **not** help with the recovery key. The mirrored repo is the same ciphertext.
- It is file-level, not bare-metal (`/boot` and container image layers excluded).

Retention note, recorded because this repository holds executed TOS documents and invoice
records: nothing in that bucket is purgeable for 90 days and it is never pruned, so a deletion
obligation that must reach every copy is an operator retention decision rather than a
`restic forget`.

## 2026-09-11 — P-EVAL: the `dji-log-parser` bump does not exist

FP-1's **P-EVAL** gate (ADR-0043 decision **D6**) is complete. Report:
`docs/reports/2026-09-11-dji-log-parser-upgrade-eval.md`. Harness:
`tools/p-eval/` — standalone, no DB writes, no schema change, not deployed, and
**`flight-parser/Cargo.toml` was not touched**.

**Verdict: do not adopt — there is nothing to adopt.** The newest published
`dji-log-parser` is **`0.5.7`**, which is exactly what `flight-parser/Cargo.lock`
already pins (confirmed by the crates.io API *and* `cargo search` under the
`rust:1.85-bookworm` toolchain the parser's Dockerfile pins). Upstream's last
release was 2025-04-26 and its repository has had one commit since — 15 months
dormant.

The only newer artefact is upstream `master` at `88fcfc96`, "Parse Inspire 1
battery serial numbers". Evaluated anyway, as the only candidate that exists:

- **782 comparisons over 762 distinct real DJI logs, 24 metrics each,
  6,756,743 `gps_track` coordinates: zero differences.** Bitwise, not
  within-tolerance. 759 DJI keychains fetched, 0 fetch failures, 0 parse errors,
  every log frame-decoded (all v14). Caveat stated in the report rather than
  rounded away: 19 of the 782 records decoded frames but never got a GPS fix, so
  their track comparison is vacuous — the 6,756,743 coordinates are across the
  763 records that have a track, and the header quantities those 19 fall back to
  are compared directly as 4 of the 24 metrics.
- Its only behavioural change is gated on `ProductType::Inspire1`/`Pro`/`RAW`.
  The fleet operates **no** Inspire 1, so it is unreachable here. Byte
  consumption is unchanged, so no field offset can shift.
- Adopting would swap a checksummed crates.io dependency for a git dependency on
  an unreleased commit, for no measurable gain.

**What this settles for the phases behind the gate:**

- **`Unknown(NNN)` does not resolve.** The `ProductType` enum is unchanged. All
  four placeholders persist — `Unknown(178)` Matrice 4TD, `Unknown(137)` Mavic
  4 Pro, `Unknown(139)` Mini 5 Pro, `Unknown(150)` Matrice 4T (the last appears
  only on recovered ODL-era logs and goes live at P7). 166 of 226 `dji_txt`
  flights — 73% — are on an airframe the crate cannot name. `dji.rs`'s
  `aircraft_name` fallback is permanent infrastructure, not a stopgap.
- **`SmartBatteryStatic` is not fixed upstream**, so **P2 must build the §2.4
  shim**. The plan's diagnosis is right and now sharper: every field after
  `index` is read exactly one byte early (consistent with C struct padding), so
  `raw >> 8` is correct and `swap_bytes()` is not. New limit: `>> 8` recovers the
  true value only while its top byte is zero — fine forever for
  `designed_capacity` and `full_voltage`, but **`loop_times` breaks at 256
  cycles**, and the plan's `0..=3000` plausibility gate cannot catch it (a
  260-cycle pack reads as 4).
- **P2 is unblocked and should proceed on `0.5.7`.**

**Corpus re-counted rather than inherited** (the plan's own §8 says to):
`/data/uploads/flight_logs` holds **200** files = **198** real DJI logs + the 2
dummies; 198 is exactly the hash-set intersection with `flights.source_file_hash`.
`dji_txt` rows are **226** (plan says 210; §8's correction says 218). 28 rows
still have no retained original. Plus the 584 recovered ODL-era originals, of
which 20 overlap the live set. The plan's 182/184/190/192 figures are all stale.

**Four follow-ups recorded in the report, deliberately not actioned here:**
`flight-parser/Cargo.toml` requests `"0.5"` not `"=0.5.7"` so the lockfile is the
only thing enforcing D6; `DJI_LOG_PARSER_VERSION` is a hand-maintained string
with nothing tying it to `Cargo.lock` despite being the provenance stamped on
every `flight_details` row; upstream dormancy makes `Unknown(NNN)` permanent;
and a fresh dependency resolve **no longer builds on `rust:1.85`** (the
`idna`→`icu` chain now wants 1.88), so the committed `Cargo.lock` is load-bearing
— never `cargo update` the parser without bumping its Dockerfile toolchain.

**Test output quoted, because nothing in CI runs these:**

```
tools/p-eval  cargo test   → test result: ok. 13 passed; 0 failed
tools/p-eval  --selftest   → selftest failures: 0
flight-parser cargo test   → test result: ok. 65 passed; 0 failed
```

## 2026-09-11 — docs sweep: CLAUDE.md and README told you to run a script that does not exist

A full pass over the repo's docs and metadata. Everything below was verified
against the working tree, not inherited.

**`CLAUDE.md` — four things were actively wrong**, and it is the file every
Claude Code session loads first:

- **Tech Stack said "Deploy: `update.sh` pulls latest, rebuilds changed
  services"**, and a *Server update commands* block listed `./update.sh`,
  `--clean` and `status`. `update.sh` was deleted in `e4610b5` — ADR-0018 records
  that removal, while this file kept advertising it. Replaced with the real path
  (fleet deployer on push to `main`), how to verify a deploy by what is running,
  and the `[skip-deploy]` semantics.
- **The version-bump list said "ALL 4 of these files"** and described
  `AppShell.tsx` as "the navbar footer", singular. It appears **twice** — desktop
  sidebar and mobile drawer — and the mobile one was repeatedly missed.
  `flight-parser/Cargo.toml` was absent entirely despite being the only thing
  that makes a parser deploy verifiable. Now 5 files, 6 locations.
  *(Status 2026-09-21: **6 files / 7 locations** — ADR-0046 added
  `backend/app/version.py`.)*
- **`.deployer-disabled` was described in a way that reads as "auto-deploy is
  off."** Nothing in the fleet deployer reads that marker; this repo **is**
  continuously deployed on push to `main`. The real pause is
  `noc-master/data/soak-pause/<repo>.pause`.
- **Nothing warned that there is no test job in CI.** New *Tests & CI* section:
  no pytest or cargo job exists, so every "green" claim is local and
  hand-quoted; plus the three environment traps — `aiosqlite` (absent until
  2026-09-11, and its absence shows up as 29 ERRORs with a plausible-looking
  pass count), the OTLP endpoint that defaults to production Alloy when unset,
  and WeasyPrint's native libs.

**`README.md` — documented a broken setup path to self-hosters.** Two separate
blocks described `droneops-autopull.service`, `droneops-autopull.timer`,
`autopull.sh` and `tail -f autopull.log`, and offered a `--branch` flag. All of
those were removed with ADR-0018; `setup-server.sh` installs exactly **one** unit
(`droneops.service`) and takes no `--branch`. Anyone following the README on a
fresh install would have chased units that do not exist. Rewritten to the real
boot-start behaviour plus an honest *Updating* section, including why there is
deliberately no in-repo poller.

**Also:** `ROADMAP.md` still called FP-1 PLANNED with "nothing built" while P0+P1
were live (fixed in `c920ce8`), and the Tech Stack now names the Rust parser
service and the `flight_details`/`flight_series` sidecars with the ADR-0019
constraint that the flight-library list query must not touch them.

No code changed; no version bump.

## 2026-09-05 — v2.90.0: canonical DJI serials in the fleet matcher (ADR-0044)

88 production flights (49 Matrice 4TD + 39 Matrice 4T, all
`source = 'opendronelog_import'`) sat unattributed because DJI reports a
serial in two fixed-width forms and the matcher only understood one:

- **16-char header form** — `1581F8HGX255P00A`, what the DJI log header
  carries and what the parser emits.
- **20-char OpenDroneLog form** — the same serial plus a 4-char suffix,
  `1581F8HGX255P00A0FEK`.

`_match_fleet_aircraft()` compared with exact equality only, so a 20-char
flight serial never matched a 16-char aircraft row. The Matrice 4TD row
has existed since 2026-03-16 — this was never a missing-row problem.

**Change** (`backend/app/routers/flight_library.py`): a second pass
inside ADR-0007's serial branch. `_canonical_serial()` truncates serials
of *exactly* 20 characters to *exactly* 16 and leaves every other length
alone; both sides are canonicalized and compared for **full equality**.
It is a fixed-width truncation, not a prefix test — a truncated or
partial serial canonicalizes to itself and can never equal a 16-char
canonical, which is why this is safe where ADR-0007's banned model-name
prefix rule was not. 14-char DJI FPV serials carry no suffix and are
untouched.

Invariants preserved: exact equality always wins outright; a canonical
match resolves only when it selects exactly one aircraft (two or more →
unattributed + INFO log); a serial that is present but unmatched still
never falls through to model matching.

Also hardened: the exact-serial read moved from `scalar_one_or_none()`
(which *raises* on duplicate fleet serials — `aircraft.serial_number`
has no unique index) to `.scalars().all()`, so duplicates degrade to
ambiguity instead of an exception that would abort the whole startup
backfill. Aircraft rows with a NULL/blank serial are excluded from the
canonical pass.

**On first deploy the startup backfill will attribute 88 flights** — 49
to `DJI Matrice 4TD`, 39 to `DJI Matrice 4T` — and normalize their empty
`drone_model`. Both backfill paths remain scoped to
`aircraft_id IS NULL`; no operator-curated assignment is touched.

Tests: `backend/tests/test_flight_attribution.py` grew from 12 to 22
cases. Full backend suite at this commit (rebased onto FP-1 P0+P1):
`753 passed, 17 skipped in 268.46s`, up from `743 passed, 17 skipped` on
`34553cf` — exactly the 10 cases added here. `flight-parser` is untouched
by this change and its suite is unchanged: `cargo test` `65 passed; 0
failed`. Both run locally; this repo has no pytest or cargo CI job. No
schema change, no migration.

## 2026-09-05 — chore(tests): declare `aiosqlite` in `requirements-dev.txt`

Pre-existing test-infrastructure gap, present on `main` (`34553cf`) before
this branch — not introduced by FP-1 or by ADR-0044. Seven test modules
build SQLAlchemy engines on `sqlite+aiosqlite://`, but `aiosqlite` was
declared in neither `requirements.txt` nor `requirements-dev.txt`. It
happened to be installed in the environments where the suite had been run,
so nobody hit it; a clean-room
`pip install -r requirements.txt -r requirements-dev.txt` loses those
modules at *setup*, which pytest reports as ERROR rather than FAIL — a
quieter failure than a red test. There is no pytest job in CI to catch it.

Measured, not assumed. Same image, same commit, `aiosqlite` uninstalled:

```
724 passed, 17 skipped, 29 errors in 36.46s
```

and with it declared and installed:

```
753 passed, 17 skipped in 268.46s (0:04:28)
```

Exactly 29 tests across 7 modules were silently not running.

Pinned `aiosqlite==0.20.0` — the release contemporaneous with the pinned
`sqlalchemy[asyncio]==2.0.36` — in `requirements-dev.txt`, **not**
`requirements.txt`: production runs asyncpg against Postgres and must not
gain a SQLite driver. No runtime code changed and the prod image installs
`requirements.txt` only, so this cannot affect a deploy.

## 2026-09-05 — v2.83.0 / parser 1.2.0: FP-1 P1 — Tier 0 parser pass

The extended DJI-log data now actually gets extracted and stored. Everything
here comes from `log.frames()`, which the parser already iterates — **no second
decode and no second DJI keychain round-trip.**

**New `flight-parser/src/details.rs`.** A streaming accumulator riding the
existing frame loop, producing ~50 typed scalars, five JSONB groups and 15
full-resolution series per flight: time base, MSL/VPS altitude,
distance-from-home, vertical rate, aircraft and gimbal attitude, RC up/downlink,
battery current and cell-voltage deviation.

- **Full resolution at rest** (operator decision D2). Scalars are computed over
  every frame; series keep one value per frame. Reduction happens only at the
  API layer.
- **Per-quantity rounding** (§2.5) is what pays for it: 1 dp for metres, 2 for
  m/s, 3 for volts, 7 for lat/lon, and so on. `193.90000000000001` → `193.9`
  drops f64 mantissa noise and nothing else — every sample is kept and the
  stored text is ~4x smaller.
- **Missing samples are `null`, never `0.0`.** An RC link with no OFDM record
  yet, or a distance-from-home with no GPS fix, stores a gap. A `0` there would
  read as "signal lost" / "at the home point" — a different and alarming claim
  about the flight.
- **Integrals use the real inter-frame interval**, so `battery_energy_wh` is
  invariant to log rate. A frame-count-times-assumed-cadence integration is the
  ADR-0027 mistake in a new place.
- **Event extraction** (§2.6): garbled prefixes trimmed and flagged, dedupe on
  the cleaned string (the census's 18 identical "Remote controller
  disconnected" strings collapse to one record with `count: 18`), and a
  remnant under 8 characters becomes `kind: "unparsed"` rather than a guess.
- **Time-base provenance.** `FrameCustom::default()` is the Unix epoch, so a
  log with no `Custom` records would stamp 1970 on every sample. The base is
  chosen explicitly — wall clock, else DJI's own `fly_time` counter, else none
  — and recorded in `config.time_base`.

**Three long-empty fields now carry data.** `TelemetryData.timestamps`,
`signal_strength` and `distance_from_home` were hard-coded empty/`None`, which
is why `/telemetry` has always served `signal_strength: null`. They go from
null to arrays; the frontend already types them permissively.
`TrackPoint.timestamp` is populated when the log carries a real clock.

**`Unknown(NNN)` → `aircraft_name` fallback.** The crate's `ProductType` enum
predates the Mavic 4 Pro and Matrice 4 series, so those airframes render as the
literal `Unknown(178)` — 150 of 210 stored DJI flights carry it. New imports
now fall back to the header's `aircraft_name`. The predicate is anchored on
both ends, so a real model name can never be displaced.

**Litchi / Airdata are untouched.** `details` is a struct-literal field, so
adding it was a compile error in both until each declared `details: None` — the
compiler enforced the audit. `skip_serializing_if` omits the key entirely, so
their JSON output is byte-identical; both have a test asserting it.

**Backend persistence.** `app/services/flight_details_writer.py`, called from
`_build_flight_from_parsed` inside the **same best-effort savepoint pattern**
battery tracking already uses. A details failure costs a WARN line and nothing
else — the flight record is the operator's work product; extended data is not.
The payload crosses a service boundary, so every value is coerced against its
column: integers range-checked against their SQL width, strings truncated,
NaN/inf and uncoercible values dropped to NULL, series bounded and deduped on
the primary key, and `sample_count` recomputed rather than trusted.

**A cross-language wire fixture.** `backend/tests/fixtures/parser_details_payload.json`
is generated by the Rust suite (`cargo test emit_wire_fixture`) and asserted
against by both sides. This exists because the silent failure mode of a JSON
boundary between two languages is: rename a field on one side, every column
writes NULL, and the import still logs success. Verified by deliberately
drifting the fixture — `photo_count` → `photoCount` turned three tests red,
including `assert None == 2` on the stored row.

Test output quoted in `PROGRESS.md`: `cargo test` 65 passed; `pytest` 759
passed / 1 skipped with a live Postgres.

## 2026-09-05 — v2.82.0: FP-1 P0 — flight-details schema + read path (inert)

First phase of FP-1 (ADR-0043, plan
`docs/plans/2026-09-04-flight-details-data-ingestion.md`). **Nothing writes to
the new tables yet and no existing behaviour changes** — the schema and the
read surface land first so the parser phase has somewhere to put data.

**Schema — two sidecar tables, not more columns on `flights`.**
`flights` is the subject of three OOM ADRs and already carries three heavy
JSON columns; widening it would make every `select(Flight)` heavier, protected
only by remembering to `defer()`. Migration `0010_flight_details` creates
`flight_details` (1:1, ~76 typed scalars + eight JSONB groups) and
`flight_series` (one row per named series, PK `(flight_id, source, name)`).
Migration `0011_battery_src_truth` adds three nullable battery columns
(`batteries.cycle_count_observed`, `batteries.metrics_source`,
`battery_logs.pack_cycle_count`) one phase early, so the phase that actually
switches battery semantics needs no migration of its own. Both migrations are
idempotent per ADR-0042 — `0001` builds fresh databases from the live models,
so on a fresh install both must no-op instead of raising.

`Flight.details` / `Flight.series` are declared `lazy="noload"`. That is
load-bearing: `selectin` would join the sidecars into every `select(Flight)`
including the 500-row mission-picker query, which is ADR-0019's production OOM
with a larger payload.

**Read path.** `GET /{id}/details`, `GET /{id}/details/series`,
`GET /details/status`. `/details` never 404s on a missing row — per operator
decision D3 the link renders on every flight, so "no extended data" is a
normal response carrying an `unavailable_reason` (`source_unsupported` /
`not_backfilled` / `odl_import_no_original`). The flight row is read
column-explicitly and the series index selects every column except `values`.

**`downsample` extracted** from the closure inside `get_telemetry` to
`app/services/telemetry_downsample.py`, now shared by `/telemetry` and
`/details/series`. ADR-0032's own conclusion is that the absence of a shared
layer is what lets a unit-defect class recur; a second copy-pasted
downsampler would be the same mistake. Behaviour is pinned identical to the
old closure by a parity sweep over 60 (length, target) pairs. **One
deliberate difference:** `max_points=1` used to raise `ZeroDivisionError` (a
500 from a query string clients are free to send, since `/telemetry` declares
no lower bound); it now returns the first sample.

**Report-audience guard (ADR-0043 §4.4 / D1).** Operator decision D1 stores
the pilot's raw position track. `reports.REPORT_READABLE_DETAIL_FIELDS` is an
empty allowlist documenting that nothing from the details surface is
report-eligible, and a guard test asserts the six client-artifact-producing
modules reference neither table, plus a runtime test that the CSV/GPX/KML
exporters emit no pilot fields. ADR-0029 unchanged: no altitude is compared
to any limit anywhere in this work.

**Encoding measured, not argued (plan §1.5 / C-2).** One flight's series
written both ways into scratch tables on a real `postgres:16-alpine`, 13,870
samples: `json` 99,012 B vs `float8[]` 109,896 B vs `jsonb` 125,376 B, and
`json` read+parse 4.34 ms vs `float8[]` 9.43 ms. **`json` confirmed** — it is
both smaller and ~2.2x faster, overturning the plan's speculation that a
native float array might read faster. Numbers in `PROGRESS.md`.

Tests: +113 (`729 passed, 1 skipped` with a live Postgres; `723 passed,
7 skipped` hermetic). Includes a real-Postgres tier covering the *production*
upgrade path — an existing DB stamped at 0009 with the objects absent, which
is the only path that executes the `create_table` / `add_column` bodies at
all; the pre-existing fresh-DB tier only ever exercises the idempotency
guards.

## 2026-09-01 — v2.81.0: six new billable-rate templates

Operator-directed expansion of the seeded billable rates (PV/solar
inspection service line):

- **PV Thermal Inspection — Field Day** — Billed Time, $2,200.00 flat
- **Mobilization — Regional Overnight** — Travel, $850.00 flat
- **Lodging + Per Diem** — Travel, $235.00 per day
- **Weather Standby** — Billed Time, $1,100.00 flat
- **Data Processing & QA** — Billed Time, $150.00/hr
- **Third-Party Analytics (pass-through)** — Other, billed at vendor
  cost with **no markup** (operator decision 2026-09-01; earlier +15%
  and +10% drafts both rejected). The schema has no formula field, so
  the template is seeded at $0.00 with the rule in its description —
  the invoicing operator enters the vendor amount as the unit price.

Mechanics: `backend/app/seed.py` rate templates hoisted to a module-level
`RATE_TEMPLATE_SEED` constant (same pattern as `AIRCRAFT_SEED`); the
startup seed inserts by name only when missing, so existing prod rows
(including any operator edits to the original eight) are untouched. New
hermetic test `backend/tests/test_rate_template_seed.py` (9 tests) locks
the list. No schema change, no migration.

## 2026-08-23 — v2.80.4: startup failures are loud + ADR-0042

Closes the last residual from the 2026-08-22 fresh-install audit. Root
cause of the silence, found by reproducing the failure in a scratch
container: `alembic/env.py` ran `fileConfig(alembic.ini)` on every
invocation, and `fileConfig()` defaults to `disable_existing_loggers=True`
— so the moment `command.upgrade()` ran during startup, the app's `doc`
logger AND `uvicorn.error` were disabled, and every subsequent message
(including the exception that explained the crash and uvicorn's
"Application startup failed") was dropped. That is why the seven-week
fresh-install crash loop (v2.80.2) restarted every ~5 s with logs that
simply ended mid-migration.

- `alembic/env.py`: `fileConfig()` now runs only for the standalone CLI —
  skipped when the programmatic path passes its connection via
  `config.attributes` — so app logging survives migrations.
- `app/main.py`: the entire pre-yield lifespan body is wrapped; on any
  exception the full traceback is logged (`STARTUP FAILED — …`) and also
  printed directly to stderr (immune to any future logging reconfig),
  then re-raised.
- Verified: forced a bogus `alembic_version` revision in a scratch
  container — exit 3 as before, but `docker logs` now carries the full
  traceback ending in the real error.

New **ADR-0042** (`docs/adr/0042-fresh-install-integrity-and-demo-hygiene.md`)
records the whole 2026-08-22 incident and the standing decisions:
post-baseline migrations must be idempotent, startup failures must be
loud, the demo resets nightly (BOS crontab + `scripts/demo-nightly-reset.sh`,
ntfy on failure), and no realistic random literals in tests.

## 2026-08-22 — v2.80.3: green Secret Scan + nightly demo reset

Two "big-time readiness" items from the same audit that produced v2.80.2:

- **Secret Scan CI red on every push since the ToS tests landed** — gitleaks
  flagged the realistic random `intake_token` literal in
  `backend/tests/test_tos_accept_route_body.py` (generic-api-key,
  entropy 4.9). It was a test-only round-trip value with no format
  constraint beyond `max_length=64`, so it is now an obviously-fake
  low-entropy stand-in (`TESTONLY-…`) instead of an allowlist entry —
  no weakening of the gate, robust to line-number drift, and the workflow
  goes green. A permanently-red public workflow both looks broken and
  trains everyone to ignore the one gate that matters.
- **`scripts/demo-nightly-reset.sh`** — the demo instance accumulated
  months of visitor junk (uploaded flight logs with real GPS, third-party
  contact emails) because `DEMO_RESET_INTERVAL_HOURS` is configured but
  unimplemented. Until an in-backend reset task exists (celery beat is not
  an option — the demo worker/beat must stay stopped, dunning-email
  hazard), this script is the reset: wipe the demo schema, restart the
  backend (startup rebuilds via alembic + demo seed), wait for healthy,
  verify the trial login end-to-end, and page `droneops-demo-reset` (high)
  via the ADR-0036 helper on any failure. Installed in the BOS-HQ operator
  crontab at 09:23 UTC (2:23 AM PDT) daily, following the existing
  snapshot-cron pattern.

## 2026-08-22 — v2.80.2: fix fresh-install crash loop in migrations 0008/0009

Every fresh database — the demo instance reseed, the self-host Quick Start,
and future managed-client provisioning — crash-looped at startup since
0008 landed (2026-07-05). Root cause: `0001_baseline_schema` builds fresh
DBs with `Base.metadata.create_all` from the **live** models, which already
include the columns 0008/0009 add, so their bare `op.add_column` raised
`DuplicateColumn`. Existing databases (prod) never noticed: they are stamped
past the baseline and their columns were added by the ORM path before the
migrations existed.

The failure was silent: uvicorn exits 3 on lifespan-startup failure and the
migration exception's traceback never reached the container logs — the demo
just looped every ~5 s. Diagnosed by running `alembic upgrade head` in a
one-off container, which surfaced the real `DuplicateColumn`.

- `0008_report_dl_payment_override` and `0009_mission_dl_email_sent_at` now
  no-op when their column already exists (inspector guard), matching the
  idempotent house style of 0001–0007.
- **Rule going forward:** every post-baseline schema migration must be
  written idempotently, because 0001 always produces the current-model
  schema on fresh DBs. A bare `op.add_column`/`op.create_table` WILL break
  fresh installs while passing on every existing DB and in CI against
  migrated schemas.
- Known residual: the swallowed startup traceback (uvicorn exit 3 with no
  logged exception) made this a forensic hunt; surfacing lifespan failures
  in logs is a candidate follow-up.

## 2026-08-19 — cloudflared 2026.3.0 -> 2026.8.2 (fleet-wide version-rot remediation)

This stack's tunnel connector was running cloudflared 2026.3.0. Cloudflare
supports releases "within one year of the most recent release"; a 2026-08-19
fleet survey found 22 connectors, most months behind, and one (the ntfy alerting
tunnel) already outside that window.

It rots silently by construction: the official cloudflare/cloudflared Docker
image disables the built-in self-updater, a floating `:latest` tag only resolves
at container recreate, and the NOC deployer reacts to git commits rather than
upstream image releases. Nothing was ever going to notice.

Image tag only — no functional, ingress, auth or routing change. Fleet-wide
reasoning, survey, alternatives considered and the monthly supervised bump
routine: `noc-master/docs/adr/0212-cloudflared-version-rot.md`.

## 2026-08-18 — ops(standby): BK-2 executed — standby archive_mode off, 1.5 GiB stale WAL reclaimed [skip-deploy]

The droneops standby on svdp-dev (`droneops-db-standby`) carried inherited
`archive_mode='on'` + the Gap-7 `archive_command` in `postgresql.auto.conf`
(ALTER SYSTEM is blocked in recovery, so the file was edited directly), plus
**1.5 GiB / 99 stale WAL segments** in its own `wal_archive` from the seeding
basebackup. Fixed and deleted; two standby restarts (first attempt targeted
`postgresql.conf`, where the setting does not live — `pg_settings.sourcefile`
pointed to `auto.conf`). Verified: standby in recovery, `archive_mode=off`,
wal receiver `streaming`, primary slot active, `replay_lag` empty, pgdata
2.3 G → 851 M. Production primary untouched. A promotion can no longer
recreate Gap 7. ROADMAP BK-2 closed.

## 2026-08-18 — ops(backups): yearly retention → unlimited (operator decision) [skip-deploy]

Bill: "retention is indefinite." `KEEP_YEARLY` 7 → `unlimited` in
`droneops-backup.sh`; yearly backup snapshots are never pruned (ADR-0041 D4
amended). Backup history only — live flight data was never subject to any
retention. Daily/weekly/monthly tiers unchanged (14/8/24).

## 2026-08-18 — ops(volumes): orphaned legacy volumes archived + removed [skip-deploy]

Operator-approved disposal of the two truly orphaned volumes on BOS-HQ
(referenced by zero containers and zero compose files, verified before
touching):

- **`droneops_postgres_data`** (46 MB) — the pre-promotion BOS primary pgdata,
  superseded when `droneops-standby-db` was promoted. Archived first into the
  encrypted restic repo as a tar (tag `legacy-bos-primary-pgdata`, snapshot
  `66ed2135`, restore-read verified: 1,204 entries incl. `PG_VERSION`), then
  removed.
- **`droneops-demo_ollama_data`** (4 KB, empty; demo compose has ollama
  disabled) — removed, nothing to archive.

**The demo stack was NOT touched** — it is live and tunnel-exposed (6 healthy
containers up 2 weeks); its data volumes are in active use and are not
"legacy". `droneops-gw_caddy_data` (live gateway ACME state) also untouched.
Post-check: 103 containers running, demo 6/6 healthy, prod DB accepting
connections.

## 2026-08-18 — ops(backups): §5.7 cutover automated — one-shot gated timer on droneops-server [skip-deploy]

Operator approved executing the cutover without waiting for a live session.
New `scripts/droneops-backup-cutover.sh` runs once via a user-level systemd
timer on droneops-server (HSH-HQ — the host with both BOS ssh access and repo
push credentials; HSH's `/etc` is bind-mounted read-only, so user units are
the local pattern) at **2026-08-20 04:12 UTC**, after the soak window's final
run. Fail-closed: all four gates re-verified over ssh before any mutation, any
failure aborts pre-mutation and pages `infrawatch-alerts` at `high`; success
notifies at `default`. Dry-run validated under the systemd user environment on
2026-08-18 (refused correctly on the not-yet-met ≥4-snapshot gate, ntfy
suppressed). The script self-documents the executed cutover into PROGRESS.md
and pushes.

## 2026-08-17 — ops(backups): legacy n8n final state archived, HSH stale dumps retired [skip-deploy]

Operator-approved cleanup of the retired HSH-HQ backup lane (`~/backups/` on
droneops-server, dead since 2026-04-15). The final n8n SQLite dump
(`n8n_20260415_020001.sqlite`, 218 MB, sha256 `ae28fe7b…`) — the last surviving
data from n8n, decommissioned fleet-wide 2026-04-21 — was archived into this
repo's encrypted R2 restic repository under its own tag **`legacy-n8n`**
(snapshot `1d0cfb76`, 19 MiB stored), restore-verified byte-identical, and all
local copies deleted (~994 MB reclaimed, dirs removed). Not a DroneOps
artifact; parked here because this is the fleet's encrypted archive repo — see
the lane table in `docs/runbooks/droneops-backup-restore.md`. The retirement
README at droneops-server `~/backups/README.RETIRED.md` records the disposition.

## 2026-08-17 — ops(backups): cold DR rehearsal + four review defects (ADR-0041 as-built) [skip-deploy]

Adversarial re-review of the backup lane shipped earlier the same day
(`e43018f`), plus the **first cold disaster-recovery rehearsal**: a full
rebuild from the **1Password Fleet items and the R2 bucket only**, executed on
`droneops-server`, with nothing read from BOS-HQ except comparison hashes and
no production container or volume touched.

**The rehearsal passed.** Recovery works cold. Three independent paths — restic
from R2, the plain break-glass `.sql.gz`, and live production — produced
**identical content digests**: `flights_digest=4d6d9276…`, 146,420,719 bytes of
telemetry, 334,757,775 bytes of GPS track, 6,842,636 telemetry points. All 226
files in the `files` lane are sha256-identical to production, and all 10
executed TOS PDFs match `tos_acceptances.signed_sha256` — a cross-lane proof
that the db and files lanes agree with each other. The restored `.env` matches
live byte-for-byte, and the full 11-service stack renders from restored config
alone; with `.env` removed the same render is correctly *refused* on the
ADR-0012 `:?` guard, so that check is not vacuous. Full evidence table:
`docs/runbooks/droneops-backup-restore.md` §11.

Four defects found and fixed (`c3d9502`) — none had broken a restore, but each
could have:

* **No concurrency guard.** The runbook tells operators to run the backup by
  hand; systemd blocks a second *service* start but not a manual shell run
  overlapping a timer run. Both would contend for restic's exclusive lock
  during `forget --prune`, turning a benign overlap into a `high` page. Added
  `flock`; an overlap exits 0 *without* stamping the freshness metric, so a
  one-off overlap is silent while a persistent one is still caught within 28 h.
* **`backups/` was not gitignored** — 1.1 GB of *plaintext* pg dumps (customer
  PII, invoice records, `device_api_keys`) sitting untracked in the deploy
  clone's working tree, one `git add -A` from being committed. Now ignored.
* **The quarterly drill never read the `files` lane** — 657 MiB of flight logs,
  report deliverables and executed TOS PDFs, the largest lane. It certified
  "restorable" while never touching it. Now asserted via a cross-lane sha256
  check rather than a file-count floor, which would stay green against a stale
  or truncated snapshot.
* **Post-metric error hole.** The local retention sweep ran after the freshness
  stamp under `set -e` with no `|| fail`, so a failure exited non-zero with no
  ntfy and a green metric — visible only in systemd.

Also fixed: **Procedure A2's first database command did not work.**
`docker compose up -d droneops-standby-db` fails with `no such service` — the
service is `db-standby`; `droneops-standby-db` is the *container* name. This
was in the critical path of a from-nothing recovery.

Explicitly re-verified and **not** changed: `forget --group-by tags` retention
is correct. A 40-day, twice-daily synthetic corpus (80 snapshots) converged to
exactly 20 — 14 daily + weeklies + monthly, one per day. The two same-day
snapshots per lane currently visible in R2 are a transient artifact of a
one-day-old repository, not a policy fault. Bash *does* run the `EXIT` trap on
`SIGTERM` (tested), so the workdir holding the repository password is not
leaked on a systemd timeout. `fail()` does **not** fail open when ntfy is
absent — exit 1 confirmed with the helper removed.

Minor, recorded not fixed: `.env` carries `FRONTEND_URL` twice (42 assignments,
41 unique keys); `uploads/tos_signed/` holds 11 PDFs against 10
`tos_acceptances` rows (one orphan from an abandoned signing flow, correctly
backed up).

## 2026-08-17 — ops(backups): comprehensive ENCRYPTED backup to R2 (ADR-0041) [skip-deploy]

Ops-scripts + docs only (no app change, no version bump). Closes the seven gaps
in the backup lane. The old plaintext lane is **still running in parallel** and
is not removed by this change — cutover is gated on three green days
(`PROGRESS.md`).

* **`scripts/droneops-backup.sh`** (new, supersedes `snapshot.sh`) — four
  restic lanes into a **dedicated, encrypted** R2 repository: `db`
  (`pg_dump -Fc`), `files` (`uploads/` **+ `reports/`**), `config`, and a
  one-shot `legacy`. Fail-closed on every path; the freshness metric is
  stamped only after all lanes, the retention pass and the integrity check
  succeed.
* **Encryption + a dedicated credential.** New R2 bucket `droneops-backups`
  with a **bucket-scoped** token replaces the account-wide
  `OBS_GLITCHTIP_BACKUPS_R2_*` reuse — blast radius drops from four services
  to one. Customer PII, invoice records, `device_api_keys` and the 11 executed
  TOS PDFs are no longer stored in the clear. `RESTIC_PASSWORD` and the R2
  credentials are filed to the 1Password **Fleet** vault (ADR-0086); the
  password is the recovery key and is unrecoverable if lost.
* **Host config is finally backed up** — `.env` (41 keys incl.
  `JWT_SECRET_KEY`, `POSTGRES_PASSWORD`, `CLOUDFLARE_TUNNEL_TOKEN`) and the
  compose overrides. Restoring DB + files without these produced a stack that
  *could not boot*. The lane is an explicit allowlist, so rotated `.env.bak-*`
  secrets cannot be swept in.
* **`uploads/` gains real history.** `aws s3 sync` was an additive mirror that
  faithfully copied corruption over the only good copy; snapshots make a
  damaged flight log recoverable.
* **Retention is now enforced in R2** — `forget --prune`
  14d/8w/24m/7y `--group-by tags`, replacing a sweep that pruned only the local
  copy while R2 grew unbounded at ~54 MB/day.
* **Dedup verified, not assumed.** The dump is fed to restic **uncompressed**
  (`-Z0`); a second full run added **414 KiB** (`files` lane: 0 B). Compressing
  first would have stored a fresh ~55 MB nightly forever.
* **WAL archiving retired.** `archive_mode=off` + `wal_archive/` deleted:
  it wrote into the very volume it protected, had never been pruned
  (5.5 GiB / 358 segments), and had not archived since 2026-07-22 — a 26-day
  hole. **Reclaimed 5.5 GiB** (`droneops_standby_pgdata` 8.0G → 2.5G); the
  `chad_hq_standby` slot stayed `active` throughout. Plus 68 MB of stale
  in-app dumps and ~130 MB of dead pre-migration dumps on droneops-server
  (archived to the `legacy` lane first).
* **`scripts/restore-drill.sh`** — migrated to restic (`pg_restore`, not
  `gunzip | psql`). Preserves the 90 %-of-live `flights` ratio, the <48 h
  freshness assertion, the throwaway DB and its `trap` cleanup. **Adds a
  `config`-lane assertion**: `.env` is restored and sha256-compared against the
  live file — the only check that proves the critical gap stays closed.
* **`scripts/systemd/droneops-backup.{service,timer}`** — twice daily,
  03:23 + 15:23 UTC (RPO 24 h → **12 h**), `Persistent=true`. UTC deliberately:
  the BOS-HQ nightly window is stacked in UTC and a local-time entry would
  DST-collide with a sibling job twice a year.
* **The failure path was observed firing**, not assumed: a deliberately
  corrupted R2 secret produced exit 1 and one `high` ntfy on
  `infrawatch-alerts` with the previously-missing click URL
  (`noc-mastercontrol.barnardhq.com/status/droneops`, HTTP 200) — and exactly
  one notification across two failures, confirming the 6 h cooldown.
* **Metric names deliberately unchanged.**
  `droneops_backup_last_success_timestamp_seconds` and
  `droneops_restore_drill_last_success_timestamp_seconds` are a hard contract
  with two live Grafana rules; renaming either would have converted a live
  alert into a permanently-green dead man.

New runbook: `docs/runbooks/droneops-backup-restore.md` (full DR, DB-only
rollback, single-file recovery, break-glass without the restic password, and
how to run the drill). Decision record: `docs/adr/0041-*.md`.

## 2026-08-05 — ops(data): prod maintenance-alert clear + 30→90-day interval tune [skip-deploy]

Data-only change in the prod DB (no code, no schema, no version bump). On
Bill's direction, in three passes:

* **Schedule-based clear:** reset `last_performed` to 2026-08-05 on all 8
  overdue `maintenance_schedules` (same semantics as the app's Skip/Defer
  endpoints), extended all nine 30-day `interval_days` to 90 (Sensor
  Cleaning / Battery Health Check / Firmware Review across Avata 2 /
  Matrice 30T / Matrice 4TD), and deferred the two Matrice 4TD 90-day items
  inside the due-soon window (IMU Calibration, Remote Controller Inspection).
* **Correction — record-based alerts:** the above did NOT clear the dashboard;
  `GET /maintenance/due` has a second alert source, `maintenance_records.next_due_date`.
  Four March-2026 service records carried stale due dates (up to 82 d overdue,
  on aircraft with no schedules at all — Mini 5 Pro, Mavic 3 Pro, plus
  Matrice 30T and Avata 2). Set `next_due_date = NULL` on those 4 rows
  (service history retained).
* **Final verified state:** all four dashboard alert sources at zero
  (schedule date-based, schedule never-performed, record `next_due_date`,
  battery health/cycles). Next date-based due item: Avata 2 Gimbal
  Calibration 2026-10-04.

Details, the schedules-JOIN verification blindness, and the seed-defaults
re-seed gotcha: `docs/ops/2026-08-05-prod-maintenance-interval-tune.md`.

## 2026-07-22 — ops(backups): automated quarterly R2 restore drill [skip-deploy]

Ops-script only (no app/version change). Closes the last discipline gap in the
backup lane: the quarterly restore drill documented in `scripts/snapshot.sh`
was manual — it relied on an operator remembering every ~92 days.

* **`scripts/restore-drill.sh`** — downloads the *newest R2 dump* (the off-host
  copy that matters in a disaster, not the local file), verifies gzip integrity
  and that the dump is <48 h old, restores it into a throwaway
  `droneops_restore_drill` database on `droneops-standby-db`, sanity-checks
  restored row counts (`flights` ≥90 % of live, `battery_logs` /
  `tos_acceptances` non-empty), then drops the scratch DB (trap-guaranteed).
* **`scripts/systemd/droneops-restore-drill.{service,timer}`** — installed on
  BOS-HQ at `/etc/systemd/system/`, `OnCalendar=*-01,04,07,10-16 16:23 UTC`
  (quarterly on the 16th, anchored to the 2026-07-16 install-time verified
  restore), `Persistent=true` so a powered-off host catches up.
* **Self-watching:** full success writes node-exporter textfile metric
  `droneops_restore_drill_last_success_timestamp_seconds`; an InfraWatch
  Grafana rule pages `infrawatch-alerts` if the drill has not succeeded in
  >100 days or the metric is absent. Failure fires an ntfy `high`
  (dedup `droneops-restore-drill`, 6 h cooldown); success posts one
  `default`-priority note (4×/year, ADR-0037 digest class).
* **Proved live 2026-07-22:** restored `droneops/db/2026/07/22/…sql.gz` from
  R2, verified flights=760/760, battery_logs=759, tos_acceptances=10; scratch
  DB dropped; metric stamped.


## 2026-07-19 — ops(standby): silence chronic healthcheck FATAL spam on droneops-db-standby [skip-deploy]

Compose-only (no app/version change). The standby's healthcheck ran
`pg_isready -U replicator`; dbname defaults to the username and no
`replicator` database exists, so PostgreSQL logged
`FATAL: database "replicator" does not exist` every 10 s (~8.5k lines/day)
while the check still passed. Healthcheck now probes `-U droneops -d droneops`
(the app role+db, present on the standby via replication). Applied live on
CHAD-HQ (10.99.0.2) by recreating `droneops-db-standby`.

## 2026-07-16 — ops(backups): off-host R2 push + fix broken tos_signed path + freshness metric [skip-deploy]

Ops-script only (no app/version change). The 2026-07-16 BOS backup audit found
the nightly `scripts/snapshot.sh` dump was **local-only** (no off-host copy)
and its signed-TOS step was silently no-op'ing every night — it tarred
`${REPO_ROOT}/data/tos_signed`, a path that never existed. The real signed
legal PDFs live in the `droneops_app_data` Docker volume at `uploads/tos_signed`
(alongside `uploads/flight_logs`, ~557M total).

* **Off-host DB push.** After the local gzipped `pg_dump` (unchanged, 14-day
  local retention), the dump is streamed to Cloudflare R2 at
  `s3://<obs bucket>/droneops/db/YYYY/MM/DD/droneops-<TS>.sql.gz`, reusing the
  obs R2 credential source (`/opt/observability/.env`) and the shared
  `obs-glitchtip-backups` bucket with a dedicated `droneops/` prefix.
* **Fixed + expanded uploads coverage.** Replaced the broken `data/tos_signed`
  tar with an incremental `aws s3 sync` of the volume's `uploads/` tree
  (signed-TOS PDFs + flight logs) to `s3://<obs bucket>/droneops/uploads/`,
  mounting the volume read-only.
* **Freshness metric + alerting.** On FULL success writes
  `droneops_backup_last_success_timestamp_seconds` to the node-exporter
  textfile collector; InfraWatch `obs-rule-droneops-backup-stale` pages on
  >28h/never-written. Any dump/upload failure pushes ntfy `high` to the
  existing `infrawatch-alerts` topic. Removed the silent `|| true` swallow.

## 2026-07-06 — fix(payments): delivery verification pass — two real bugs + e2e endpoint tests — v2.80.1 (ADR-0040 addendum)

End-to-end verification of the v2.80.0 automation caught two bugs before any
prod payment exercised them (full detail in the ADR-0040 addendum):

* **`Mission.invoice` lazy="noload" identity-map trap.** Every trigger path
  loads the mission before the delivery service runs, so the service's
  `selectinload(Mission.invoice)` re-query returned the identity-mapped
  mission WITHOUT repopulating the relationship — the gate read
  `invoice=None` and skipped `not-paid-in-full` on PAID missions. The Stripe
  webhook and mission-update triggers were silently dead. Fix: the service
  queries the Invoice table directly; `_delivery_skip_reason(mission,
  invoice)` takes it explicitly.
* **SMTP-unconfigured no-op was stamped as sent.** `_send_html_email`
  returns False when SMTP isn't configured; the stamp was written anyway,
  permanently losing the delivery. Fix: stamp only on a True send; the False
  path returns `skipped:smtp-unconfigured` (WARN) and stays armed.
* New `test_download_link_delivery_e2e.py`: 9 endpoint-level tests driving
  the REAL `update_invoice` / `update_mission` / `get_client_mission`
  functions against sqlite (house pattern; includes a JSONB→JSON sqlite
  shim for the reports table). These are the tests that caught bug #1.
* **Report-editor override could be silently lost (independent review
  finding).** Generate Report / Generate PDF re-baselined the unsaved
  `paymentOverride` switch without persisting it — the dirty-guard went
  quiet and the server kept override=false, so the operator believed the
  link was released while PDF + email withheld it. Fix: the pre-PDF PUT now
  persists `include_download_link` + `download_link_payment_override` (the
  PDF renders against what the operator sees), and the generate paths
  preserve the previous baseline so an unsaved flip stays dirty.
* Bypass sweep confirmed: portal + report PDF/email are the only
  client-reachable `download_link_url` surfaces, all gated; docs updated
  (README feature sections, PROGRESS, ADR-0040 addendum).
* Suites: 607 backend / 53 frontend pass; tsc clean.

## 2026-07-06 — feat(payments): automated download-link delivery on payment-in-full — v2.80.0 (ADR-0040)

Completes ADR-0039: payment-in-full is now a TRIGGER, not just a gate. Per
Bill (2026-07-06): no manual report regeneration — when the client pays they
get the link in a separate automated follow-up email and it populates in the
client portal.

* **Delivery service** `app/services/download_link_delivery.py` — also now
  owns the ADR-0039 gate policy (reports router + portal import it; one
  source of truth). Sends branded `download_link_email.html`, stamps
  `missions.download_link_email_sent_at` (migration
  `0009_mission_dl_email_sent_at`) AFTER a successful send so failures retry
  on the next trigger. Fail-soft: never breaks the payment flow.
* **Three triggers:** Stripe balance-paid webhook; manual mark-paid
  (`PUT /invoice` false→true transition); download URL set/changed on the
  mission (covers footage-ready-after-payment; a URL change RESETS the dedup
  stamp so replacement links re-deliver).
* **Client portal:** `GET /api/client/missions/{id}` returns
  `download_url`/`download_expires_at` only when the gate passes; the
  DELIVERABLES card shows the download button when unlocked ("unlocks when
  the invoice is paid in full" while unpaid), and the post-payment poll
  re-pulls the mission so the link appears without a reload. Gotcha honored:
  `Mission.invoice` is lazy="noload" — endpoint eager-loads it explicitly.
* Skip conditions logged with reason: no-url / already-sent / not-billable /
  not-paid-in-full / link-expired (WARN) / no-customer-email (WARN).
* Tests: 16 new (`test_download_link_delivery.py`); portal fixture +
  migration fence updated; 598 backend / 53 frontend pass.

## 2026-07-05 — feat(reports): unpaid-invoice download-link gate + operator override — v2.79.0 (ADR-0039)

Policy (Bill, 2026-07-05): **clients do not get the mission-footage download
link until the invoice is paid in full.** Trigger: the 2026-07-02 River M.
report went out with the footage link while BARNARDHQ-2026-0005 ($400.50) was
unpaid — nothing in the code checked payment.

* **Server-side gate at a single choke point.** Both exposure paths (report
  PDF render + report email) now build the link only via
  `_build_download_link()` → `_download_link_payment_blocked()`
  (`backend/app/routers/reports.py`). Withholds while a billable mission's
  invoice is unpaid; **fail-closed** when billable-but-never-invoiced; $0
  invoices and non-billable missions pass. Deposit alone does NOT release —
  only `paid_in_full`.
* **Per-report operator override** `reports.download_link_payment_override`
  (migration `0008_report_dl_payment_override`, additive, default false).
  Settable only via `PUT /report` (never the generate path, so regeneration
  can't reset it); every flip is audit-logged with the acting user.
* **Editor surface** (`MissionReportEdit.tsx`): yellow "link withheld —
  invoice not paid in full" alert + orange override switch when the link is
  requested and payment is outstanding; the Sent toast says explicitly when
  the link was withheld (`download_link_withheld` on the send response).
  `GET/PUT /report` return computed `download_link_payment_blocked`.
* **Withholding never blocks the report itself** — the client still gets the
  report; only the footage link is held.
* **Residual:** a PDF rendered pre-gate carries the baked-in link; send
  warn-logs this and River's stale `pdf_path` was invalidated in prod. See
  ADR-0039 for the full policy + alternatives.
* Tests: 14 new gate tests (`test_report_download_link_payment_gate.py`);
  migration-fence + ADR-0038 fixtures updated; 582 backend / 53 frontend pass.

## 2026-07-03 — feat(reports): client-report narrative quality levers — v2.77.0 (ADR-0035)

Guard-safe quality pass on the shared report system prompt
(`SYSTEM_PROMPT_TEMPLATE` in `backend/app/services/ollama.py`, inherited by the
Claude path via `claude_llm.py`). Implements the top three levers of
**docs/plans/2026-07-03-report-quality.md** (`FU-AI-QUALITY-PASS`):

* **Kill hedging (§3.1).** Requires definitive, active-voice authority; forbids
  "appeared to" / "seemed" / "was observed to" / "it is likely" softeners unless
  the data is genuinely uncertain.
* **Anti-bloat budget (§3.2).** Each section is 2–5 sentences of substance — no
  padding, no restating the heading, no generic boilerplate; brevity on a routine
  flight is professional, not a defect.
* **Number-grounding (§3.3).** Grounds every claim in the provided figures
  (flight count / total time / distance / aircraft with units; area acreage); no
  vague quantities when an exact number exists.
* **Guard integrity (ADR-0029).** The number-grounding lever carries an explicit
  altitude carve-out — number-grounding does NOT extend to altitude, which stays
  neutral capture data; ranking/singling-out/tallying flights by altitude remains
  forbidden. The runtime detector `report_audience.py` is unchanged; new tests in
  `test_report_audience_guard.py::TestNarrativeQualityLevers` lock the levers and
  prove a report containing a 146.3 m AGL (480 ft) flight stays guard-clean. Full
  ADR-0029 / audience-leak suites pass unchanged (52 passed).
* **Caps unchanged** (ADR-0030). `.deployer-disabled` repo — hand-deploy on
  BOS-HQ; verify the public OpenAPI version (2.77.0), not `deployer-state.json`.

## 2026-07-03 — Advisory-lock the Alembic migration boot path (ADR-0036, Phase 1)

Migration-consolidation hardening, Phase 1 of
**docs/plans/2026-07-03-migration-consolidation.md**. Decision + rationale in
**docs/adr/0036-migration-single-path-hardening.md**.

* **Advisory lock on the migration run.** `run_migrations_sync()`
  (`backend/app/db_migrations.py`) now wraps its entire detect + stamp +
  upgrade critical section in a **session-level Postgres advisory lock**
  (`_MIGRATION_LOCK_ID = 8675310`) taken on a dedicated AUTOCOMMIT connection
  and released in a `finally`. Previously the migration path relied only on
  transaction atomicity + the `--workers 1` / single-replica assumptions — two
  backends booting concurrently (multi-worker, multi-replica, or a blue-green
  pair briefly pointing two backends at the same writable primary) could both
  enter `command.upgrade` and deadlock on a revision's DELETEs (0003) or
  double-apply DDL. The lock is **blocking** (`pg_advisory_lock`, not `try_`):
  a losing racer WAITS for the winner, then re-detects `current == head` and
  no-ops — it never skips the lock and proceeds against an un-migrated schema.
  Lock id is DISTINCT from `seed.py`'s `_SEED_LOCK_ID` (8675309) so migrating
  and seeding don't needlessly serialize against each other. Mirrors the
  posture the seed path already had. The ADR-0021 `pg_is_in_recovery()`
  primary-only guard is untouched.
* **Revision-id length invariant fence.** A hermetic test asserts every
  Alembic revision id is ≤ 32 chars (the `alembic_version.version_num`
  `VARCHAR(32)` that caused the v2.75.1 crash-loop when revision `0004` was
  41 chars, ran its DDL, then rolled back the stamp on every boot). CI now
  fails before such a revision can ship.
* **Tests.** `backend/tests/test_db_migrations.py` gains lock-envelope
  coverage: acquire-before-upgrade / release-after ordering, brownfield
  stamp+upgrade under lock, the no-op fast path still acquires+releases, the
  lock is released even when `command.upgrade` raises, and the lock id is
  distinct from the seed lock. Suite: 25 passed, 2 skipped (opt-in real-PG
  integration via `DOC_TEST_PG_URL`).
* **Scope.** Phase 1 only. `_add_missing_columns` / `_create_hot_indexes` and
  the legacy helpers in `main.py` are intentionally NOT removed here — the plan
  defers helper-severance (Phase 3) and the model-vs-head CI sync gate
  (Phase 2) to later, lower-urgency passes.
* **Deploy.** This repo is `.deployer-disabled` — the NOC deployer pulls git
  but does not rebuild. Ship via a hand-deploy on BOS-HQ
  (`docker compose build backend worker beat && up -d --no-deps …`); verify
  container build time, not `deployer-state.json`.

## 2026-07-03 — feat(missions): airspace / LAANC awareness at mission creation (ADR-0037)

Airspace/weather data was dashboard-only and not tied to mission creation.
Operators now get an **operator-facing pre-flight airspace check** at
scheduling time. Full design in **docs/adr/0037-airspace-laanc-awareness-at-mission-creation.md**.

* **New service `backend/app/services/airspace.py`.** `fetch_airspace_class()`
  point-in-polygon queries the FAA public Class Airspace ArcGIS FeatureServer
  (free, no key) — no intersecting polygon ⇒ uncontrolled Class G.
  `derive_laanc_requirement()` is tri-state: `True` for controlled B/C/D/
  E-surface, `False` for G, **`None` when undetermined** (never fabricate a
  safe-looking default from missing data). `assemble_preflight()` reuses the
  existing weather-router TFR/METAR/Open-Meteo fetchers and emits neutral
  advisories. `extract_latlon()` derives a coordinate from a mission's
  free-form `area_coordinates` (flat/aliases, `center`, GeoJSON Point/Polygon).
* **New endpoints (`backend/app/routers/missions.py`).**
  `GET /api/missions/airspace-preflight?lat=&lon=&airport=` (primary) and
  `GET /api/missions/{mission_id}/preflight`. Returns `{airspace_class,
  laanc_likely_required, controlling_facility, tfrs, weather, advisories,
  degraded, disclaimer}`. Static preflight route is declared before
  `/{mission_id}` so the path isn't captured as a mission id.
* **Computed on demand, never persisted.** TFRs/weather are time-varying; a
  create-time snapshot would be stale by flight day. No schema change, no
  migration → failover-safe. The `create_mission` write path is unchanged.
* **Graceful degradation.** All feeds gathered with `return_exceptions=True`;
  any feed failing (or raising) yields partial data + `degraded: true` +
  advisory — **never a 500**. Undetermined airspace ⇒ `laanc_likely_required:
  null`.
* **Operator-facing ONLY — never in the client report (ADR-0029 boundary).**
  Preflight is never persisted on the mission, never passed to any report
  builder, and renders no compliance verdict. Guarded by
  `tests/test_report_never_references_airspace.py` (fails if any report module
  or the mission schema references airspace/laanc/preflight/tfr) and by a unit
  test asserting no advisory contains "violation/illegal/non-compliant".
* **Tests.** +44 (`tests/services/test_airspace_service.py`,
  `tests/test_missions_airspace_preflight.py`,
  `tests/test_report_never_references_airspace.py`). Suite 507 → 551 passing,
  0 regressions.

## 2026-07-03 — fix(reports): resolve the aircraft from the live flight, not the stale junction copy (ADR-0038) — v2.78.0

Phase 1 of the flight-attach unification (plan:
`docs/plans/2026-07-03-flight-attach-unification.md`) — the **root fix** for the
ADR-0033 junction-staleness class.

* **The bug.** The `MissionFlight` junction copied `Flight.aircraft_id` at attach
  time. When a fleet serial was registered **later**, the live flight updated but
  the junction copy stayed stale — the Avata 2 mechanism. ADR-0033 made the
  report *tolerant* of a NULL copy (fell back to parsed `drone_model`), but never
  read the live fleet record, so a late-linked flight showed the bare string
  `"Avata2"` instead of the canonical `"DJI Avata 2"` card (name/image/specs).
* **Read convergence** (`backend/app/routers/reports.py`). `_load_live_flight_metrics`
  now LEFT-JOINs the fleet `Aircraft` (scalar columns only — the heavy Flight
  JSON is still never loaded, ADR-0025/0019). `_aircraft_label` and the new
  `_build_aircraft_cards` (extracted from the PDF path) resolve **native** flights
  from the live `Flight.aircraft`; **legacy-ODL** (`flight_id IS NULL`) rows keep
  the junction/cache read (Phase 2 materializes them). One resolver drives both
  the narrative label and the PDF "Aircraft used" card.
* **Write change** (`backend/app/routers/missions.py`). The single-add and bulk
  native attach paths no longer copy `aircraft_id` onto the junction (set NULL —
  derived on read; client-sent values still ignored, ADR-0007). The column is
  **retained** (drop is Phase 4).
* **Behaviour.** Preserving for reports **except** the fix: a native flight linked
  after attach now shows the correct fleet aircraft with no detach/re-attach.
* **Defers (per plan):** legacy-ODL materialization (Phase 2); metrics/track
  live-only flip + zero-cache-read counter (Phase 3); column drops + `flight_id`
  NOT NULL (Phase 4). ADR-0007 matcher and ADR-0029 audience guard untouched.
* **Tests.** New `backend/tests/test_report_live_aircraft_adr0038.py`: real-DB
  late-link root-fix proof (junction stays NULL, live resolves), legacy-ODL
  no-regression, unlinked-native `drone_model` fallback, stale-copy-ignored.
  Fail-before/pass-after confirmed (`"Avata2"` → `"DJI Avata 2"`). Full backend
  suite green (511 passed, 3 skipped). Existing attach-derives-aircraft tests
  updated to the new "junction not copied" contract.
* **Deploy.** `.deployer-disabled` — manual BOS-HQ rebuild
  (`docker compose build backend worker beat flight-parser && up -d --no-deps …`);
  verify the public `openapi.json` version (`2.78.0`), not `deployer-state.json`.

## 2026-07-03 — Avata 2 report incident: data remediation + prod deploy (ADR-0033)

Follows the code fix below. Full incident write-up + audit trail in
**docs/adr/0033-avata2-missing-from-report-incident.md**.

* **Data remediation (prod).** Root cause was a blank `serial_number` on the
  fleet `DJI Avata 2` record, so ADR-0007's strict serial-first matcher left
  every recent Avata flight unlinked. Registered serial `1581F6W8A242N0A3` and
  backfilled: aircraft 1 row, `flights` 12 rows (unlinked 12 → 0),
  `mission_flights` 4 rows. The "Springfield Drifters Promo" mission now resolves
  `DJI Avata 2 ×2 + DJI Mini 5 Pro ×1`; regenerating the report renders the Avata
  correctly. **Standing rule:** register a drone's serial when adding it to the
  fleet, or its flights stay unlinked under ADR-0007.
* **Deploy.** This repo is `.deployer-disabled` — the NOC deployer pulls git but
  does not rebuild. The reports fix + the ADR-0032 parser fix were hand-deployed
  on BOS-HQ (`docker compose build backend worker beat flight-parser && up -d
  --no-deps …`). Verify container build time, not `deployer-state.json`, to
  confirm a DOC deploy is actually running.

## 2026-07-03 — fix(reports): attached flight with unrecognized aircraft no longer missing from report

An attached flight whose fleet aircraft was unrecognized (`flights.aircraft_id`
NULL) was dropped from — or genericized to "Unknown" in — the client report.

* **Field defect.** The 2026-07-02 "Springfield Drifters Promo" mission had a DJI
  Avata 2 flight attached (native `flight_id`), but the generated report omitted
  it. Root cause: the Avata flight carried a `drone_serial` that the fleet
  "DJI Avata 2" aircraft record lacked (its `serial_number` is blank), so the
  strict serial-match path (ADR-0007) refused a model fallback and left
  `aircraft_id` NULL. `backend/app/routers/reports.py` then read ONLY
  `MissionFlight.aircraft` and substituted the literal "Unknown" — discarding the
  flight's own parsed `drone_model` ("Avata2"). The flight was attached the whole
  time; the report layer was not robust to an unlinked aircraft.
* **Fix (report layer, defense-in-depth).** New `_aircraft_label()` resolves the
  aircraft display name with a fallback chain: linked fleet `model_name` → live
  `Flight.drone_name`/`drone_model` → cache `drone_name`/`drone_model`/`aircraft`
  → "Unknown" only as a true last resort. `_build_flight_summaries` (the LLM
  aggregation) and the PDF "Aircraft used" section both use it, so an attached
  flight is never silently dropped from either surface. `_load_live_flight_metrics`
  now also selects `drone_model`/`drone_name` (scalar columns; heavy JSON still
  never loaded, ADR-0019). Regression test:
  `backend/tests/test_report_unrecognized_aircraft_label.py`.
* **No compliance logic touched** — the ADR-0029 altitude/Part-107 exceedance
  prohibition stays intact.
* **Operator residual (data, not code).** To restore canonical fleet attribution
  (and the aircraft image/specs card), add serial `1581F6W8A242N0A3` to the
  "DJI Avata 2" fleet aircraft record, then POST `/api/flights/backfill-aircraft`.
  Until then the report labels the flight "Avata2" from the parsed model.

## 2026-07-02 — fix(flight-parser): correct DJI voltage, Litchi/Airdata speed units, Airdata altitude selection

Three confirmed flight-log parser correctness bugs that put wrong numbers into
client-facing report data. All fixed with new per-format tests (the Litchi and
Airdata parsers previously had zero test coverage). Candidate for a new ADR
(parser unit-correctness; number TBD by the operator).

* **`flight-parser/src/dji.rs` — DJI battery voltage was 1000× too small.** The
  frame loop did `battery.voltage as f64 / 1000.0`, but `dji-log-parser` 0.5.7
  already returns `FrameBattery.voltage` in **volts** (its `SmartBattery` and
  `CenterBattery` record parsers map the raw `u16` with `/1000.0` — confirmed in
  the crate's `src/record/smart_battery.rs` and `src/record/center_battery.rs`).
  The extra divide turned a 15.2 V pack into 0.0152 V and disagreed with the
  Airdata parser (which stores volts raw). Extracted a tested
  `frame_battery_voltage()` normaliser that passes volts through unchanged.

* **`flight-parser/src/litchi.rs` — Litchi speed was stored without unit
  conversion.** Litchi CSVs export `speed(mph)` (some km/h); the value was
  summed into `max_speed` and every track speed with no conversion, inflating
  speed by ~2.237× (mph) / ~3.6× (km/h). Now detects the unit from the matched
  header and normalises to m/s, mirroring the Airdata parser. Also fixed the
  time-column selection: the old `contains("time")` could bind the numeric
  epoch-ms `timestamp` column instead of `datetime(utc)`, collapsing duration to
  the point-count fallback — now prefers an explicit `datetime` column and never
  binds `timestamp`.

* **`flight-parser/src/airdata.rs` — metric Airdata speed + altitude
  selection.** Added a km/h → m/s branch (metric exports were treated as m/s,
  ~3.6× inflated). Lowercased the dead `altitude_above_seaLevel(feet)` candidate
  (its capital `L` never matched a lowercased header) and demoted sea-level (MSL)
  below the AGL / relative-altitude candidates, adding an explicit
  `height_above_takeoff(m)` entry — so a metric export exposing both
  `height_above_takeoff(m)` and `altitude_above_sealevel(m)` now reports the AGL
  value for `max_altitude`, not the (much larger) MSL value.

No altitude/Part-107 exceedance flagging was added — these are unit-correctness
fixes only (ADR-0029: reports are client deliverables, not compliance audits).

Verification: `cargo test` in a `rust:1-slim` container — 20/20 pass (14
pre-existing + 6 new); clean `cargo build`, zero warnings.

## 2026-07-01 — fix(reports): remove disproven "unverified peak" ODL altitude caveat — v2.76.3

ODL-imported flights at the ~500 m DJI device ceiling were tagged in client
reports as `" — unverified (device-reported maximum, not a measured peak)"`. That
caveat was a defensive residue from ADR-0028 H1, never validated. It is **false**
and is removed. See **ADR-0031**.

**Ground-truth verification** (author bill-bg, 2026-07-01): device-ceiling
flights' max-altitude readings are self-consistent on a per-aircraft basis and
agree with post-flight telemetry reviews. The warning was a false overprotection.
Removed the caveat from the report narrative template; the underlying metric
(max altitude in feet/meters from the parsed flight log) stands.
