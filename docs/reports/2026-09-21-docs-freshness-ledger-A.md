# Docs-freshness pass, 2026-09-21 — Ledger A (root docs, `docs/*`, runbooks, ops, infra comments)

**Scope:** part A of a two-part exhaustive pass. Part B owns `docs/adr/`,
`docs/plans/`, `docs/reports/`, `docs/incidents/`, `docs/superpowers/`,
`docs/patches/` — nothing under those paths was edited here (this ledger is a
new file, created because the pass brief directs it).

**Repo state at start:** `main` = `origin/main` = `28b9c98`, clean.
**Verification sources:** `git log` / working tree / read-only `ssh
bbarnard065@10.99.0.4` (BOS-HQ: `docker ps`, `docker exec … printenv`, `curl
localhost:8000/openapi.json`, `crontab -l`, `systemctl list-timers`, `systemctl
cat`) / live DNS + HTTP / `~/noc-master` config on HSH-HQ. **No memory was used
as evidence.**

**Ground truth re-measured today (2026-09-21, times Pacific):**

| Fact | Observed | How |
|---|---|---|
| Live app version | **2.92.1** | `curl localhost:8000/openapi.json` → `info.version`; `docker exec droneops-backend-1 printenv APP_VERSION` |
| flight-parser | **1.2.0** | `GET flight-parser:8100/health` from inside `droneops-backend-1` |
| Alembic head | **`0011_battery_src_truth`** | `docker exec droneops-backend-1 alembic current` |
| Demo stack | **2.92.1**, at `/home/bbarnard065/droneops-demo` (no `/opt/droneops-demo`) | `curl localhost:8001/openapi.json`; `ls -d` |
| Replication | primary = BOS-HQ `droneops-standby-db` (`pg_is_in_recovery()=f`); standby = `10.99.0.2`, `application_name=chad_hq_standby`, `streaming` | `pg_stat_replication` |
| Ollama model | **`llama3.1:8b-instruct-q4_K_M`, 4.9 GB** | `docker exec droneops-ollama-1 ollama list` |
| Seeded aircraft / rate templates | **7 / 14** | parsed `backend/app/seed.py` |
| frontend nginx port | **8080** (non-root); `80/tcp` in `docker ps` is the base image's unused `EXPOSE` | `frontend/nginx.conf:6`, `frontend/Dockerfile` |
| Basemap probe schedule | `crontab(day_of_week=1, hour=15, minute=47)` → **Mon 15:47 UTC = 08:47 PDT** | `backend/app/tasks/celery_tasks.py:92-95` |
| `command.barnardhq.com` | **NXDOMAIN, no zone wildcard**, `curl` → 000 | `dig`, `curl` |
| `command-demo.barnardhq.com` | resolves, **HTTP 200** | `curl` |
| `droneops.barnardhq.com` | **HTTP 302** (Cloudflare Access) | `curl` |
| `droneops-backup.timer` | `03:23` + `15:23` **UTC** (= 20:23 / 08:23 PDT), UTC-anchored on purpose | `systemctl cat` |
| `droneops-restore-drill.timer` | 16th of Jan/Apr/Jul/Oct `16:23 UTC`; next **2026-10-16 09:23 PDT** | `systemctl cat`, `list-timers` |
| Backup freshness metric | `droneops_backup_last_success_timestamp_seconds` = 2026-09-21 08:26 PDT | textfile collector on BOS |
| Cutover one-shot timer | `disabled` + `inactive`, no enable symlink | `systemctl --user is-enabled/is-active` |
| `doc-autogen.timer` | fires `04:00` + `16:00` **local (America/Los_Angeles)**, not UTC | `systemctl --user cat` + `list-timers` (last 04:00 PDT / next 16:00 PDT) |
| noc-master decision file | `docs/decisions/**DEC**-0013-docs-only-deploy-skip.md` (no `ADR-0013-*`) | `ls ~/noc-master/docs/decisions/` |
| `flight-parser` in deployer `build_map` | **present** (added 2026-09-06) | `~/noc-master/data/config.yml:961-964` |
| `droneops-managed` network | `172.29.0.0/16` | `docker network inspect` |

---

## Per-file verdicts

### Root documents

| File | Verdict | What changed | Evidence |
|---|---|---|---|
| `README.md` | **CORRECTED** (24 fixes) | See detail table below. | live `openapi.json`, `docker ps`, `seed.py`, `App.tsx`, `config.py`, `docker-compose.yml`, `flight-parser/Cargo.toml`, `frontend/nginx.conf`, DNS |
| `CLAUDE.md` | **CORRECTED** (5) | Deleted a duplicated `APP_VERSION` block that **directly contradicted** the paragraph above it (one said nothing reads the env var since v2.92.1, the other said it tags Sentry releases and `H-1` is open). Replaced the deploy-verification recipe: `droneops.barnardhq.com` is Access-gated, so `curl` gets a 302 and `curl -fsS` without `-L` scores that as success — now verifies from BOS-HQ `localhost:8000`. Corrected `admin_device_rotation.py:35,175,177` → `:35` + `:163` (one call site, not two). Corrected the `command.barnardhq.com` claim (it said "resolves only via the zone wildcard, CF 530"; there is no wildcard and it returns nothing). Pinned the demo path to `~/droneops-demo` on BOS-HQ and noted worker/beat stay stopped. | `git show c70ccc8`, `sentry.py`, `sentry.ts`, `grep -n send_alert`, `dig`/`curl`, `ls -d` on BOS |
| `CONTRIBUTING.md` | **CORRECTED** (5) | Flight Parser "Python microservice" → **Rust (axum), port 8100**. LLM model corrected. Added the four `:?`-required secrets to the local-dev block (a literal `cp .env.example .env && docker compose up -d` fails without them). Added a **Tests** section stating there is no pytest/cargo job in CI and how to run them (incl. blanking `OTEL_EXPORTER_OTLP_ENDPOINT`). Fixed the `../../issues` relative link to an absolute URL and pointed at `ROADMAP.md`. | `flight-parser/Cargo.toml`, `docker-compose.yml`, `.github/workflows/` |
| `ROADMAP.md` | **CORRECTED** (4) | **MP-2's first-run date was wrong**: it said "first run Monday 2026-09-22" — 2026-09-22 is a **Tuesday**, and v2.92.0 went live at 14:52 PDT on Monday 2026-09-21, *after* that day's 15:47 UTC slot. First scheduled run is **Mon 2026-09-28**; noted that two weekly runs land 09-28 + 10-05 (so the "earliest 2026-10-05" headline still holds) and two full weeks is 10-12. FU-8's alembic correction said "0001…0009, all nine"; it is eleven. `FU-AI-RUNTIME-GATE` cited `main.py:114-122` for `_add_missing_columns`, which is at `:67`. | `celery_tasks.py:92-95`, `date -d`, `ls backend/alembic/versions/`, `grep -n _add_missing_columns` |
| `PROGRESS.md` | **CORRECTED** (6) | First scheduled probe run `2026-09-22` → **2026-09-28**. "demo backend reports 2.92.0" → 2.92.1 (re-read live). Open item 3 ("CLAUDE.md still says 5 files / 6 locations") is **DONE** — CLAUDE.md says 6/7. The 2026-09-05 "`flight-parser` absent from the deployer `build_map`" residual is **CLOSED** (mapped 2026-09-06). The "copy the manifest + report into `docs/plans/data/`" bullet is **DONE** (both files verified present). Added a supersession banner over the 2026-09-05 log-inventory counts (190/192/218), which the 2026-09-11 re-derivation replaced with 198/226. | live `openapi.json` (:8001), `noc-master/data/config.yml`, `ls docs/plans/data/`, `ls docs/reports/` |
| `CHANGELOG.md` | **CORRECTED** (3) + **HISTORICAL-ANNOTATED** (2) | Header banner: the doc-autogen timer fires at 04:00 + 16:00 **Pacific**, not UTC (the unit's own comment is the stale one) — and the link target `docs/decisions/**ADR**-0013-…` is a **dead link**; the file is `DEC-0013-…`. Dated `Status 2026-09-21:` notes appended to the 2026-09-21 sweep entry ("the live app stays 2.92.0" / `H-1` open / host `.env` pinned 2.67.4 — all three superseded hours later by v2.92.1) and to the 2026-09-11 entry's "Now 5 files, 6 locations". Older entries left verbatim as a dated ledger. | `systemctl --user cat doc-autogen.timer`, `systemctl --user list-timers`, `ls ~/noc-master/docs/decisions/` |

#### README.md — the individual corrections

| # | Was | Now | Why |
|---|---|---|---|
| 1 | Architecture table listed a **Watchtower** service | removed; new *Base image updates* section explains ADR-0088 | no watchtower in any compose file — only a comment recording its 2026-06-05 removal |
| 2 | *Watchtower (Auto-Update)* config section + `WATCHTOWER_*` vars | replaced with 7 config sections that actually exist (trusted-proxy, customer-link lifetimes, operator locale, managed instance, demo mode, alerting/watchdogs, observability) | nothing in the repo reads `WATCHTOWER_*` |
| 3 | *Watchtower (base image updates)* under Updating | rewritten as pinned-tag guidance | same |
| 4 | Flight Parser = "Python microservice" | **Rust (axum)** | `flight-parser/Cargo.toml` |
| 5–9 | "Qwen 2.5 3B", "~1.5GB" ×5 | `llama3.1:8b-instruct-q4_K_M`, ~4.9 GB | `ollama list`; `docker-compose.yml:119` pulls llama3.1, `:160/:288` default `OLLAMA_MODEL` to it |
| 10 | "Aircraft fleet (6 DJI models)" / list of 6 | **7**, Mavic 4 Pro added | `seed.py::AIRCRAFT_SEED` |
| 11 | "rate templates (8 billing presets)" / list of 8 | **14**, full list | `seed.py::RATE_TEMPLATE_SEED` |
| 12 | `POSTGRES_PASSWORD` default `changeme_in_production`; `DATABASE_URL` "postgresql+asyncpg://…"; `JWT_SECRET_KEY` default `changeme_generate_a_random_secret` | all three **no default — required**, `:?`-guarded | `docker-compose.yml` ADR-0012 guards |
| 13 | Quick-Start step 2 named only `POSTGRES_PASSWORD` + `JWT_SECRET_KEY` | names all **four** `:?`-required values with generation commands | `cp .env.example .env` + `up -d` fails on `DATABASE_URL`/`REPLICATION_PASSWORD` otherwise |
| 14 | RAM minimum 8 GB, "Ollama alone reserves ~4 GB" | **12 GB**; 8 g reservation / 10 g cap / ~6 GB resident | compose `mem_reservation`/`mem_limit` + its own comment |
| 15 | `GET/POST /api/missions/{id}/report/generate` | **POST** only | live `openapi.json` |
| 16 | `GET/POST /api/device-keys` | **`/api/settings/device-keys`** (wrong path) | live `openapi.json` |
| 17 | API table missing ~30 live routes | added tile-health, TOS, intake, backup jobs/schedule/history, maintenance schedules, per-area settings, business-signals, demo status, flight-details, async device upload, `/health` | live `openapi.json` |
| 18 | "New Mission (`/missions/new`) — multi-step wizard with 5 stages" | `/missions/new` is a **soft redirect** since v2.67.0; per-section edit routes tabled | `frontend/src/App.tsx:73-84,131-138` |
| 19 | "Setup (`/setup`)" as a route | no such route — rendered in place of the app while no users exist | `App.tsx` |
| 20 | "Client Portal (`/client`)" | real routes `/client/:token`, `/client/login`, `/client/mission/:missionId`; new *Public routes* table incl. `/intake/:token`, `/tos/accept` | `App.tsx:110-114` |
| 21 | `/tos-acceptances` page undocumented | added | `App.tsx:147` |
| 22 | Replication paragraph gave only the standby side | states primary = BOS-HQ `droneops-standby-db`, standby = CHAD-HQ, verified live; adds a warning that `docker-compose.standby.yml` / `init-standby.sh` carry the old direction | `pg_stat_replication` |
| 23 | Two near-duplicate *Updating* sections | Quick-Start one collapsed to commands + a pointer | drift risk |
| 24 | Ollama tuning para understated the pinning/limits | cpuset, 8 g/10 g, parallelism, image pin | `docker-compose.yml:71-104` |

### `docs/*.md`

| File | Verdict | What changed | Evidence |
|---|---|---|---|
| `docs/cloudflare-tunnel-setup.md` | **CORRECTED** (4) | **`frontend:80` → `frontend:8080`** in the public-hostname step, the troubleshooting row and the architecture diagram, plus a new "Healthy tunnel, every request 502s" row. The frontend image runs nginx as non-root: `listen 8080`, `EXPOSE 8080`. `docker ps` shows `80/tcp` only because the `nginx:alpine` base declares it and nothing listens there — so the documented value produced a connection-refused 502. | `frontend/nginx.conf:6`, `frontend/Dockerfile`, `docker ps` |
| `docs/managed-hosting.md` | **CORRECTED** (annotation) | Header note separating the unverifiable commercial offer (pricing, inclusions, `support@` address) from the technically verified half. The `TRUSTED_PROXY_HOSTNAME=caddy` / `FORWARDED_ALLOW_IPS` guidance, the `172.29.0.0/16` CIDR and the fail-closed statement all **re-verified and correct**. | `docker network inspect droneops-managed`, `docker ps \| grep managed`, `backend/app/config.py:53-70` |
| `docs/windows-self-hosting.md` | **CORRECTED** (4) | Ollama model + size; RAM minimum 8 → 12 GB with the real reservation/cap; disk 25 → 35 GB. | `ollama list`, `docker-compose.yml` |
| `docs/TOS-Rebuild.md` | **HISTORICAL-ANNOTATED** | Already carried a "historical handoff spec" banner. Extended it: the alembic range is now **0001…0011** (said 0001…0009), and every `https://command.barnardhq.com/…` URL in the body is **dead** (NXDOMAIN) — prod is `droneops.barnardhq.com` and is Access-gated, so the `curl -sS -I` smoke checks in §Deploy would hit the Access login page. Body left verbatim. | `ls backend/alembic/versions/`, `dig`, `curl` |
| `docs/migration-synology-to-ubuntu.md` | **HISTORICAL-ANNOTATED + CORRECTED** (3) | New header banner: one-time runbook, superseded by the backup runbook's Procedure A; the autopull units it installs/checks do not exist; the stock `db` + `doc`/`doc` role is not the BOS topology. Rewrote the *ONGOING MANAGEMENT* block, which advertised `sudo ./setup-server.sh --branch main` (no such flag), "tracks `claude/dev` by default" (no such branch) and `systemctl list-timers droneops-autopull*` (no such units). Fixed `git checkout main # or claude/dev`. | `setup-server.sh` argument parser, `git branch -r`, ADR-0018 |

### `docs/runbooks/`, `docs/ops/`, `docs/archive/`

| File | Verdict | What changed | Evidence |
|---|---|---|---|
| `docs/runbooks/droneops-backup-restore.md` | **CORRECTED** (6) | Header now records the **legacy lane RETIRED 2026-09-21** and that restic is the sole lane. Timer schedule labelled `03:23/15:23 UTC = 20:23/08:23 PDT` with the reason it stays UTC-anchored. §8 triage step 1 told the operator to `tail ~/droneops/backups/snapshot.log` "for the legacy lane" — **both gone at the cutover**; replaced, with a note that the `.sql.gz` break-glass lane is *kept* and has no log. §9 prose said "24-month / 7-year retention" and "7-year retention is the affordable choice" while the `forget` command beside it already read `--keep-yearly unlimited` — reconciled to 14/8/24 + unlimited, with the ADR-0232 consequence. Procedure A4 verified a restore with `curl -sf https://droneops.barnardhq.com/health`, which is Access-gated (302, and `-sf` without `-L` reads a 302 as success) — replaced with host-local checks. §6 drill schedule labelled PDT with the next date. | `git log d153623`, `crontab -l` on BOS, `systemctl cat droneops-backup.timer` / `droneops-restore-drill.timer`, `scripts/droneops-backup.sh:70-73`, `curl -o /dev/null -w %{http_code}` |
| `docs/ops/2026-08-05-prod-maintenance-interval-tune.md` | **CURRENT** | Nothing. Dated data-only record; its own same-day correction is intact; "next date-based due item 2026-10-04" is still in the future. | read in full |
| `docs/archive/PROGRESS-2026-H1.md` | **CURRENT** (header/pointer only, per scope) | Nothing. The frozen-archive banner, the live-document list and the two named traps (`.deployer-disabled`, `update.sh`) are all accurate. | read header; `ls docs/reports/` |

### Config, compose, scripts, units, CI

| File | Verdict | What changed | Evidence |
|---|---|---|---|
| `.env.example` | **CORRECTED** (13) | `OLLAMA_MODEL` **`qwen2.5:3b` → `llama3.1:8b-instruct-q4_K_M`** — this was a live foot-gun: `ollama-setup` pulls llama3.1, so copying `.env.example` made the app request a model that was never pulled. Removed `APP_NAME` and `WATCHTOWER_MONITOR_ONLY`/`_NOTIFICATION_URL` (**nothing reads them**). Added the 13 vars the code reads that were absent: `CLAUDE_MODEL`, `OPERATOR_TIMEZONE`, `INTAKE_TOKEN_EXPIRE_DAYS`, `CLIENT_TOKEN_EXPIRE_DAYS`, `MANAGED_INSTANCE`, `CLIENT_ID`, `ADMIN_USERNAME`, `ADMIN_PASSWORD`, `DEMO_MODE`, `DEMO_RESET_INTERVAL_HOURS`, `NTFY_DRONEOPS_PUBLISHER_TOKEN`, `DEVICE_SILENCE_{HOURS,ACTIVITY_WINDOW_DAYS,DEDUP_HOURS}` — each with a one-line comment. Added an explicit "`APP_VERSION` is cosmetic, do not set it here" block. **Programmatically verified afterwards: the symmetric difference between `.env.example` keys and (pydantic `Settings` fields ∪ direct `os.environ` reads) is empty in both directions.** | `backend/app/config.py`, `grep -r os.environ backend/app`, `grep -r import.meta.env frontend/src`, `grep '${' docker-compose*.yml`, `scripts/*.sh` |
| `docker-compose.yml` | **CORRECTED** (3 comment blocks) | Top observability comment named **HSH-HQ** Alloy and `/opt/observability-agent/alloy/config.alloy`, and called `docker-compose.demo.yml` "(CHAD-HQ)". This stack **and** the demo run on BOS-HQ, where Alloy lives at `/opt/observability/alloy/config.alloy` (verified to match `com.barnardhq.{project,service,env,tenant}`); the HSH path is a different host's legacy tree. Added "cosmetic since v2.92.1" notes on `APP_VERSION` (backend) and `VITE_APP_VERSION` (frontend build-arg). | `docker inspect alloy` mounts + `grep com.barnardhq` in the config, `ls -d` on both hosts |
| `docker-compose.demo.yml` | **CORRECTED** (2) | Header said "Usage from **`/opt/droneops-demo/`**" (does not exist — it is `/home/bbarnard065/droneops-demo` on BOS-HQ) and described a retired **CHAD-HQ-local Alloy cascade (10.99.0.2)** that **its own service block contradicts** (`OTEL_EXPORTER_OTLP_ENDPOINT` defaults to `alloy.barnardhq.com:4317`, BOS-HQ, noc-master ADR-0050). Both fixed; added the not-deployer-managed + worker/beat-stay-stopped facts and `bootstrap.sh` as the preferred entry. Also `.env.demo "(e.g. CHAD-HQ)"` → BOS-HQ. | `ls -d` on BOS, the file's own line 50, `docker ps` |
| `docker-compose.standby.yml` | **HISTORICAL-ANNOTATED** | Header claimed "runs on HSH-HQ to maintain a standby of the primary on CHAD-HQ (10.99.0.2:5434)". The direction **inverted** at the 2026-04-20 HSH→BOS migration: primary is BOS-HQ `droneops-standby-db`, standby is `10.99.0.2`. So `primary_conninfo host=10.99.0.2` now points at the *standby* and `application_name=hsh_hq_standby` is not what the live replica registers as. Added a ⚠ STATUS block; **the `command:` value itself was left untouched** (it is code, not a comment) — flagged for the orchestrator. | `pg_stat_replication`, `docker-compose.bos-prod.yml` header |
| `docker-compose.demo-standby.yml` | **HISTORICAL-ANNOTATED** | Same inversion, plus: **no demo standby container is running anywhere on the mesh**, and the demo primary's 5435 is on BOS-HQ, not `10.99.0.2`. ⚠ STATUS block added. | `docker ps` on both hosts |
| `docker-compose.bos-prod.yml` | **CURRENT** | Nothing. Header, drift-check command, port map (base `db` 5434 neutralized · `droneops-standby-db` 5434 · demo-standby 5437) and the `alpine:3` neutralization all match the running host. | `docker inspect droneops-db-1` → `alpine:3` + the exact sleep-infinity entrypoint; `docker ps` ports |
| `bootstrap.sh` | **CORRECTED** (2) | Error text told the operator `.env.demo` "should be present on the demo host (**CHAD-HQ**:~/droneops-demo/.env.demo)" — the live demo is on **BOS-HQ 10.99.0.4**. Header updated too. | `docker ps` on BOS, `ls -d ~/droneops-demo` |
| `scripts/init-standby.sh` | **HISTORICAL-ANNOTATED** | Header said "initializes the standby on HSH-HQ … pg_basebackup from the CHAD-HQ primary"; `PRIMARY_HOST="10.99.0.2"` is the pre-migration address and today points at the **standby**, so running it as-is would clone the replica instead of the source of truth. ⚠ STATUS block added; the constant left untouched (code) and flagged. | `pg_stat_replication` |
| `scripts/init-demo-standby.sh` | **HISTORICAL-ANNOTATED** | Same, plus "nothing uses this today". | `docker ps` |
| `scripts/droneops-backup.sh` | **CURRENT** | Nothing. "Supersedes `scripts/snapshot.sh`" is now literally true (it is deleted); four lanes correct; `KEEP_YEARLY=unlimited` carries its own operator-decision comment. | `git show d153623`, `sed -n '70,73p'` |
| `scripts/droneops-backup-cutover.sh` | **CURRENT** | Nothing. Header is accurate and already reconciled: SPENT/executed 2026-09-21 ~14:55 PDT, user-scope timer, `--user is-enabled` → `disabled`, the 2026-08-28 journald-retention abort and the Amendment-2 gate rewrite. **Re-verified the disabled claim independently** (`is-enabled`=disabled, `is-active`=inactive, no `timers.target.wants` symlink). | `systemctl --user is-enabled/is-active`, `ls timers.target.wants` |
| `scripts/restore-drill.sh` | **CURRENT** | Nothing. Quarterly 16th Jan/Apr/Jul/Oct 16:23 UTC matches the installed unit; `DB_CONTAINER="droneops-standby-db"` is already annotated "post-2026-04-20 topology"; metric name matches the live textfile collector and the Grafana contract. | `systemctl cat droneops-restore-drill.timer`, `cat …/droneops_restore_drill.prom` |
| `scripts/demo-nightly-reset.sh` | **CURRENT** | Nothing. "Runs on BOS-HQ from the operator crontab" ✔ (`23 2 * * *`); container names, `doc_demo` role/db and the `DEMO_RESET_INTERVAL_HOURS`-is-unimplemented claim all verified. | `crontab -l` on BOS, `docker exec droneops-demo-db-1 printenv`, `grep -rn demo_reset_interval` |
| `scripts/init-primary.sh` | **CURRENT** | Nothing. | read in full |
| `scripts/primary-entrypoint.sh` | **CURRENT** | Nothing. | read in full |
| `scripts/verify-deploy.sh` | **CURRENT** | Nothing. | read in full |
| `droneops.service` | **CURRENT** | Nothing. Install path, unit name, log/control commands all match `setup-server.sh`. | cross-read both |
| `setup-server.sh` (header) | **CURRENT** | Nothing. Installs exactly one unit; the ADR-0018 note is accurate; `--uninstall` really does clean stray autopull units (lines 69-74), which is what the README claims. | `grep -n autopull setup-server.sh` |
| `.github/workflows/auto-merge-claude.yml` | **CURRENT** | Nothing. | read in full |
| `.github/workflows/secret-scan.yml` | **CURRENT** | Nothing. The "no untrusted input / no injection surface" comment still holds; gitleaks pinned 8.21.2. | read in full |
| `.github/workflows/self-hosted-smoke-test.yml` | **CURRENT** | Nothing. | read in full |
| `frontend/README*`, `backend/README*` | **n/a** | Do not exist. | `ls` |

---

## Cross-cutting checks run

- **Dead relative links:** every relative link target in all 14 markdown files under this
  pass now resolves — **0 dead links** (was 1: `CONTRIBUTING.md` → `../../issues`,
  a GitHub-only relative form, replaced with the absolute URL).
- **README in-page anchors:** all `](#…)` targets match a heading — **0 dead**.
- **`.env.example` ↔ code:** symmetric difference empty in both directions (see above).
- **Every file path cited by a doc** in this set was `test -e`'d.
- **Every compose file re-parsed** after editing (with `!override`/`!reset` tags registered).
- **`bash -n`** clean on every edited shell script.
- **Line-number citations** re-grepped; three had drifted and were fixed
  (`sentry.ts`, `admin_device_rotation.py`, `main.py`).

## Not verifiable from here

- `docs/managed-hosting.md` pricing, plan inclusions and `support@barnardhq.com` —
  commercial offer, no system of record in this repo. Annotated as such rather
  than silently trusted.
- Whether the live `droneops-cloudflared-1` tunnel's public-hostname URL is
  actually set to `frontend:8080` — the container has no shell, and tunnel
  ingress is token-managed at the Cloudflare edge. The repo-side fact (nginx
  listens on 8080 only) is verified; the edge config is an operator read.
