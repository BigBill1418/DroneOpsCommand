# DroneOpsCommand — In-Flight Work

Maintained alongside `CHANGELOG.md` and `docs/adr/`. `CHANGELOG.md` is
the ledger of shipped changes; this file tracks what's in-flight or
blocked.

> **Archive (2026-09-21).** Closed history older than **2026-08-01** now lives in
> `docs/archive/PROGRESS-2026-H1.md`, verbatim. Nothing was summarised away and
> nothing in-flight or blocked was moved — this file is the *open* surface, and
> the archive is the record. The authoritative open-items + operator to-do list
> as of today is `docs/reports/2026-09-21-open-items-inventory.md`.

## 2026-09-21 — Operator Cloudflare Access SSO — **BUILT, NOT DEPLOYED (worktree `~/wt-droneops-sso`, branch `feat/operator-sso`)**

**State: code + tests complete for Step A (verification, additive) and Step B (local-login
kill switch, off by default). No push, no merge, no deploy, no Cloudflare API calls per the
operator's explicit constraint for this task.** ADR-0047. Full detail, cutover runbook, and
rollback-per-step in `docs/adr/0047-operator-cloudflare-access-sso.md`.

**What's done:**

| Piece | State |
|---|---|
| RS256/JWKS Access-JWT verifier (`backend/app/auth/cf_access.py`) | Done, 33 tests |
| `cf_access_identities` mapping table + migration `0012_cf_access_ident` | Done, 6 real-Postgres tests |
| `get_current_user` wired additively | Done, 7 tests |
| `LOCAL_LOGIN_DISABLED` kill switch | Done, off by default, 9 tests |
| Login/Setup screen modernized for SSO + email fix + CallSignLane-style footer | Done, 14 tests |
| Three commits: Step A (2.93.0), frontend Part 2 (2.93.1), Step B (2.94.0) | Done, `test_app_version_parity` green at each |

**Operator action required before any of this takes effect:** set
`CF_ACCESS_TEAM_DOMAIN`/`CF_ACCESS_AUD` on BOS-HQ (Step A), soak, then set
`LOCAL_LOGIN_DISABLED=true` (Step B) — see the ADR's cutover runbook. Until then this is a
complete no-op for the live app, and a complete no-op forever for self-hosted/OSS installs
and the public demo instance (neither has Cloudflare Access; both env vars stay unset by
design).

## 2026-09-21 — Basemap migration off CARTO + tile-health probe — **LIVE IN PRODUCTION**

**State: MERGED, PUSHED AND DEPLOYED.** `72dd1a9` went to `main`; the fleet
deployer built and recreated the stack and **v2.92.0 was live on BOS-HQ at
14:52 PDT**. ADR-0046. (Superseded state: "SHIPPED, PENDING DEPLOY / NOT
pushed".)

**Post-deploy verification, read off the running system:**

| Check | Observed |
|---|---|
| `openapi.json` → `info.version` | **2.92.0** |
| flight-parser `GET /health` → `version` | 1.2.0 (unchanged — no Rust change) |
| served leaflet chunk | contains the Esri endpoints; **zero `cartocdn`** across all 44 chunks |
| probe run inside `droneops-worker-1` | `ok: true`, `layers_ok: 5` |
| probe ntfy | **OFF** by design (MP-2, earliest 2026-10-05) |
| first *scheduled* probe run | 2026-09-22 15:47 UTC |

**Demo stack updated by hand the same day (14:57 PDT).** `~/droneops-demo` on
BOS-HQ is **not** deployer-managed (only prod is), so it was pulled
`--ff-only` to `d153623` and rebuilt: `compose up -d --build --no-deps frontend
backend flight-parser`. It went **v2.80.4 → v2.92.0 (14:57 PDT), then → v2.92.1 (15:23 PDT)**. `cloudflared`, `db` and
`redis` were deliberately left running (4-week uptime intact); the demo
**worker and beat stay stopped on purpose** — a running demo beat is the
dunning-email hazard recorded in ADR-0042. Verified: demo backend reports
2.92.0, all three rebuilt containers healthy, served bundle carries the Esri
endpoints and zero `cartocdn`. The **CHAD-HQ demo clone is a different clone**,
still on `dfad0a3`, and remains open.

Every map's default Dark layer had been serving CARTO tiles watermarked
"API KEY REQUIRED" since ~2026-08-28 — HTTP 200 with correct headers, so
nothing in the stack could see it and it stood for 24 days. Replaced with
keyless Esri + OSM behind a single registry, and added the pixel-level probe
that is the only class of control that can catch that failure.

Evidence quoted in the commit body. Verification done this session:

- All five registry tile URLs fetched live: 200, correct bytes, `ACAO: *`.
- Dark base + transportation composited at z12/z13 and inspected; the
  `World_Dark_Gray_Reference` layer was tried as a third layer and **rejected**
  on the evidence (its city labels are overprinted unreadable by the
  transportation layer, and it is blank from z16).
- Report renderer's User-Agent confirmed **on the wire** against a local HTTP
  server — 4 tile requests, all carrying `DroneOpsCommand/2.92.0 (...)`, no
  `StaticMap` default.
- Probe detection validated against the real defect class, not asserted: a
  watermark stamped into each of the five live tiles moves the hashes 10-35
  bits against a threshold of 8; the same clean tile against its own baseline
  reads `ok`.
- Both frontend and backend guard tests falsified by mutation before being
  trusted.

**Open for Bill / next session:**

1. **ROADMAP MP-2 — arm the probe's ntfy, earliest 2026-10-05.** It ships
   observe-only on purpose; the thresholds are starting points, not
   measurements. Two weeks of weekly runs first (first run Monday 2026-09-22),
   then `PUT /api/admin/basemap/tile-health/ntfy {"enabled": true}`.
2. **Prove the probe once after deploy:**
   `POST /api/admin/basemap/tile-health/run` (admin-authed) should return
   `ok: true, layers_ok: 5`. A 429 just means it ran in the last 60s.
3. **`backend/app/version.py` is a SEVENTH version location.** CLAUDE.md's bump
   list still says 5 files / 6 locations and was deliberately not edited by this
   session. A missed bump there is caught by
   `tests/test_app_version_parity.py` (red, not silent), and it only affects the
   outbound User-Agent — but the bump list should be updated.
4. **The Esri keyless-terms risk is accepted, not resolved** (ADR-0046
   Consequences). If Esri gates those endpoints, the exits are Stadia at
   $20/month or Protomaps on R2 (MP-1), and the registry makes either a
   one-line change.

## 2026-09-21 — Phase 7 customer-surface hardening — **MERGED AND LIVE**

**State: MERGED AND DEPLOYED.** Bill merged
`security/phase7-customer-hardening` at **`d30eb5b` (13:53 PDT)**; the fleet
deployer built and recreated the stack and **v2.91.0 was live on BOS-HQ at
13:58 PDT**. A same-day correction (`a226c93`, ADR-0045 correction — the
trusted-proxy fix trusted only one hop) is in the same merged range.
(Superseded state: "AWAITING OPERATOR MERGE / NOT merged to `main`, NOT
deployed".) Full detail: `docs/adr/0045-phase7-customer-surface-hardening.md`
+ `CHANGELOG.md` 2026-09-21 entries.

Remaining items for Bill:
1. ~~**Review + merge this branch**~~ — **DONE 2026-09-21 13:53 PDT** (`d30eb5b`),
   deployed 13:58 PDT. (Suite was 784 passed / 17 skipped at branch time, 795
   passed / 17 skipped after the one-hop correction.)
2. **CS-Public item is a PATCH, not a commit** —
   `docs/patches/0075-cspublic-*` in this repo. Needs a worktree created in
   `~/repos/CallSignPublic` (per dispatch instruction, not created by this
   session) before it can land. **Read `0075-cspublic-README.md`'s
   rollout-order section before merging** — deploying it without first
   setting `search.worker_origin_token` (origin) and
   `ORIGIN_SEARCH_SECRET` (Worker secret) 503s ALL archive search,
   including the legitimate path.
3. **Open, deliberately unactioned recommendation:** a CAPTCHA on
   `/api/intake/form/{token}` would need a new Cloudflare Turnstile site
   key (operator/dashboard action) — not added this pass; the 256-bit
   intake token already makes brute-force guessing infeasible, so this is
   lower-priority than it reads in the original audit line item. See
   ADR-0045 Consequences.
4. **P7-6 — the post-deploy client-IP check is still OPEN, and only Bill can
   do it.** `droneops.barnardhq.com` sits behind Cloudflare Access, so any
   agent-side request is answered with a 302 to the Access login page and never
   reaches the app. Only a request from Bill's own authenticated browser
   session produces a log line carrying a real external client IP. One-liner:
   open the app in a browser, then
   `ssh 10.99.0.4 'docker logs --tail 50 droneops-frontend-1'` and confirm the
   resolved-client field is his own public IP, **not** the constant
   `172.19.0.11`. Source: ADR-0045 §"Verification re-run after the fix".

## 2026-09-11 — FP-1 **P-EVAL complete**: no crate bump exists; P2 is unblocked on `0.5.7`

**State: EVIDENCE DELIVERED, awaiting Bill's call on §8's follow-ups.** Nothing
was adopted and nothing was deployed. Report:
`docs/reports/2026-09-11-dji-log-parser-upgrade-eval.md`. Harness: `tools/p-eval/`.

ADR-0043's **D6** asked for a before/after diff before the crate moves. The
finding is that **the newest published `dji-log-parser` is `0.5.7` — already the
pinned version.** Two independent sources agree (crates.io API; `cargo search`
inside `rust:1.85-bookworm`). Upstream last released 2025-04-26 and is one commit
ahead of that tag, dated 2025-06-07.

That one commit ("Parse Inspire 1 battery serial numbers", `88fcfc96`) was
evaluated as the candidate because it is the only candidate that exists.

| Measurement | Result |
|---|---|
| Comparisons run | **782** (198 live originals + 584 recovered; 20 overlap) |
| Distinct real DJI logs | **762**, all log **v14** |
| Metrics diffed per log | **24**, floats compared **bitwise** (`to_bits`), not by tolerance |
| `gps_track` coordinates compared | **6,756,743** across the **763** records with a non-empty track (full-precision ordered SHA-256 digest). 19 records decoded frames but never got a GPS fix, so their track comparison is vacuous; the header quantities they fall back to are compared directly as 4 of the 24 metrics. |
| Frames decoded | **6,797,600**; `frames_decoded` true for 782/782 |
| DJI keychains fetched / failed | **759 / 0** (23 cache hits = 3 pilot + the 20 overlaps) |
| Parse errors | **0** |
| **Logs with ANY difference** | **0** |
| Runtime | 1202 s on 5 cores, off-prod (logs copied to the workspace; BOS untouched) |

**The zero is falsified, not assumed.** `p-eval --selftest` drives the candidate's
only changed function to a known-different answer (`Inspire1`: `"0987654321"` →
`"1234567890"`), proves the change is reachable *only* via the three Inspire-1
product types, and proves the comparator reports a one-ULP float difference.
`selftest failures: 0`. A corpus with no Inspire 1 is therefore *predicted* to
show zero differences.

**Second control — the harness against production's own rows**, partitioned by
when each row was written:

| Row era | duration_secs | total_distance |
|---|---:|---:|
| Written by today's parser (73 rows) | **73 / 73** | **73 / 73** |
| Legacy, pre-ADR-0027/0028 (125 rows) | 62 / 125 | 123 / 125 |

`max_altitude`, `max_speed`, `point_count`, `frame_count`, `product_type`:
**198 / 198** each. Every disagreement is a legacy row with a named cause —
ADR-0027 (duration source) for 63, ADR-0028's C1 outlier gate for 2 (the harness
drops one teleport segment those rows predate), ADR-0044's matcher rewriting
`drone_model` to the canonical fleet name for 129.

**Quoted test output (no CI job runs these):**

```
tools/p-eval  cargo test → test result: ok. 13 passed; 0 failed; 0 ignored
tools/p-eval  --selftest → selftest failures: 0
flight-parser cargo test → test result: ok. 65 passed; 0 failed; 0 ignored
```

### What P2 inherits

- **Build the §2.4 `SmartBatteryStatic` shim** — upstream has not fixed it
  (`record/smart_battery_group.rs` is identical between pin and candidate). The
  plan's `raw >> 8` is the right transform; the new constraint is that it
  recovers the true value only while the top byte is zero, so **`loop_times`
  silently wraps at 256 cycles** and the `0..=3000` plausibility gate cannot
  catch it. Confirm in P2-a against a pack with a three-digit cycle count.
- **Keep the `Unknown(NNN) → aircraft_name` fallback in full.** Four placeholders
  live, not three: `Unknown(150)` (Matrice 4T) exists on 39 recovered logs and no
  native row yet, so P7's dry-run should expect four values from P4(b)'s
  `^Unknown\(\d+\)$` predicate.
- **Peak RSS is still unmeasured** against the parser's 256 MB `mem_limit`. The
  harness is not a proxy — it holds two frame vectors and two track copies, so its
  footprint is structurally larger. Still P2-a's gate.

### Open for Bill

1. **Pin the crate exactly** (`= "0.5.7"`). `Cargo.toml` currently requests
   `"0.5"`, so a `cargo update` could move it and bypass D6 without a decision.
   Not done here — it is a change to the manifest D6 governs.
2. **Guard `DJI_LOG_PARSER_VERSION`** against `Cargo.lock`. It is stamped on every
   `flight_details` row as the provenance D6's "re-backfill below version X" query
   depends on, and nothing keeps it honest.
3. **153 of 226 `dji_txt` rows are legacy** and carry pre-ADR-0027/0028 duration
   and distance. D5 freezes those fields, so they stay stale unless something
   reprocesses. The error is small — 6 rows off by more than 1 s, +35.7 s total
   across the 125 measured — and leaving it is defensible. It should be a
   decision, not an oversight.
4. **Operational note:** the DJI decode key is **not** in the parser's
   environment anywhere (prod and demo both have `DJI_API_KEY=""`, so
   `GET /health` → `dji_key_configured: false` is correct by design, not a
   defect). Production reads it from `system_settings.dji_api_key` and passes it
   per-request as `X-DJI-Api-Key`. Worth knowing before anyone "fixes" that
   health field.

## 2026-09-05 — Fleet-attribution matcher: canonical DJI serials (ADR-0044) — **LIVE IN PRODUCTION**

**State: MERGED AND LIVE.** Operator gave the go on 2026-09-05.
`feat/serial-prefix-matcher` was fast-forwarded onto `main`
(`34553cf` → **`dfc7054`**) and pushed; the fleet deployer built and
recreated the stack. Version **2.90.0**, deliberately clear of the
2.84.0–2.89.0 band FP-1 reserves for P2–P7.

**Post-deploy verification, read off the running system (not a log line):**

| Check | Observed |
|---|---|
| `openapi.json` → `info.version` | **2.90.0** |
| flight-parser `GET /health` → `version` | 1.2.0 (unchanged) |
| alembic head | `0011_battery_src_truth` (unchanged — this change adds no migration) |
| flights with `aircraft_id IS NULL` and a serial | **0** (was 88) |
| attributed to `DJI Matrice 4TD` (`1581F8HGX255P00A`) | **50** |
| attributed to `DJI Matrice 4T` (`1581F7K3C25AA00D`) | **39** |
| aircraft rows / duplicate serials | 11 / 0 |
| backend ERROR or CRITICAL since startup | **0** |

Backend startup logged `STARTUP: Aircraft backfill — 88/88 unlinked matched`.
The 4TD total reads 50 rather than the 49 predicted because one of its ODL
rows was already attributed before this change; 49 newly linked + 39 = the 88
the log reports, so the counts reconcile exactly.

- The defect and the rule are recorded in
  `docs/adr/0044-serial-prefix-matcher-odl-canonical-serials.md`.
- Verified against the production DB on BOS-HQ before and after writing
  the rule: exactly 88 `aircraft_id IS NULL` flights, all
  `opendronelog_import`, all 20-char serials
  (`1581F8HGX255P00A0FEK` ×49, `1581F7K3C25AA00DMZMG` ×39).
- **This deployed as a bulk write, as designed.** The startup backfill runs
  on every container restart against `aircraft_id IS NULL`; it attributed all
  88 rows on the first restart after the merge. Note the corollary recorded in
  ADR-0044: the backfill will not re-evaluate a row once `aircraft_id` is set,
  so correcting any one of those 88 is now a manual detach through the UI.
- Depends on the aircraft rows Bill's earlier data work created
  (`DJI Matrice 4T` / `1581F7K3C25AA00D`, `DJI FPV` / `37Q7LA800BX0PN`);
  this change is code-only and touched no DB rows.
- **Coordination:** FP-1 P0+P1 merged first; this landed on top of it. The diff is confined to the matcher (`flight_library.py`),
  its test file, ADR-0044, docs and the version markers, and it merges
  cleanly — FP-1's `flight_library.py` edits are in the ingest, status and
  telemetry regions, not in `_match_fleet_aircraft`. ADR **0044** is free
  on `34553cf` (FP-1 stopped at 0043) and is claimed here.
- Interacts with FP-1: the planned ODL-era re-import (ADR-0043) lands
  20-char serials, which this rule is what makes attributable.

**Test evidence at the rebased HEAD — there is no pytest or cargo job in CI,
so this is local and quoted, not inferred from an exit code.** Run in
`python:3.13.5-slim-bookworm` with the backend Dockerfile's native libs and
`requirements.txt` + `requirements-dev.txt`, hermetic (no live Postgres, so
the migration integration tier skips), `OTEL_EXPORTER_OTLP_ENDPOINT=""`:

```
$ python -m pytest -q
753 passed, 17 skipped in 268.46s (0:04:28)

$ python -m pytest tests/test_flight_attribution.py -q
22 passed in 3.87s
```

Baseline on `origin/main` `34553cf`, same command and image: `743 passed,
17 skipped`. The delta is exactly the 10 cases this branch adds. The
`flight-parser` crate is untouched here and its suite is unchanged
(`rust:1.85-bookworm`, matching `flight-parser/Dockerfile`):

```
$ cargo test
test result: ok. 65 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out
```

**Repo defect found while doing this, fixed in its own commit on this
branch:** `aiosqlite` was declared in neither `backend/requirements.txt` nor
`backend/requirements-dev.txt`, yet seven test modules build engines on
`sqlite+aiosqlite://`. It is missing on `main` too — pre-existing, not
something this branch or FP-1 introduced. A clean-room install loses those
modules at *setup*, so pytest reports ERROR rather than FAIL: measured at
this commit, `724 passed, 17 skipped, 29 errors` without it vs `753 passed,
17 skipped` with it. Now pinned `aiosqlite==0.20.0` in
`requirements-dev.txt`. Kept as a separate commit so it can be reverted
independently of the matcher change.

## 2026-09-05 — FP-1 Flight Details — P0 + P1 **LIVE IN PRODUCTION**

Operator gave the go on 2026-09-05. `feat/fp1-flight-details` was
fast-forwarded onto `main` (`9f502e0` → **`34553cf`**) and pushed at
**02:35 PDT**. A push to `main` IS a production deploy on this repo
(ADR-0018, NOC fleet deployer), so that push deployed BOS-HQ.

**Deploy verified live — see "Production verification" below for the
container-content evidence.** Both phases remain behaviourally inert as
designed: the new tables exist and are empty, and details are only written
by imports that happen from now on.

### Production verification (2026-09-05, ~02:35–02:40 PDT)

Verified by **container content and live DB**, never by a deployer log line
(the silent-stale-deploy class). Every value below was read off the running
system on BOS-HQ.

**Deployer handling of the push** — it did NOT go pull-only. The two HEAD-most
commits carry `[skip-deploy]`, but the gate is `allCommitsSkipDeploy`, and the
two code commits beneath them do not carry it, so the range deployed:

```
"from":"9f502e0a","to":"34553cf3","msg":"Changes detected — starting deploy"
"msg":"Building all services on remote..."
"changed":["backend","frontend","worker"],"msg":"Image-digest gate passed (ADR-0056)"
"msg":"Recreating changed services on remote (up -d, no down — ADR-0066)..."
"msg":"Smoke test PASSED"
"status":"success","duration":"267368ms","services_actually_rebuilt":["backend","frontend","worker"]
```

**Live artifacts, as actually served:**

| Check | Expected | Observed |
|---|---|---|
| `droneops.barnardhq.com/openapi.json` → `info.version` | 2.83.0 | **2.83.0** |
| flight-parser `GET :8100/health` → `version` | 1.2.0 | **1.2.0** |
| alembic head (`droneops-standby-db`) | `0011_battery_src_truth` | **`0011_battery_src_truth`** |
| `flight_details` table | exists | **exists, 77 columns, 0 rows** |
| `flight_series` table | exists | **exists, 7 columns, 0 rows** |
| `0011` battery columns | 3 added | **`batteries.cycle_count_observed`, `batteries.metrics_source`, `battery_logs.pack_cycle_count`** |
| read-path routes in live OpenAPI | 3 | **`/api/flight-library/{flight_id}/details`, `/details/series`, `/api/flight-library/details/status`** |

Container recreation confirmed by timestamp, not by log: `backend`,
`frontend`, `worker`, `beat` and `flight-parser` all recreated 02:38:02–02:38:03
PDT from images built 02:37:03–02:37:09 PDT.

**Migrations applied cleanly**, from the backend's own startup log — a single
forward run under the ADR-0035 advisory lock, no errors:

```
MIGRATIONS: upgrading schema from 0009_mission_dl_email_sent_at to head 0011_battery_src_truth (ADR-0022).
Running upgrade 0009_mission_dl_email_sent_at -> 0010_flight_details, ADR-0043 — flight_details + flight_series sidecar tables
Running upgrade 0010_flight_details -> 0011_battery_src_truth, ADR-0043 D4 — battery source-of-truth columns (landed early, inert)
MIGRATIONS: upgraded complete (head=0011_battery_src_truth)
```

**Prod data invariants held across the deploy** (pre-swap → post-swap):
`aircraft` 11 → **11** rows, duplicate serials 0 → **0**, and flights with
`aircraft_id IS NULL` **88 → 88, unchanged**. The startup aircraft-backfill ran
and deliberately left all 88 unattributed, logging INFO (not error) for exactly
two serials that account for the whole set:

```
39  serial=1581F7K3C25AA00DMZMG
49  serial=1581F8HGX255P00A0FEK
```

Both are the log-side **superset** form of a fleet serial (e.g. fleet
`1581F7K3C25AA00D` vs log `…00DMZMG`), which is precisely the mismatch the
matcher change addresses. **Superseded later the same day:** ADR-0044 shipped
in `dfc7054` and the startup backfill attributed all 88 — see the matcher
section above. The 88 recorded here is the state of *this* deploy
(`34553cf`), not the current state, which is 0. `grep -ci 'error|exception|traceback|critical'`
over the backend log since startup: **0**.

**One residual to fix separately — `flight-parser` is absent from the
deployer's `build_map`** for this repo (`noc-master/data/config.yml` maps only
`backend`, `frontend`, `worker`). The ADR-0056 digest gate and the
`services_actually_rebuilt` field are both computed *over `build_map`*, so the
parser is structurally invisible to them — which is why a deploy that
demonstrably rebuilt and recreated the parser still reports
`services_actually_rebuilt: ["backend","frontend","worker"]`.

The parser went live anyway because both surrounding steps are unscoped: with
no `external_build_cmd` configured the build is a bare `docker compose build`
(all services), and the recreate is a bare `up -d --remove-orphans`, which
recreates anything whose image ID changed. So the outcome was correct, but it
was correct *incidentally* — the gate that exists to catch a silent stale
parser cannot see the parser. Adding a `flight-parser` entry to `build_map`
would close that. **Not tested:** whether a parser-ONLY change (no
`backend/`/`frontend/` diff) still deploys; that path was not exercised here.

### P0 — schema + read path (v2.82.0) — LIVE, inert

Migrations `0010_flight_details` (both tables) and `0011_battery_src_truth`
(three nullable battery columns, landed a phase early so the battery
source-of-truth phase needs no migration). Models, `Flight.details` /
`Flight.series` at `lazy="noload"`, schemas, `/details`, `/details/series`,
`/details/status`, the extracted `telemetry_downsample` service, and the §4.4
report-audience guard. **Nothing writes to the new tables yet; no existing
response changes.**

**Test evidence — there is no pytest or cargo job in CI, so this is local and
quoted, not inferred from an exit code.**

Hermetic suite (`cd backend && pytest -q`):

```
723 passed, 7 skipped in 75.24s (0:01:15)
```

Full suite with a live Postgres 16, which un-skips the migration integration
tier (`docker run -d --name doc-mig-test -e POSTGRES_USER=doc -e
POSTGRES_PASSWORD=test -e POSTGRES_DB=doc -p 55432:5432 postgres:16-alpine`;
then `DOC_TEST_PG_URL=postgresql+asyncpg://doc:test@127.0.0.1:55432/doc
DATABASE_URL=$DOC_TEST_PG_URL pytest -q`):

```
729 passed, 1 skipped in 89.70s (0:01:29)
```

Baseline before this work, same command, same container, from a clean
`origin/main` export: `618 passed, 1 skipped`. So P0 adds 111 passing tests
and breaks nothing.

**A gap in the pre-existing migration tests, closed.** `test_db_migrations.py`
builds every test database with `create_all` from the LIVE models first. Since
the models now declare the new tables, that tier only ever exercises 0010/0011's
*idempotency guards* — `op.create_table` and `op.add_column` are never reached,
so a typo in either DDL body would have sat green through the whole suite and
first appeared as a BOS-HQ crash loop. `tests/test_migration_0010_0011_upgrade_path.py`
reproduces the actual production shape instead (full legacy schema, new objects
dropped, stamped at 0009) and upgrades, covering the CREATE branch, the FK
`ON DELETE CASCADE`, "primary key only, no secondary indexes", a second run
being a no-op, and an empty autogenerate diff against `Base.metadata`.

**One self-inflicted defect found and fixed during that work,** worth recording
because it is the ADR-0042 hazard biting from a new direction: the first draft
of that test stamped 0009 with `alembic.command.stamp(_alembic_config(), ...)`.
A bare `Config` has no `connection` attribute, so `alembic/env.py` takes its CLI
branch and runs `fileConfig()`, which defaults to `disable_existing_loggers=True`
and killed every `doc.*` logger for the rest of the pytest process — making
three unrelated log-assertion tests fail depending on file ordering. They passed
in isolation and failed only in the full run. Confirmed mine rather than
pre-existing by running the same command against a clean `origin/main` export
(618 passed, 0 failures). Fixed by writing the `alembic_version` row with SQL.
**Anything that calls Alembic programmatically outside `run_migrations_sync`
needs the same care.**

**§1.5 / C-2 encoding measurement — DECIDED by measurement, `json` stands.**
Run against real `postgres:16-alpine`, synthesising the census's largest flight
(M4TD, 13,870 frames) with a realistic climb/cruise/descent profile and §2.5
per-quantity rounding, each series written three ways:

| series | n | dp | raw json | `json` | `jsonb` | `float8[]` |
|---|---:|---:|---:|---:|---:|---:|
| altitude_msl_m | 13,870 | 1 | 81,187 | **23,490** | 32,656 | 24,937 |
| t_offset_s | 13,870 | 2 | 90,817 | **40,894** | 47,724 | 45,410 |
| battery_current_a | 13,870 | 2 | 77,134 | **32,707** | 42,650 | 36,415 |
| pilot_lat | 657 | 7 | 7,165 | **1,921** | 2,346 | 3,134 |
| **total** | | | 256,303 | **99,012** | 125,376 | 109,896 |

Read + parse of 13,870 samples to a Python list, best of 60:
`json` **4.34 ms**, `jsonb` 7.28 ms, `float8[]` 9.43 ms.

Conclusions, including two that correct the plan:
- `json` wins on **both** axes — 11 % smaller than `float8[]` and ~2.2x faster
  to read. The plan's §1.5 speculation that a native float array "needs no JSON
  parse in Python and maps straight to a list" and might therefore be faster is
  **wrong as measured**: psycopg2's array parser is slower than the C
  `json.loads`. C-2 is closed; `values` stays `json`.
- Compression is **2.59:1**, not the plan's assumed 3–5x. So the largest
  flight is ~500 KB compressed rather than 260–430 KB, and 210 flights land
  nearer **35–70 MB** than the plan's 25–60 MB. Same order, still comfortable,
  but the estimate should not be quoted at the optimistic end.
- Caveat on the caveat: this is synthetic data with gaussian noise, which is
  roughly a worst case for compressing digit runs. Real sensor data at 1 dp
  with slow drift should do slightly better. The measurement is a floor.
- Unexpected: `t_offset_s` is the **largest** series, bigger than altitude —
  monotonically increasing 2-dp values have high digit entropy. If storage ever
  needs trimming, storing a start + cadence instead of a full time base is the
  cheapest win available. Not done; out of scope.

Script kept at `/tmp/claude-1000/.../enc_measure.py` for the session only —
it writes nothing outside a scratch table on a throwaway container, and touched
no production database.

### P1 — Tier 0 parser pass (app v2.83.0, parser v1.2.0) — LIVE

All of §2.2 in the existing frame loop, §2.5 per-quantity rounding, full-
resolution series, `details: None` in `litchi.rs` / `airdata.rs`, the
`Unknown(NNN)` → `aircraft_name` fallback, and backend persistence inside the
existing best-effort savepoint. **No second decode and no second DJI keychain
round-trip** — the accumulator rides the loop that was already there.

Produced per DJI flight: ~50 typed scalars, five JSONB groups, and **15**
full-resolution frame series (`t_offset_s`, MSL altitude, VPS height,
distance-from-home, z-speed, RC down/uplink, gimbal P/R/Y, aircraft P/R/Y,
battery current, cell-voltage deviation).

**Test evidence — no cargo or pytest job exists in CI, so this is local and
quoted.**

`cd flight-parser && cargo test` (run in a `rust:1-bookworm` container to
escape the workspace's ~2.5 GB cgroup cap):

```
running 65 tests
...
test result: ok. 65 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.11s
```

Baseline on `origin/main` was 20 tests, so P1 adds 45.

`cd backend && pytest -q`:

```
743 passed, 17 skipped in 113.29s (0:01:53)
```

With the live Postgres 16 (un-skips both DB tiers):

```
759 passed, 1 skipped in 194.70s (0:03:14)
```

Cumulative against the `origin/main` baseline of `618 passed, 1 skipped`:
**+141 passing backend tests, +45 Rust tests.**

**The cross-language wire fixture, and why it exists.** The payload crosses a
JSON boundary between two languages, whose silent failure mode is: rename a
field on one side, every column writes NULL, and the import still logs
"Flight details stored". Permanent, invisible, and it looks exactly like
success. So `backend/tests/fixtures/parser_details_payload.json` is
**generated by the Rust suite** and asserted against by both sides — Rust
checks the checked-in copy still matches what it emits; pytest checks every key
in it maps to a real column and that a real row comes back populated.

Regenerate with:
`cd flight-parser && DETAILS_FIXTURE_OUT=../backend/tests/fixtures/parser_details_payload.json cargo test emit_wire_fixture`

Verified by falsification rather than assumed: deliberately drifting the
fixture (`photo_count` → `photoCount`) turned **three** tests red, including
`assert None == 2` on the stored row — the exact silent-NULL symptom. Fixture
restored afterwards.

**Two defects found in my own implementation during P1, both by a test:**

1. `video_seconds` attributed the interval `[prev_frame, this_frame]` to the
   *current* frame's recording flag, while the phase histogram (correctly)
   attributes it to the state that actually held during it. That shifts every
   interval by one sample and silently drops the last stretch of a recording —
   3 s reported for a 6 s clip. Both now use the same convention.
2. `waypoint_mode_seconds` serialised as `-0.0`. Rust's `Sum` impl for floats
   folds from `-0.0`, so a mode that never occurred sums to negative zero:
   numerically fine, but it reads as a defect on screen and churns every JSON
   diff. `round_dp` now normalises it, with a regression test that asserts the
   std-library premise it depends on.

**Decisions taken during P1 that the plan did not specify:**

- **Missing samples are JSON `null`, not `0.0`.** Series values are
  `Option<f64>`. An RC link with no OFDM record yet, or a distance-from-home
  with no GPS fix, stores a gap — a `0` there would read as "signal lost" or
  "at the home point", which are different and alarming claims about the
  flight. Costs ~4 bytes per genuinely-absent sample.
- **Time-base provenance is recorded.** `FrameCustom::default()` is the Unix
  epoch, so a log with no `Custom` records would otherwise stamp 1970 on every
  sample. The base is chosen explicitly (wall clock → `fly_time` → none) and
  written to `config.time_base`; with no base, `t_offset_s` is omitted rather
  than filled with zeros, and `first/last_frame_at` and `frame_hz_est` stay
  NULL.
- **Takeoff/landing come from `osd.is_on_ground` edges**, a direct physical
  signal, rather than from inferring intent from mode names.
- **The crate injects its own `"Flight mode changed to X."` into `app.tip`.**
  We emit structured `kind: "mode"` events from `flyc_state`, so the textual
  duplicate is dropped — otherwise every transition appears twice.
- **`battery_energy_wh` / `battery_discharge_mah` integrate over the real
  inter-frame interval**, making them invariant to log rate. A
  frame-count-times-assumed-cadence integration is the ADR-0027 mistake in a
  new place; there is a test that logs the same flight at 1 Hz and 5 Hz and
  requires the same answer.

**Response-size impact, checked rather than assumed:** every call site posts
exactly ONE file to `/parse` (`files={"file": ...}` in `_SpooledUpload.parse`
and in the Celery device-upload task), so the ~1.3 MB details payload is
per-request and is not multiplied by a batch upload.

**NOT measured:** peak parser RSS on a real 13,870-frame log. Arithmetic bounds
it at roughly 3–4 MB of series buffers plus the serialised JSON, well inside
the container's `mem_limit: 256m`, but there is no local DJI original to run it
against and I have not exercised the real path end to end. The plan's R-2 memory
gate belongs to the Tier 1 phase and should cover this too.

**Also not done in this run** (correctly out of scope): the Tier 1 record pass,
the crate before/after evaluation, backfill, repair, the UI, the battery
source-of-truth switch, and the ODL re-import. The crate evaluation runs
**before** the Tier 1 phase by design — if a newer crate fixes
`SmartBatteryStatic` and `ProductType`, part of that phase shrinks or vanishes.

### Corrections to the plan from live prod data (2026-09-05)

Found by a parallel session verifying the matcher contract against the BOS-HQ
prod DB. **Note the prod DB is `droneops-standby-db` (db `droneops`), the
promoted standby — NOT `droneops-db-1`, which is an `alpine:3` placeholder.**

1. **DJI serials exist in two forms and the plan conflates them.** Every
   OpenDroneLog-era `drone_serial` is the 16-char header serial plus a 4-char
   suffix — e.g. `1581F8HGX255P00A` + `0FEK` (Matrice 4TD),
   `1581F5BK7241J00B` + `A040` (M30T). The 14-char FPV serials carry no suffix
   and are identical in both forms. **§7.1 as written matches zero of the 584
   files**, because it requires the parsed 16-char header serial to be *equal*
   to the stored 20-char row value. §7.1 now states the serial check is a
   **16-char-prefix comparison**, not equality. The plan file is corrected on
   this branch.
2. **The "88 unattributed ODL rows" have a different cause than the plan
   states.** Verified: 49 Matrice 4TD + 39 Matrice 4T. The 4TD's aircraft row
   has existed since 2026-03-16 with serial `1581F8HGX255P00A`; those 49 are
   unattributed purely because of the 16-vs-20-char mismatch, not a missing
   row. The plan's claim that the two missing aircraft rows account for 46
   unattributed flights is wrong.
3. **Relevant to P1:** the parser stamps `drone_serial` from the DJI log
   header (`details.aircraft_sn`, 16 bytes for log version > 5), so newly
   imported flights carry the **16-char** form. `_match_fleet_aircraft`
   (`flight_library.py` branch 1) does `func.upper(Aircraft.serial_number) ==
   drone_serial.upper()` via `scalar_one_or_none()` — **exact equality, no
   prefix logic.** P0/P1 deliberately do NOT change the matcher and build
   nothing that assumes the two forms interoperate. The 20-char form is where
   `flight_details.aircraft_sn_full` will land in the Tier-1 phase, which is
   the natural place to reconcile them later.

### Production data changes applied 2026-09-05 (by the parallel session, not by this branch)

All on the BOS-HQ prod DB, verified by reading the rows back:

1. **Created** aircraft `DJI Matrice 4T`, serial `1581F7K3C25AA00D` (16-char
   header form), `image_filename` NULL — **there is no `dji_m4t_official.png`
   in `/app/app/static/aircraft/`; one is needed before the fleet tile renders
   properly.**
2. **Created** aircraft `DJI FPV`, serial `37Q7LA800BX0PN`, image
   `dji_fpv_official.png`.
3. **Renamed** the pre-existing row with serial `37QBJ5WBD100DN` from
   `DJI FPV` to `DJI FPV - DECOM` (ODL's own label is "DJI FPV (DECOM)"; also
   required so branch 2 of `_match_fleet_aircraft`, the no-serial model
   fallback, does not go ambiguous on two identically-named rows).
4. **Re-pointed 9 flights** with `drone_serial = '37Q7LA800BX0PN'` from the
   DECOM airframe (`f09558d1-…`) to the new active row
   (`007e1483-e5e3-45f9-9610-378f61f5523d`) — they had been fuzzy-matched to
   the wrong airframe pre-ADR-0007. 7 `opendronelog_import`, 2 `dji_txt`.
   Blast radius verified nil first: 0 `mission_flights`, 0
   `maintenance_records`, 0 `maintenance_schedules`, 0 `batteries` referenced
   the DECOM row; the 9 `battery_logs` derive their airframe through
   `Flight.aircraft_id` and follow automatically. Post-assert inside the
   transaction required exactly 9 moved / 0 stragglers. Final: DECOM 3
   flights, active 9.

Also filled `specs` on the Matrice 4T from DJI's published enterprise page.
**The M4T is not IP-rated and its figures legitimately differ from the M4TD**
(49 vs 54 min, 1219 g vs 1850 g, 6000 m vs 6500 m ceiling, no QZSS) — the 4TD
is the heavier dock-compatible variant. A future reader should not "correct"
one to match the other.

Consequences: the startup backfill in `main.py` only touches
`aircraft_id IS NULL`, so none of the above shifts on the next container
restart, and the new M4T row will **not** pick up the 39 existing ODL rows
(20-char serial mismatch, per correction 1). Aircraft table is now 11 rows,
zero duplicate serials.

### Log inventory — corrected 2026-09-05 (recovery hunt finished)

**Counts re-verified by me against the live prod DB on 2026-09-05, not taken
from the plan:**

```
$ docker exec droneops-standby-db psql -U droneops -d droneops -tAc \
    "SELECT source, count(*) FROM flights GROUP BY source ORDER BY 2 DESC"
opendronelog_import|584
dji_txt|218
$ docker exec droneops-backend-1 sh -c 'ls /data/uploads/flight_logs | wc -l'
192
```

So: `dji_txt` is **218** (not 210 — 8 uploaded 2026-09-04 23:47 PDT), retained
files **192** (not 184), of which 2 are dummy test files → **190 real
originals**. The S3 mirror holds 184, not 183. **The 28 file-less rows are
unchanged.** Anything in the plan that hard-codes 210 or 182 is stale by
construction — the backfill and the crate evaluation should re-derive the
count at run time, because it moves every time Bill uploads.

- **All 584 OpenDroneLog-era originals recovered** from Bill's Google Drive to
  BOS-HQ `~/droneops-staging/drive-logs/` (0 download failures; sha256 + header
  CSV alongside; `docs/plans/data/2026-09-04-drive-logs-inventory.csv`).
  584/584 filenames match the `opendronelog_import` rows 1:1 → P7 matches on
  `original_filename`. 548 hashes equal ODL's own recorded sha256. 20 duplicate
  existing `dji_txt` flights; 564 new.
- **The staging backup gap is CLOSED** (2026-09-05): those 584 files are in the
  existing droneops restic repo under tag `staging` — snapshot `4c08afa7`,
  2.181 GiB, in R2, independently verified. Source mounted read-only; nothing
  moved or reconfigured, so there is nothing for the deployer to clobber.
  **Caveat: this is a one-shot snapshot of a static archive, not a recurring
  lane.** Files added after 2026-09-05 are unprotected; the durable fix is P7
  ingesting them into `/data/uploads/flight_logs/`.
- **The 28 missing `dji_txt` originals are unrecoverable, and the mechanism is
  now proven rather than inferred.** Of the 52 rows created 2026-03-23 →
  2026-04-19, exactly 24 have files, and that set of 24 is byte-identical to
  `~/migration/doc_appdata.tar.gz`. **The survival boundary is not a date — it
  is "was the file inside the migration tarball."** HSH's `~/backups/pg-backup.sh`
  still exists and runs only `pg_dump` plus an n8n sqlite `.backup`; it never
  touched `/data/uploads`. The HSH prod era had **no file-level backup of
  flight logs at all** — the bytes were never captured. This is firmer than the
  plan's "backups began 2026-07-16" and it closes the question.
- Their `original_filename` values are recovered and follow **three distinct
  naming patterns**, so a search for `DJIFlightRecord*` alone misses 12 of 28.
  Manifest (`missing_28_full.tsv`) and report
  (`FP1-log-recovery-hunt-2026-09-05.md`) are in the recovery session's
  scratchpad — **they should be copied into `docs/plans/data/` before that
  scratchpad is reaped.** I have not done that: they are another session's
  files and I did not want to commit artifacts I had not produced or read in
  full.
- **Two long-standing facts were wrong and are corrected in the plan.**
  DroneOpsSync is an **Android APK on the controller**, not a Windows companion
  — a Windows companion was formally rejected in its ADR-0007, and NEXTL3VEL
  was never in the ingest path, which largely dissolves it as a lead. The seed
  of that misconception looks like a comment in
  `backend/tests/test_flight_ingest_consolidated.py`, **fixed on this branch**.
  Separately, the Synology Active Backup range is 10 versions,
  2026-05-29 → 2026-06-07 (not → 2026-09-01), with missed backups logged daily
  since 2026-06-08, and its repo is not shell-enumerable — the portal is the
  only path.
- **New P7 lead (not a dependency):** `2026-03-06_20-38-33_Open_Dronelog.db.backup`
  (95.6 MB, md5 `56a156aa…`) in two Synology Downloads archives, reporting
  **576** flights — a week newer than the DuckDB the plan cites. It may carry
  sha256s for some of the 36 files that currently match by filename only, which
  would upgrade them to hash matches in P7's verification step.

**Waiting on Bill:**
1. ~~Merge call on `feat/fp1-flight-details`~~ — **DONE 2026-09-05 02:35 PDT**,
   merged and deployed (verified below).
2. A `dji_m4t_official.png` asset for the new Matrice 4T fleet tile.
3. Nothing further on the 28 missing logs — they are gone, and the reason is
   now evidenced rather than assumed.


## 2026-08-17 — Encrypted R2 backup (ADR-0041) — LIVE — **§5.7 CUTOVER EXECUTED 2026-09-21**

**The new lane is deployed, running on a timer, and verified end-to-end**
(V1–V12, see ADR-0041 "Implementation outcome"). **The legacy lane is retired as
of 2026-09-21 ~14:55 PDT** — see "Cutover executed" at the end of this section.
Everything between here and there describes the parallel-run window that has now
closed, and is kept because the criteria it states are what the cutover was
gated on.

**Green-day window opened:** 2026-08-17 (first timer-driven run 15:23 UTC).

**Soak tally** (timer-driven runs, `Result=success` + metric advanced, each
verified live over ssh — not inferred):
- 2026-08-17 15:23 UTC ✓ (completed 15:28:22)
- 2026-08-18 03:23 UTC ✓ (completed ~03:28)
- 2026-08-18 15:23 UTC ✓ (completed ~15:29)
- remaining: the 2026-08-19 pair + 2026-08-20 03:23 → then §5.7 fires
  **automatically**.

**Cutover is AUTOMATED (operator-approved 2026-08-18).** A user-level systemd
timer on droneops-server (`~/.config/systemd/user/droneops-backup-cutover.timer`)
fires `scripts/droneops-backup-cutover.sh` once at **2026-08-20 04:12 UTC**.
The script re-verifies every gate below over ssh BEFORE mutating anything
(≥6 completions, metric <13 h, `Result=success`, ≥4 restic db snapshots),
executes §5.7 (cron line out, plaintext R2 prefix deleted, `snapshot.sh`
retired), flips this doc, commits/pushes, syncs the BOS clone, and reports the
outcome — success or abort — to ntfy `infrawatch-alerts` with a click URL.
Any gate failure aborts before mutation. Dry-run tested 2026-08-18 under the
systemd user environment (correct refusal on the not-yet-met snapshot gate).
Cancel with: `systemctl --user disable --now droneops-backup-cutover.timer`
(on droneops-server). Note: criterion 4 (Sunday `--read-data-subset=5%`) is
satisfied by the manual deep-read checks in V2 + the DR rehearsal (both clean);
the first in-script Sunday run lands 2026-08-23, after cutover — accepted.

> **What actually happened — the automatic attempt ABORTED on 2026-08-28.** The
> timer fired and the script **correctly refused to mutate**, reporting
> `only 5/6 completed runs in last 3 days`. **The backup lane was never the
> problem — it was green twice daily throughout.** Gate 1 counted `done.` lines
> out of **journald**, and **journald on BOS-HQ retains under three days**, so the
> oldest completion in a 72 h window had rotated out before the gate read it: a
> healthy 6-of-6 lane reported 5. The gate was measuring *log retention*, not
> *backup success*, and would have re-failed on every retry.
>
> **Gate rewritten 2026-09-21** (in `scripts/droneops-backup-cutover.sh`): count
> the lane's **own output** — `restic snapshots --tag db --json` filtered to the
> last 72 h. `forget --keep-daily` collapses the two daily runs to one kept `db`
> snapshot per day, so **≥3 snapshots in 72 h *is* "three consecutive green
> days"**. The journald figure is still gathered but only printed as context; it
> can no longer abort the run. The same `snapshots` call now also serves Gate 4,
> so the script makes one restic call instead of two.
>
> **Rule this generalises to:** a gate asserting "N events in the last T" must
> read a store whose retention exceeds T. Logs are the wrong store by default;
> the lane's durable output is the right one. Full record: ADR-0041 Amendment 2.
> The spent one-shot timer was **disabled** on 2026-09-21.

Also archived into this repo's restic repository during the window: the final
n8n database snapshot, tag `legacy-n8n` (see CHANGELOG 2026-08-17 entry and the
runbook lane table).

### Cutover criteria — ALL must hold before running §5.7 — **ALL SATISFIED 2026-09-21**

> Criterion 1's *check command* below is the one that was wrong (journald
> retention < 3 days). The criterion itself — three green days — held, and was
> re-measured against restic `db` snapshots instead. See the note above.

1. Three consecutive days with `droneops_backup_last_success_timestamp_seconds`
   advancing after **timer-driven** runs (not hand runs). Check:
   ```bash
   ssh 10.99.0.4 'systemctl list-timers droneops-backup.timer --no-pager;
                  journalctl -u droneops-backup.service --since "3 days ago" | grep -c "done\."'
   ```
   Expect ≥ 6 completions (twice daily × 3 days).
2. `obs-rule-droneops-backup-stale` has not fired in that window.
3. `restic check` green on every run (it is fatal in-script, so any failure
   would already have paged).
4. A Sunday `--read-data-subset=5%` run has completed at least once.

### Cutover commands (run on BOS-HQ `10.99.0.4`, in this order) — **ALL RUN 2026-09-21**

```bash
# 1. Retire the legacy cron line (leaves CallSign's line intact)
crontab -l | grep -v 'droneops/scripts/snapshot.sh' | crontab -
crontab -l                                   # verify only the callsign line remains

# 2. Retire the superseded script (history preserves it)
cd ~/droneops && git rm scripts/snapshot.sh && git commit -m 'ops(backups): retire snapshot.sh, superseded by droneops-backup.sh (ADR-0041) [skip-deploy]'

# 3. Delete the old PLAINTEXT R2 prefixes (~2.3 GiB, 229 objects)
#    ⚠️ Confirm the new repo has >=3 days of db snapshots FIRST.
docker run --rm --network host \
  -e AWS_ACCESS_KEY_ID=... -e AWS_SECRET_ACCESS_KEY=... -e AWS_EC2_METADATA_DISABLED=true \
  amazon/aws-cli s3 rm --recursive \
  --endpoint-url https://<R2_ACCOUNT_ID>.r2.cloudflarestorage.com \
  s3://obs-glitchtip-backups/droneops/
```

Then confirm the freshness metric keeps advancing for **three more days**
before declaring done. — **this post-cutover watch is the one piece still
running: the three-day window opened 2026-09-21 and closes 2026-09-24.**

### 2026-08-17 later the same day — cold DR rehearsal PASSED, four defects fixed

The lane was re-reviewed adversarially and rehearsed **cold**: rebuilt from the
1Password Fleet items and the R2 bucket only, on `droneops-server`, reading
nothing from BOS-HQ but comparison hashes and touching no production container
or volume. Restic-from-R2, the break-glass `.sql.gz` and live prod agreed on
every content digest; all 226 files in the `files` lane are sha256-identical to
production; the restored `.env` matches live byte-for-byte and the full stack
renders from it. **Recovery works cold — the filed secrets are sufficient.**
Evidence: `docs/runbooks/droneops-backup-restore.md` §11.

Fixed in `c3d9502`: missing `flock` concurrency guard; `backups/` not
gitignored (1.1 GB of plaintext PII dumps in the deploy clone's working tree);
the quarterly drill never reading the `files` lane; a post-metric error hole.
Plus a runbook defect — Procedure A2's first database command
(`docker compose up -d droneops-standby-db`) fails with `no such service`; the
service is `db-standby`.

Retention was challenged and **upheld**: a synthetic 40-day twice-daily corpus
converged exactly as designed. This does **not** change the three-green-days
cutover gate below — it remains the criterion.

### Also at cutover (do not forget) — **status 2026-09-21**

- **OPEN, now unblocked (ROADMAP `BK-3`): update the two Grafana rule
  descriptions.** This lives in `~/noc-master`, **not in this repo**, which is
  why the cutover script could not do it. It was blocked on the cutover
  happening; it no longer is.
- **Update the two Grafana rule descriptions.** `obs-rule-droneops-backup-stale`
  still instructs the operator to `tail ~/droneops/backups/snapshot.log` and
  re-run `snapshot.sh`. Replace with
  `journalctl -u droneops-backup.service -n 50` and
  `sudo systemctl start droneops-backup.service`. In
  `/opt/infrawatch/grafana/provisioning/alerting/observability-alerts.yml`.
  **Change the `description` text only — the metric names and expressions are a
  hard contract and must not move.**
- **Keep the local `.sql.gz` lane.** It is not part of the old lane being
  retired; it is the documented break-glass path that needs no
  `RESTIC_PASSWORD`.

### Open, for Bill — **reconciled 2026-09-21: one of four still open**

- **ntfy topic** stayed `infrawatch-alerts` per the 2026-07-14 "no new topics"
  decision, rather than the new `droneops-backup` topic the brief proposed.
  *(Informational — decided, not a question.)*
- ~~**7-year yearly retention**~~ — **CLOSED 2026-08-18.** Bill: *"retention is
  indefinite."* `KEEP_YEARLY` 7 → `unlimited` in `droneops-backup.sh`; yearly
  snapshots are never pruned (ADR-0041 D4 amended, CHANGELOG 2026-08-18). Daily/
  weekly/monthly unchanged (14/8/24). **Any "7-year retention" phrasing left in
  ADR-0041's older sections is historical.** Note the ADR-0232 consequence: the
  B2 copy is never pruned at all, so retention here no longer bounds how long
  the bytes exist anywhere.
- **STILL OPEN — `~/backups/n8n_*.sqlite` on droneops-server** (~840 MB,
  root-owned, stops 2026-04-15) — untouched, out of scope, needs a separate
  keep-or-delete decision from Bill.
- ~~**Legacy volumes**~~ — **CLOSED 2026-08-18.** `droneops_postgres_data`
  (46 MB) was archived into the encrypted restic repo first (tag
  `legacy-bos-primary-pgdata`, snapshot `66ed2135`, restore-read verified:
  1,204 entries incl. `PG_VERSION`), then removed; the empty
  `droneops-demo_ollama_data` was removed with nothing to archive. **The demo
  stack itself was NOT touched** — it is live and tunnel-exposed and its
  volumes are in active use, not legacy. CHANGELOG 2026-08-18.

### Cutover executed 2026-09-21 — §5.7 DONE

Ran at **~14:55 PDT** by `scripts/droneops-backup-cutover.sh`. The script
re-verified every gate over ssh before touching anything, then executed §5.7:

1. **Legacy cron line removed** on BOS-HQ (`~/droneops/scripts/snapshot.sh`).
   **CallSign's own `snapshot.sh` line at `30 3 * * *` was left intact** — two
   different repos share that script name, which is why the `grep -v` matches
   the full path. Verified after: the operator crontab holds exactly the
   CallSign line and the `demo-nightly-reset.sh` line.
2. **Plaintext R2 prefix deleted** — `s3://obs-glitchtip-backups/droneops/`
   (~2.3 GiB / 229 objects).
3. **`scripts/snapshot.sh` removed** from the repo (git history preserves it).

**The deleted prefix is not gone from everywhere, on purpose.** The fleet's
second provider (noc-master **ADR-0232**, live since 2026-09-11/12) runs a
`fleetbackup-r2-mirror` lane that `rclone copy`s — never `sync`s — every R2
bucket into Backblaze B2 `barnardhq-fleet-nightly` under **Object Lock
compliance, 90 days, keep-all-versions, never pruned**. That prefix had already
been copy-forwarded before today's delete, so the retired plaintext dumps still
exist in immutable B2 and are not purgeable for at least 90 days. Right posture
for a *retirement*; wrong assumption for a *deletion obligation* — this repo
holds executed TOS PDFs and invoice records, and an `s3 rm` no longer reaches
every copy.

**Correction to the record the script wrote:** it stamped "via systemd timer".
It was **run by hand on 2026-09-21**. The timer's own firing on **2026-08-28
aborted** — see the abort note above — and the spent one-shot timer (user scope
on HSH-HQ / droneops-server) was **disabled** today.

**Committed as `d153623`** (already pushed). The gate rewrite that unblocked it
is in this same sweep's commit.

**Still to do:** the post-cutover three-day freshness watch closes 2026-09-24,
and ROADMAP `BK-3` (the two Grafana rule descriptions in `~/noc-master`) is
now unblocked and open.

## 2026-06-15 — Device-upload async decoupling (audit P2-2) — DESIGNED (not started)

The last open item from the 2026-06-11 ground-up audit (P2-2 full leg) is now
designed. Analysis + docs only; no application code touched.

- **Decision (ADR-0023):** add a **separate** async route
  `POST /api/flight-library/device-upload/async` (202 + `{batch_id}`) + a poll
  route `GET .../device-upload/status/{batch_id}`, mirroring the v2.70.0
  backup-job pattern (`backup_jobs.py` + `run_backup_job_task` +
  `/api/backup/jobs`). The legacy synchronous `device-upload` route stays
  unchanged forever (native APK = no OTA, so old field devices must keep
  working). Separate route chosen over a capability header because the
  response shape/status code differ — two clean contracts beat one URL with
  two behaviours.
- **batch_id granularity:** keep one-file-per-request (one `batch_id` per
  file); the contract also supports multi-file submit but the first client
  release won't use it (per-file = better field reliability + 1:1 onto the
  existing `UploadStatus`).
- **Client (DroneOpsSync ADR-0008):** the **socket-timeout-is-per-file** fix
  (`MainViewModel.kt:721-725` — drop `aborted = true`) is a one-line,
  backend-independent reliability win recommended as a **standalone fast-follow
  APK** ahead of the async leg. `UnknownHostException` + 401/403 keep
  `aborted = true` (correctly batch-wide). Timeout retune deferred to the
  async-adopting release (lowering it before the parse moves off-request would
  amplify the hang).
- **Plan:** `docs/plans/2026-06-15-device-upload-async-decoupling.md` —
  Stage A (redis module) → B (route+task+poll, ships backend-first) → C (client
  fast-follow, parallel) → D (client async adopt, after B) → E (docs).
- **Verification of audit claims:** all file:line claims confirmed against
  current source. One nuance: `performUpload` also sets `aborted=true` on HTTP
  401/403 (`:690`) — that one is correct and is left in place.
- **No data-loss risk today:** SHA-256 server-side dedup already makes every
  path idempotent; the brittleness costs bandwidth + a confusing half-failed
  sync UX, not lost flights.
- **Owner:** aegis (backend leg) + fleet-mobile-engineer/aegis (client leg).
  Not started.

## 2026-05-14 — Mission-report overall quality — OPEN BACKLOG (watching brief)

Operator feedback at the ADR-0015 close-out, verbatim: *"its ok for
now but it needs to get better."* Referring to the overall
client-facing mission report quality, not any specific defect. No
specific changes requested; this is a signal that the current quality
bar is not the destination.

**Status.** NOT STARTED. Tracked on ROADMAP as `FU-AI-QUALITY-PASS`
under the "LLM-assisted report surface" section. The audience-separation
contract from ADR-0015 is load-bearing; any quality work happens
inside that contract.

**Operating rule for future sessions.** Do not assume a direction and
start editing the prompt or the report template. When asked to "improve
the report," first ask the operator what specifically he wants
improved. Candidate areas captured on the ROADMAP entry are inference
for kickoff, not a committed punch-list. Trigger to act is
operator-driven.

## Follow-ups (Observability Phase 5, 2026-04-18 — open residue only)

Trimmed 2026-09-21; the two resolved bullets moved to
`docs/archive/PROGRESS-2026-H1.md` with the rest of that section.

- **Companion APK instrumentation — STILL OPEN.** Per
  `feedback_droneops_companion_apk.md`, the Android companion is not
  instrumented in Phase 5. **Note the path in the original bullet
  (`~/droneops/companion/`) is dead** — `companion/` was deleted from this repo
  in `4b87e65` and the real companion lives in `BigBill1418/DroneOpsSync`
  (native Kotlin). If it needs `SentryAndroid.init`, that is a commit + APK
  rebuild + release **in that repo**, not this one.
