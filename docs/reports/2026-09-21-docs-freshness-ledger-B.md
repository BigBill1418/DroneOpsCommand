# Docs-freshness ledger — Part B (ADRs, plans, reports, incidents, superpowers, patches)

**Date:** 2026-09-21 (times Pacific — fleet hosts run `America/Los_Angeles` since
2026-08-25; container internals and Celery are still UTC)
**Scope:** `docs/adr/*.md` (46) · `docs/plans/*.md` (13) + `docs/plans/data/*` ·
`docs/reports/*.md` (4) · `docs/incidents/*.md` (3) · `docs/superpowers/**/*.md` (5) ·
`docs/patches/*.md` (1). Part A owned README/CLAUDE/CONTRIBUTING/ROADMAP/PROGRESS/
CHANGELOG, `docs/*.md` top-level, runbooks, ops, archive, `.env.example`, compose and
script comments — untouched here.
**Repo state:** `main` = `origin/main` = `28b9c98` at start, clean. Live app
**2.92.1**, flight-parser **1.2.0**, alembic head **`0011_battery_src_truth`**.
**Nothing committed, nothing pushed** — the orchestrator commits once after both parts.

## Ground truth this pass was checked against

Every verdict below rests on one of these, never on prose or recall.

| Fact | How it was established |
|---|---|
| App version **2.92.1** | `curl localhost:8000/openapi.json` on BOS-HQ → `info.version` |
| Alembic head **`0011_battery_src_truth`** | `docker exec droneops-backend-1 alembic current`; `SELECT version_num FROM alembic_version` |
| **16** `ix_*` indexes, DB **124 MB**, extensions = `plpgsql` only | `pg_indexes` / `pg_database_size` / `pg_extension` on `droneops-standby-db` |
| **818** flights: 584 `opendronelog_import` + **234** `dji_txt`; `flight_details` 16; `flight_series` 240 | `SELECT source, count(*) FROM flights GROUP BY 1` + row counts |
| **0** unattributed serial-bearing flights; M4TD 50 / M4T 39 | ADR-0044's own §Verification SQL, re-run |
| Async device-upload routes **live** | `openapi.json` paths list carries `/device-upload/async` + `/device-upload/status/{batch_id}` |
| Backend suite **855 passed, 17 skipped** (82 s) | `cd backend && OTEL_EXPORTER_OTLP_ENDPOINT="" pytest -q tests/` |
| BOS crontab = CallSign `30 3 * * *` + demo-reset `23 2 * * *` | `crontab -l` |
| `droneops-backup.timer` / `droneops-restore-drill.timer` **system** scope, enabled | `systemctl list-timers --all`, `list-unit-files` |
| No `droneops` 4xx alert rule; `obs-rule-droneops-backup-stale` still cites `snapshot.log`/`snapshot.sh` | `/opt/infrawatch/grafana/provisioning/alerting/observability-alerts.yml` |
| Soak-pause dir empty; NOC `/status/:code` route exists | `ls ~/noc-master/data/soak-pause/`; `noc-master/api/routes/status.js` |
| Celery beat TZ is **UTC**; probe = Mondays 15:47 UTC | `celery_app.conf timezone="UTC"`; `docker exec droneops-beat-1 date` → UTC |
| DroneOpsSync async client on `main`, tagged from **v1.3.29** | `~/DroneOpsSync` git log + source |
| CS-Public patch still applies to `0570fdd` | `git apply --check` in `~/repos/CallSignPublic` |

---

## 1. Ledger — one row per file

Verdicts: **CURRENT** = no change needed · **CORRECTED** = a factual error fixed ·
**HISTORICAL-ANNOTATED** = body preserved, dated status/correction note added.

### `docs/adr/` (46 + new index)

| File | Verdict | What changed | Evidence |
|---|---|---|---|
| `README.md` | **NEW** | Created: full 46-row index (number, title, status, date, superseded-by), contiguity statement, the two number-collision traps, and a verified cross-repo ADR table | numbering script; `ls ~/noc-master/docs/adr/` |
| `0001-observability.md` | HISTORICAL-ANNOTATED | Prod+demo are both on BOS-HQ now (not HSH-HQ/CHAD-HQ); Sentry release tag no longer comes from `APP_VERSION` env (v2.92.1) | `docker ps`; `git show c70ccc8` |
| `0002-droneopssync-upload-auth.md` | HISTORICAL-ANNOTATED | Pushover→ntfy supersession; `companion/` deleted `4b87e65`; §6's 3 open questions mapped to `FU-1`/`FU-4`/`FU-5` | `ls companion` → absent; `services/ntfy.py` |
| `0003-zero-touch-device-key-rotation.md` | HISTORICAL-ANNOTATED | Pushover→ntfy; `_add_missing_columns` no longer the runtime path; **15 tests re-verified green** | `pytest tests/test_device_key_rotation.py` → 15 passed |
| `0004-perf-audit-baseline.md` | **CORRECTED** | `main.py:455-470` → `log_requests` (now `:706-716`); DONE status with all 5 commits; "NOC autopull deploys" flagged stale | `grep -n log_requests backend/app/main.py` |
| `0005-perf-audit-results.md` | **CORRECTED** | Two `_filled in by aegis once pushed_` placeholders resolved (`eb60229`, `aea428f`); duplicate empty "FIX-3 _pending_" heading marked as an editing artifact; FIX-4's pending UI smoke closed; all four §Followups re-checked | `git log --oneline --all`; `Settings.tsx` 134 lines; DB 124 MB |
| `0006-pushover-to-ntfy-migration-addendum.md` | **CORRECTED** | Expired soak-pause; `/status/:code` route is live; "still 11 tests" → 15 | `ls ~/noc-master/data/soak-pause/`; `noc-master/api/routes/status.js` |
| `0007-strict-fleet-attribution-matcher.md` | **CORRECTED** | Added "Amended by ADR-0044"; `main.py:308-346` → `_run_startup_schema_and_seed()`; 0-unattributed re-verified | matcher SQL; backend startup log |
| `0008-…payment-gated…` | CURRENT | — | `INVOICE_VISIBLE_STATUSES` unchanged |
| `0009-deposit-feature.md` | **CORRECTED** | "this repo has no Alembic" annulled; ntfy topic registration confirmed done (renamed by noc ADR-0194) | `ls backend/alembic*`; `ntfy-fallback-topics.yml` |
| `0010-tos-acceptance-acroform.md` | **CORRECTED** | Alembic; CF-Access bypass landed; **operator TOS review UI shipped** (`725f5b9`); `CustomerIntake.tsx` still open; 12 tests re-verified | `TosAcceptancesAdmin.tsx`; `pytest` → 12 passed |
| `0011-payment-idempotency…` | **CORRECTED** | Fleet-vs-local ADR-0036/0037 disambiguation; both symbols re-verified | `client_portal.py:527`, `invoices.py:42` |
| `0012-secret-hygiene…` | **CORRECTED** | Fleet ADR-0036 disambiguation; follow-up disposition recorded | `.github/workflows/secret-scan.yml` present |
| `0013-customer-flow-contract-tests…` | **CORRECTED** | **"Implementation status" was stale**: v2.66.3 and v2.66.4 items never built; PEP-563 ban drifted (3 routers) but live `openapi.json` proves it is not reproducing the defect | InfraWatch YAML; `ls backend/tests/contract` → absent; live `openapi.json` |
| `0014-mission-hub-redesign.md` | HISTORICAL-ANNOTATED | Legacy wizard still mounted; deletion criteria never evaluated; the 4xx alert it relies on was never built | `App.tsx:133`; 1,441-line file |
| `0015-mission-report-audience-separation.md` | **CORRECTED** | Fleet ADR-0037; `ollama.py:9-21` → symbol; gate live in prod; guard suite 17 → **36** tests | `celery_tasks.py:166`/`:307`; `pytest` → 36 passed |
| `0016-mission-source-attribution.md` | **CORRECTED** | Decision §3's "no alembic.ini/env.py/versions" annulled | `ls backend/alembic/versions/` |
| `0017-flight-date-operator-timezone.md` | CURRENT | every cited path exists | `ls` checks |
| `0018-deploy-path-is-noc-fleet-deployer.md` | CURRENT | still the canonical deploy record; `setup-server.sh` matches | `grep droneops.service setup-server.sh` |
| `0019-flight-library-list-defers…` | **CORRECTED** | Follow-up 1 (silent ODL fallback) **still open**; follow-up 2 (log spam) not present today, with the reason | `MissionFlightsEdit.tsx:166-176`; `grep -c fleet-match` → 0 |
| `0020-report-geo-buffer-oom.md` | **CORRECTED** | Follow-up status; InfraWatch `droneops-backend-mem` rules confirmed live | InfraWatch YAML lines 1293/1339 |
| `0021-startup-recovery-guard…` | **CORRECTED** | Its own "Alembic = future work" closed by ADR-0022/0036; deferred indexes landed; `client_portal.py:159` → `:154`; four other citations re-verified **exact** | line-by-line `sed -n` checks |
| `0022-alembic-adoption…` | **CORRECTED** | Operator manual-verification plan satisfied (head, 16 indexes, health 200); six §5 line citations corrected, seven confirmed still exact | `alembic current`; `\di ix_*`; `sed` checks |
| `0023-device-upload-async…` | **CORRECTED — headline** | **Status "Proposed — design only, no code shipped" → "Accepted — SHIPPED AND LIVE, both legs"**, with backend v2.71.0 and DroneOpsSync v1.3.29 evidence and five drifted citations | live `openapi.json`; `~/DroneOpsSync` source + `c66931a` |
| `0024-financials-summary…` | CURRENT | — | `financials.py` |
| `0025-large-mission-flight-handling…` | **CORRECTED** | §C pagination follow-up still open, not yet binding (818 flights vs the 2000 cap) | DB counts; `MissionFlightsEdit.tsx:166` |
| `0026-duplicate-flight-attachment…` | **CORRECTED** | `dji.rs:50` → `dji.rs:75`; the Part-107 "honesty flag" conclusion superseded by ADR-0029 | `grep -n "meters AGL" flight-parser/src/dji.rs` |
| `0027-dji-duration-and-flight-name…` | CURRENT | (its `ADR-0036/0037` pair is covered by the index's trap note) | — |
| `0028-flight-data-integrity…` | **CORRECTED — headline** | **§H1 marked SUPERSEDED** by ADR-0029/0031 and the Status line amended; **§"Out-of-repo open item" marked CLOSED** (570/570 ODL peaks verified) | ADR-0029 + ADR-0031 bodies; code has no `PART_107_CEILING_M` |
| `0029`, `0030`, `0031` | CURRENT | — | `report_audience.py` rules present |
| `0032-flight-parser-unit-correctness…` | **CORRECTED** | Both named follow-ups re-verified **still open** (Litchi-first dispatch; no shared units module) | `main.rs:275-277`; `ls flight-parser/src/` |
| `0033`, `0034`, `0035` | CURRENT | already annotated by the `144b0fe` sweep; re-verified correct and complete (incl. `missions.map_share_url` genuinely absent) | `grep map_share_url` → no hits |
| `0036-migration-single-path-hardening.md` | **CORRECTED** | Phase 2 exists as tests but **nothing enforces it** (no pytest in CI); Phase 3 not started; `main.py:387` → `:389`/`:391`; the "ADR-0035" mis-numbering noted | `.github/workflows/`; `pytest` → 17/2 |
| `0037-airspace-laanc…` | CURRENT | `airspace.py` + the static guard test both present | `ls` checks |
| `0038-flight-attach-unification-phase1…` | **CORRECTED** | The ADR-number coordination note **did not hold** — this is 0038, not 0035; residue named | `ls docs/adr/` |
| `0039`, `0040` | CURRENT | their `ADR-0036` refs correctly mean the **local** one | symbols verified |
| `0041-comprehensive-encrypted-backup-to-r2.md` | **CORRECTED** | Residual 2 sharpened from "Correct today" to **wrong today**, with the live Grafana text quoted; added a re-check of the retired cutover timer incl. an explicit statement of what could **not** be verified from this session | InfraWatch YAML line 893; `ls ~/.config/systemd/user/` |
| `0042-fresh-install-integrity…` | **CORRECTED** | "ADR-0035 (migration advisory lock)" → **ADR-0036**; cron `23 9 * * *` UTC → `23 2 * * *` local (same 02:23 PT); in-backend reset still unbuilt | `crontab -l`; `docker-compose.demo.yml:37` |
| `0043-flight-details-sidecar…` | **CORRECTED** | Repaired a **garbled duplicated sentence** in §Context; re-derived counts (234/584/16/240); `flight_library.py:1999-2003` → the extracted `telemetry_downsample` service | DB queries; `flight_library.py:51`/`:2220` |
| `0044-serial-prefix-matcher…` | HISTORICAL-ANNOTATED | Its own §Verification re-run today (0 unattributed; 50/39; 22 tests) | SQL + `pytest` |
| `0045-phase7-customer-surface-hardening.md` | **CORRECTED** | Placeholder `docs/adr/0247-....md` → the real noc-master filename; post-deploy version note (2.91.0 at ship, 2.92.1 now, 855/17); managed-tenant action marked still outstanding | `ls ~/noc-master/docs/adr/`; `pytest` |
| `0046-keyless-basemap-registry…` | **CORRECTED** | Status "deploy is the operator's push" → **shipped and deployed 14:52 PDT**, with the first *scheduled* probe run corrected to **Mon 2026-09-28**; fleet ADR-0037 note | live `openapi.json`; beat crontab + container TZ |

### `docs/plans/` (13 + new data README)

| File | Verdict | What changed | Evidence |
|---|---|---|---|
| `2026-04-24-perf-audit.md` | **CORRECTED** | Had **no status header** → `✅ DONE`, all five commits, plus per-follow-up disposition | `git log`; DB size; `pg_extension` |
| `2026-04-24-zero-touch-key-rotation.md` | **CORRECTED** | Had **no status header** → `✅ DONE` (v2.63.6 / v1.3.25); §7 checklist's `update.sh` and "Pushover" lines corrected | `e4610b5`; `services/ntfy.py` |
| `2026-05-23-mobile-invoice-editor-ux.md` | **CORRECTED** | "Implemented" → implemented **but on-device sign-off still outstanding** (`O-15`) | its own §Verification |
| `2026-05-24-payment-reminders-dunning.md` | **CORRECTED** | "Approved (design); ready for implementation plan" → **shipped v2.67.7**; only Phase 2 (SMS) is still a design | the sibling `-plan.md` ship record |
| `2026-05-24-payment-reminders-dunning-plan.md` | CURRENT | already carries a SHIPPED banner | — |
| `2026-06-11-ground-up-audit.md` | **CORRECTED** | Added a per-finding disposition (P1-1 / P1-5 / P2-2 / P3-1 / P2-4/5 / P3-4); `main.py:414` version citation → `app/version.py` | ADRs 0021/0022/0023/0036; `version.py:19` |
| `2026-06-15-device-upload-async-decoupling.md` | **CORRECTED — headline** | Had **no status header** and read as open → **all stages DONE**, incl. the Stage-C fast-follow; drifted citations corrected; DroneOpsSync's own stale ADR flagged | live `openapi.json`; `c66931a` |
| `2026-07-03-dji-2027-fleet-longevity.md` | HISTORICAL-ANNOTATED | Still Proposed, re-confirmed; three questions still unanswered (`O-7`) | no decision recorded since |
| `2026-07-03-flight-attach-unification.md` | **CORRECTED** | Flat "Proposed" → **Phase 1 shipped (ADR-0038, v2.76.4); Phases 2–4 proposed** | ADR-0038; 584 ODL rows still present |
| `2026-07-03-migration-consolidation.md` | **CORRECTED** | Flat "Proposed" → **Phase 1 shipped (ADR-0036); Phase 2 partial; Phase 3 not started** | `db_migrations.py`; `main.py:67`/`:256` |
| `2026-07-03-report-quality.md` | **CORRECTED** | Flat "Proposed" → **§3.1–3.3 shipped (ADR-0035, v2.77.0); §3.4–3.6 gated on Bill (`O-3`)**; `ollama.py:9-67` citation flagged | ADR-0035; guard-suite test class |
| `2026-09-04-dji-log-untapped-data-census.md` | **CORRECTED** | "research record, nothing built" → **P0/P1 shipped and live**; P2–P7 not built | v2.82.0/v2.83.0; alembic head |
| `2026-09-04-flight-details-data-ingestion.md` | **CORRECTED** | Sweep annotations verified correct; added today's **re-derived counts** (the "226" was itself point-in-time) | DB queries |
| `data/README.md` | **NEW** | Created: documents both data files' column schemas (the TSV has **no header row**), what each is, and which scratch filenames were never committed | `head` of both files; line counts 585/28 |

### `docs/reports/` (4)

| File | Verdict | What changed | Evidence |
|---|---|---|---|
| `2026-09-05-fp1-log-recovery-hunt.md` | **CORRECTED** | Broken manifest path `missing_28_full.tsv` → the real committed path + its column schema, with the other scratch filenames named as never-committed; dated status note (both operator leads still open) | `ls docs/plans/data/` |
| `2026-09-11-dji-log-parser-upgrade-eval.md` | **CORRECTED** | Dated re-check: recommendation stands; **the exact-pin gap it names is still open** (`Cargo.toml` = `"0.5"`, `DJI_LOG_PARSER_VERSION` unchecked); states plainly that a fresh crates.io lookup was **not** performed | `Cargo.toml:12`; `dji.rs:7` |
| `2026-09-21-basemap-provider-eval.md` | **CORRECTED** | "Research only. No application code changed, nothing committed." → **acted on the same day**, shipped as ADR-0046 / v2.92.0 and live; header version note; first scheduled probe run 2026-09-28 | `72dd1a9`; live `openapi.json` |
| `2026-09-21-open-items-inventory.md` | **CORRECTED** (owned) | 3 coordinator fixes (probe date, MP-2 first run, demo 2.92.1) + closed the wrong device-upload row + **new §3.7 with 14 previously-uncaptured open items** + 12 new rows in §5 + sharpened the audience-gate evidence | see §2 below |

### `docs/incidents/` (3)

| File | Verdict | What changed | Evidence |
|---|---|---|---|
| `2026-05-14-mission-report-audience-leak.md` | **CORRECTED** | Header Status "**In-progress** (runtime soft-block gate)" → **CLOSED in full** — the gate is live in prod; fleet ADR-0037 note; `MissionReportEdit.tsx:262-272` → `:285-287`; guard suite 17 → 36 | `celery_tasks.py:166`/`:307`; prod columns present |
| `2026-06-05-cf-health-flap-external-bos-firewall.md` | CURRENT | Closed, external cause, "no DroneOps-side action" still correct | — |
| `2026-06-11-deploy-rename-conflicts-demo-bringup.md` | CURRENT | Closed; `docs/cloudflare-tunnel-setup.md` (its one repo reference) exists | `ls docs/` |

### `docs/superpowers/` (5)

| File | Verdict | What changed | Evidence |
|---|---|---|---|
| `plans/2026-05-03-v2.65.0-orchestration-plan.md` | **CORRECTED** | No status header + **0 of 34 boxes ticked** → read as NOT STARTED. Marked `✅ DONE` (v2.65.0), pointed at ADR-0009/0010/0011, plus a do-not-re-execute warning | ADRs; `git log` |
| `plans/2026-05-03-v2.67.0-mission-hub-orchestration-plan.md` | **CORRECTED** | Same shape: **0 of 40 boxes** → `✅ DONE` (v2.67.0), with the one genuinely-open residue (legacy wizard) named | ADR-0014; `App.tsx:133` |
| `postmortems/2026-04-13-droneops-outage-postmortem.md` | **CORRECTED** | §5 `CLAUDE.md` "Deploy Discipline" additions and §6 memory records **were never executed** — stated plainly instead of left to look done; what did change independently is credited | `grep -i "deploy discipline" CLAUDE.md` → nothing; no `deploy-logs/` |
| `specs/2026-05-03-deposit-and-tos-rebuild-design.md` | **CORRECTED** | "Approved" → approved **and implemented, v2.65.0**, with the decision records | ADR-0009/0010 |
| `specs/2026-05-03-mission-hub-redesign-design.md` | **CORRECTED** | "Approved" → approved **and implemented, v2.67.0**; its §Integration-matrix `scripts/snapshot.sh` row flagged as a retired lane | ADR-0014; ADR-0041 Amendment 2 |

### `docs/patches/` (1)

| File | Verdict | What changed | Evidence |
|---|---|---|---|
| `0075-cspublic-README.md` | **CORRECTED** | Dated re-verification: **still unapplied, still applies cleanly** against CallSignPublic HEAD `0570fdd` (moved from `6c22708`); the defect is still live; the third artifact is still UNVERIFIED | `git apply --check` clean; `grep` for the three symbols → no hits |

---

## 2. ADR index check (required deliverable)

- **Contiguity:** `0001`–`0046`, **no gaps, no duplicates.** Script-verified
  (`missing: NONE`, `dupes: NONE`). Next number is **`0047`**.
- **No ADR index existed**, so `docs/adr/README.md` was created with a 46-row table
  (number, title, status, date, superseded-by) generated from the files, plus the two
  number-collision traps and a cross-repo table whose ten noc-master ADRs were each
  confirmed present on disk.
- **Every `ADR-00NN` reference in the file set now resolves.** The only non-local
  numbers remaining (`0036`, `0037`, `0056`, `0079`, `0086`, `0194`, `0218`, `0232`,
  `0246`, `0247`) are all noc-master, all now named as such, and all verified to exist
  in `~/noc-master/docs/adr/`.
- **Every relative markdown link in the file set resolves** (`test -e` across all 75
  files → zero broken). The one broken link found repo-wide is in Part A's ledger
  (`…` placeholder) — flagged below.

### ADR status summary

| Status | Count | Which |
|---|---|---|
| Accepted, shipped, nothing outstanding | 36 | most of `0001`–`0046` |
| Accepted **with open work named in-file** | 8 | `0013` (4xx alert + smoke runbook + contract tier), `0014` (legacy wizard), `0019` (silent fallback), `0025` (pagination), `0032` (Litchi dispatch, shared units), `0036` (Phase 2/3), `0042` (in-backend demo reset), `0043` (P2–P7) |
| Accepted **in part, superseded in part** | 1 | `0028` — §H1 superseded by `0029`/`0031`; everything else in force |
| **Proposed** (genuinely undecided) | 1 | `0034` — awaiting Bill's Tier-0 decision (`O-4`) |
| Status line was **wrong** and is now fixed | 3 | `0023` (said Proposed, shipped both legs) · `0046` (said "deploy is the operator's push", deployed) · `0028` (Accepted → Accepted-except-§H1) |

---

## 3. For Part A / the orchestrator — outside my file set

1. **`docs/reports/2026-09-21-docs-freshness-ledger-A.md` has a broken relative link**
   — a literal `…` placeholder target (line ~59 region). It is the only broken link
   left in `docs/`.
2. **`backend/app/main.py`** carries two stale citations in `_create_hot_indexes`'
   docstring: `client_portal.py:159` (now `:154`) and, by inheritance, the same
   `business_signals.py:107` / `maintenance.py:505` pair (those two are still exact).
   Source-comment fix, not a docs fix.
3. **`backend/tests/test_db_migrations.py`** comments call the advisory lock
   **"ADR-0035"**; it is **ADR-0036**. Same residue as ADR-0042's Related list (which I
   fixed on the docs side).
4. **`BigBill1418/DroneOpsSync` `docs/adr/0008-device-upload-async-poll-client.md`**
   still reads "**Proposed** — design only, no code shipped" while the client leg has
   been live since v1.3.29. Different repo; needs the same one-line status fix.
5. **ROADMAP has no ID for the eleven items in inventory §3.7.** If Bill wants them
   tracked as first-class items rather than inventory rows, they need IDs allocated.
6. `ROADMAP.md` `MP-2`'s first-run date — Part A reported it corrected on their side;
   I independently re-derived the same answer (Mon **2026-09-28**) and applied it to
   the inventory.

## 4. Could not verify

- **`systemctl --user is-enabled droneops-backup-cutover.timer`** on droneops-server —
  this agent's environment has no user D-Bus (`Failed to connect to bus: No medium
  found`). The unit **files** are present and un-removed at
  `~/.config/systemd/user/`, consistent with "disabled, not removed"; the disabled
  state itself is carried from the cutover run's own verification. Stated as such in
  ADR-0041 rather than asserted.
- **Whether a `dji-log-parser` release newer than `0.5.7` has appeared since
  2026-09-11.** That needs a crates.io lookup; this pass did not perform one. The
  local pin and the unguarded `DJI_LOG_PARSER_VERSION` constant were verified and are
  what matters for the open item.
- **`P7-6` (post-deploy client-IP check)** — structurally impossible from a session:
  `droneops.barnardhq.com` is behind Cloudflare Access, so an agent request gets a 302
  to the login page and never reaches the app. Unchanged from the inventory's own
  finding; it is `O-13`.
- **The three-day post-cutover backup freshness watch (`O-16`, closes 2026-09-24)** is
  passive and not yet elapsed.
