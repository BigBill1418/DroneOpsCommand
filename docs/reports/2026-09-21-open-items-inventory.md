# DroneOpsCommand — open items and operator to-do, as of 2026-09-21

**Date:** 2026-09-21 (times Pacific — BOS-HQ and the other fleet hosts have run
`America/Los_Angeles` since 2026-08-25)
**Live:** app **2.92.0**, flight-parser **1.2.0**, alembic head
`0011_battery_src_truth`
**Supersedes:** every earlier "what's open" list in this repo. `ROADMAP.md`
remains the durable record per item; this is the snapshot with today's closures
already applied.

**How this was built.** Every claim below was checked against the running system
or the file it cites — not against prose. Where a prior pass's inventory named
an item, it was re-verified before being reused; where it could not be
substantiated, it was dropped and that is noted in §4.

---

## 1. Shipped today (2026-09-21)

- **Phase 7 customer-surface hardening — v2.91.0 (ADR-0045).** Merged by Bill at
  **`d30eb5b`, 13:53 PDT**; the fleet deployer built and recreated the stack and
  **v2.91.0 was live on BOS-HQ at 13:58 PDT**. The same-day one-hop trusted-proxy
  correction (`a226c93`) is in the merged range. 795 passed / 17 skipped.
- **Keyless Esri basemap registry + tile-health probe — v2.92.0 (ADR-0046).**
  `72dd1a9`, **deployed 14:52 PDT**. Live backend reports 2.92.0; the served
  leaflet chunk carries the Esri endpoints and **zero `cartocdn` across all 44
  chunks**; a probe run inside `droneops-worker-1` returned `ok: true,
  layers_ok: 5`. Probe **ntfy is OFF by design** (MP-2). First *scheduled* probe
  run: **2026-09-22 15:47 UTC**.
- **ADR-0041 §5.7 backup cutover — EXECUTED ~14:55 PDT** by
  `scripts/droneops-backup-cutover.sh` (`d153623`). Legacy cron line removed
  (**CallSign's line at `30 3 * * *` untouched** — verified), plaintext
  `s3://obs-glitchtip-backups/droneops/` prefix deleted (~2.3 GiB / 229
  objects), `scripts/snapshot.sh` retired. **The deleted prefix still exists in
  immutable Backblaze B2** — the fleet r2-mirror lane (noc-master ADR-0232) had
  already copy-forwarded it under Object Lock compliance, 90 days, never pruned.
- **Also (not a release):** the demo stack `~/droneops-demo` on BOS-HQ was
  updated **by hand at 14:57 PDT, v2.80.4 → v2.92.0** (it is not
  deployer-managed). `cloudflared`/`db`/`redis` untouched; demo worker + beat
  remain deliberately stopped (dunning-email hazard, ADR-0042). Verified: demo
  backend reports 2.92.0, three rebuilt containers healthy, zero `cartocdn`.

**Closed by the above:** Phase 7 merge + deploy; the §5.7 cutover; **OBS-3**
(`NTFY_DRONEOPS_PUBLISHER_TOKEN` is set in BOS-HQ `~/droneops/.env` — verified).
Maps shipped; `MP-1`/`MP-2`/`MP-3` remain open by design.

---

## 2. Operator to-do — Bill only

Nothing in this section can be done from a session. Each line names the file and
line that carries the detail.

### 2.1 Urgent / time-critical

| # | Action | Why now | Source |
|---|---|---|---|
| O-1 | **Check the M4TD / M30T controllers and the Mavic 3 Pro for surviving `FlightRecord` folders.** Paths: `/storage/emulated/0/Android/data/dji.go.v5/files/FlightRecord`, `/storage/emulated/0/DJI/com.dji.industry.pilot/FlightRecord`, `/storage/emulated/0/Android/data/com.dji.fly/files/FlightRecord`, `/storage/emulated/0/DJI/dji.go.v4/FlightRecord`. Look for names dated **2026-03-22 → 2026-04-19**. | **The single highest-probability surviving copy of the 28 lost `dji_txt` originals.** v1.3.23 only deleted the controller original on an operator-confirmed dialog, and per ADR-0006 the RC Pro 2's SAF grant lacked WRITE before v1.3.28, so those deletes were *silently failing* — surviving copies from that era are **more** likely, not less. | `docs/reports/2026-09-05-fp1-log-recovery-hunt.md:189-206` |
| O-2 | **Synology Active Backup search for the 28 lost originals — do it BEFORE the NEXTL3VEL PC rejoins the backup task.** DSM → Active Backup for Business → Restore → task `bbarnard065-Default` → Restore files/folders. Version `2026-06-07 04:30` first, then back. Search `DJIFlightRecord_` **and** `FlightRecord_` separately, `.txt` only. | Time-critical: rejoining the task starts retention ageing out the versions that could still hold them. **Honest prior: this lead is weak** — NEXTL3VEL was never in the ingest path, and two Downloads archives from that PC (2026-05-07 and 2026-08-12) contain **no raw `.txt` flight logs at all**. Do it because it is cheap and expiring, not because it is likely. | `docs/reports/2026-09-05-fp1-log-recovery-hunt.md:142-168`, `:175-188` |

### 2.2 Decisions Bill owes

| # | Decision | Source |
|---|---|---|
| O-3 | **Report-quality direction.** `docs/plans/2026-07-03-report-quality.md` §7 asks one yes/no: approve a **single low-risk prompt pass** (3.1 voice authority + 3.2 anti-bloat + 3.3 number-grounding), validated by the golden-report diff + the existing audience-leak detector, then react before deciding on the routine-flight variant and real-estate register. The standing ROADMAP item is explicit that no session should pick a direction unasked. | `docs/plans/2026-07-03-report-quality.md:167-172`; `ROADMAP.md:410` (`FU-AI-QUALITY-PASS`) |
| O-4 | **ADR-0034 is still `Status: Proposed`.** Ship Tier 0 — operator-pasted deliverable URLs on the mission, rendered in the existing client portal (one Alembic revision adding `missions.map_share_url`, a few hours, no integration)? The ADR itself recommends yes. | `docs/adr/0034-mission-map-stream-cross-system-linkage.md:3`, `:187-190` |
| O-5 | **153 of 226 `dji_txt` rows are legacy** and carry pre-ADR-0027/0028 duration and distance. D5 freezes those fields, so they stay stale unless something reprocesses. The error is small (6 rows off by >1 s; +35.7 s total across the 125 measured) and leaving it is defensible — **but it should be a decision, not an oversight.** Reprocess or accept? | `PROGRESS.md:203` |
| O-6 | **`~/backups/n8n_*.sqlite` on droneops-server** (~840 MB, root-owned, last written 2026-04-15) — keep or delete? The final n8n snapshot is already in the restic repo under tag `legacy-n8n`, so this copy is redundant; deleting someone's data is not an agent call. | `ROADMAP.md:245` (`BK-4`); `PROGRESS.md:895` |
| O-7 | **DJI 2027 fleet longevity — three open questions:** spares budget & count per model; whether a public-safety/DFR deal is real near-term pipeline or aspirational; risk appetite on running security-frozen airframes past 2029 if the waiver lapses. | `docs/plans/2026-07-03-dji-2027-fleet-longevity.md:196-203` |
| O-8 | **Do we want a CAPTCHA on `/api/intake/form/{token}`?** It needs a new **Cloudflare Turnstile site key** (dashboard action). Deliberately not added in Phase 7: the 256-bit `secrets.token_urlsafe(32)` intake token already makes brute-force guessing infeasible, so this is lower-priority than the original audit line reads. Recorded, not silently dropped. | `docs/adr/0045-phase7-customer-surface-hardening.md:219-225` |

### 2.3 Keys, dashboards and assets

| # | Action | Source |
|---|---|---|
| O-9 | **Confirm `droneops-alerts` is subscribed on Bill's phone before MP-2 arms the probe's ntfy.** A topic nobody has subscribed to is a black hole — server-side subscription is impossible, it is a ~30-second phone action. The probe deliberately reuses the existing topic rather than minting `droneops-basemap` for exactly this reason. | `ROADMAP.md:66-70` |
| O-10 | **Supply a `dji_m4t_official.png` asset** for the new Matrice 4T fleet tile. The aircraft row exists with `image_filename` NULL, so the tile renders without an image. | `PROGRESS.md:737` |
| O-11 | **Grafana: fix the `obs-rule-droneops-backup-stale` description text.** It still tells the operator to `tail ~/droneops/backups/snapshot.log` and re-run `snapshot.sh` — **both gone as of today's cutover**. Replace with `journalctl -u droneops-backup.service -n 50` + `sudo systemctl start droneops-backup.service`. **Change the `description` only; metric names and expressions are a hard contract.** Lives in `~/noc-master` (`/opt/infrawatch/grafana/provisioning/alerting/observability-alerts.yml`), which is why the cutover script could not do it. **Now unblocked.** | `ROADMAP.md:225` (`BK-3`) |
| O-12 | **CS-Public patch rollout — strict order, or archive search goes down.** `docs/patches/0075-cspublic-*` is a **patch, not a commit**, and needs a worktree in `~/repos/CallSignPublic`. Order: (1) set `search.worker_origin_token` on the **origin** via the Settings UI and deploy — the origin 503s **all** search briefly, expected; (2) `wrangler secret put ORIGIN_SEARCH_SECRET` with the **same** value in `worker/api/` and `wrangler deploy`; (3) verify through `cs.barnardhq.com`. Deploying without both secrets **takes down archive search entirely, including the legitimate path** — fail-closed by design. | `docs/patches/0075-cspublic-README.md:66-84` |

### 2.4 Verification Bill has to perform

| # | Check | Why only Bill | Source |
|---|---|---|---|
| O-13 | **P7-6 — post-deploy client-IP check.** Open `droneops.barnardhq.com` in a browser, then `ssh 10.99.0.4 'docker logs --tail 50 droneops-frontend-1'` and confirm the resolved-client audit field shows **his own public IP**, not the constant `172.19.0.11`. | The app sits behind **Cloudflare Access**: any agent-side request gets a 302 to the Access login page and never reaches the app, so **only an authenticated browser session produces a real external-IP log line.** Attempted and confirmed blocked today. | `docs/adr/0045-phase7-customer-surface-hardening.md:336`; `PROGRESS.md:113-121` |
| O-14 | **Annual cold, 1Password-only DR rehearsal — overdue since ~2026-08-17.** Rebuild from the 1Password Fleet items and the R2 bucket only, reading nothing from BOS-HQ. The quarterly drill **cannot** detect a mis-filed secret, because it never reads 1Password; only this rehearsal shape can. | Requires the 1Password vault. | `docs/adr/0041-comprehensive-encrypted-backup-to-r2.md:664-667` |
| O-15 | **Mobile invoice-editor — final on-device confirmation.** Playwright screenshots at 390/412/768/1280 px are clean and `tsc` is green; the operator sign-off on a real phone is the outstanding half. | Needs the physical device. | `docs/plans/2026-05-23-mobile-invoice-editor-ux.md:48-49` |
| O-16 | **Post-cutover freshness watch closes 2026-09-24.** Confirm `droneops_backup_last_success_timestamp_seconds` keeps advancing for three days after the cutover before declaring §5.7 fully done. Passive — the deadman alert covers it. | — | `PROGRESS.md:939` |

---

## 3. Open roadmap items, by area

Every ID below exists in `ROADMAP.md`. Status is as of 2026-09-21.

### Maps (ADR-0046)

| ID | Title | Status | What unblocks it | Source |
|---|---|---|---|---|
| `MP-1` | Protomaps PMTiles self-hosted on R2 | NOT STARTED | Esri changes terms / watermarks / gates behind a key, or MP-2's probe reports a provider-side change. Also worth starting if the soft dark base above z16 becomes a real complaint. ~1–2 eng-days; the swap is one line in `frontend/src/lib/basemaps.ts`. | `ROADMAP.md:14` |
| `MP-2` | Tune the probe thresholds, then arm ntfy | NOT STARTED, **earliest 2026-10-05** | Two weeks of weekly runs (first 2026-09-22), then read the accumulated `system_settings.basemap_probe_last_result` history and either widen the thresholds to observed variance + headroom or confirm them. Then `PUT /api/admin/basemap/tile-health/ntfy {"enabled": true}` — **after** O-9. | `ROADMAP.md:43` |
| `MP-3` | Re-measure the Esri zoom ceilings | NOT STARTED | DroneOps starts flying materially outside the Willamette Valley. Every `maxNativeZoom` was measured over Eugene; Esri's ceilings are location-specific and past the real ceiling every service returns a byte-identical blank filler tile, not a 404 — so the failure mode is a blank map. | `ROADMAP.md:71` |

### Flight-parser data expansion

| ID | Title | Status | What unblocks it | Source |
|---|---|---|---|---|
| `FP-1 P2` | Tier 1 records + the `SmartBatteryStatic` `>> 8` shim | NOT STARTED | **The one hard gate: peak RSS on a real full-length log against the parser's `mem_limit: 256m`, still unmeasured.** The P-EVAL harness is explicitly not a proxy — it holds two frame vectors and two track copies. Plus Bill's call on the four §8 follow-ups. Known constraint: `raw >> 8` recovers the true value only while the top byte is zero, so `loop_times` **silently wraps at 256 cycles** and the `0..=3000` plausibility gate cannot catch it. | `ROADMAP.md:89` |
| `FP-1 P3–P6` | Backfill → repair → UI → battery | NOT STARTED | Sequential on P2. P4 and any `force` re-backfill default to `dry_run=true`. | `ROADMAP.md:89` |
| `FP-1 P7` | ODL re-import over `opendronelog_import` rows | NOT STARTED, **no longer blocked** | **Correction:** this was recorded as blocked on the §8 log-inventory hunt. That hunt was filled in 2026-09-04/05. P7 now waits only on P2–P6. Expect **four** `Unknown(NNN)` placeholders from P4(b)'s `^Unknown\(\d+\)$` predicate, not three — `Unknown(150)` (Matrice 4T) exists on 39 recovered logs. | `ROADMAP.md:89`; `docs/reports/2026-09-05-fp1-log-recovery-hunt.md` |
| — | Pin the crate exactly (`= "0.5.7"`) and guard `DJI_LOG_PARSER_VERSION` against `Cargo.lock` | Recommended, not done | `Cargo.toml` requests `"0.5"`, so a `cargo update` could move it and bypass ADR-0043 **D6** without a decision. `DJI_LOG_PARSER_VERSION` is stamped on every `flight_details` row as the provenance D6's "re-backfill below version X" query depends on, and nothing keeps it honest. | `PROGRESS.md` § 2026-09-11 "Open for Bill" |

### Backup + DR

| ID | Title | Status | What unblocks it | Source |
|---|---|---|---|---|
| `BK-1` | PITR via `pg_receivewal` | DEFERRED | Write volume grows ~10× **or** the RPO requirement drops below 12 h. Neither holds: real change is ~1 MB/day. **Explicitly not the path:** re-enabling `archive_command` into pgdata. | `ROADMAP.md:208` |
| `BK-3` | Grafana `obs-rule-droneops-backup-stale` description text | **OPEN, unblocked today** | Nothing — do it. See O-11. ~15 minutes, in `~/noc-master`. | `ROADMAP.md:225` |
| `BK-4` | `~/backups/n8n_*.sqlite` disposal | OPEN | Bill says keep or delete. See O-6. | `ROADMAP.md:245` |

### Observability + fleet hygiene

| ID | Title | Status | What unblocks it | Source |
|---|---|---|---|---|
| `FU-1` | Fleet APK version audit | DE-PRIORITIZED | Optional hygiene only. ADR-0002 §5's silence watchdog + layer-1 banner already make fleet drift self-detecting. | `ROADMAP.md:264` |
| `FU-3` | Grafana stale-client tripwire | NOT STARTED | Nothing — v2.63.4 is live and the WARN stream exists. **Must be built on ntfy, not Pushover** (topic prefix `droneops`, click `https://noc-mastercontrol.barnardhq.com/status/droneops`) and graded against the ADR-0037 five-question gate before it pages. ~0.5 eng-day. | `ROADMAP.md:283` |
| `FU-4` | Device-key lifecycle policy | NOT STARTED | Before the first real managed tenant ships. Decide: 90-day TTL with a 7-day dual-key grace, or stay revoke-on-demand. | `ROADMAP.md:302` |
| `FU-5` | Managed-tenant discovery (EyesOn ADR-0020 parity) | NOT STARTED | First DroneOps managed customer signed. Copy-paste-with-rename from the EyesOn repos; 1–2 eng-days. | `ROADMAP.md:315` |
| `FU-6` | End-to-end test for the `device-upload` auth path | NOT STARTED | Nothing — good hygiene any time. The auth dependency has unit coverage; the full upload pipeline is untested end-to-end. ~0.5 eng-day. | `ROADMAP.md:331` |

### LLM report surface

| ID | Title | Status | What unblocks it | Source |
|---|---|---|---|---|
| `FU-AI-3` | Prompt source-of-truth relocation | DE-PRIORITIZED | Bundle it the next time the prompt is meaningfully edited. Pure refactor, no behavioural coverage; not worth a standalone pass. | `ROADMAP.md:365` |
| `FU-AI-4` | Per-tenant prompt override | NOT STARTED | First managed-hosting customer asks for a different voice. Not before — premature flexibility. The ADR-0015 audience invariant stays hard-coded and non-overridable. | `ROADMAP.md:387` |
| `FU-AI-QUALITY-PASS` | Mission-report quality iteration | NOT STARTED (watching brief) | **O-3.** Operator-driven by design: do not assume a direction and start editing the prompt. | `ROADMAP.md:410` |

### Billing, hygiene, and FU-8 residue

| ID | Title | Status | What unblocks it | Source |
|---|---|---|---|---|
| `BL-1` | Formula/markup support for pass-through rate templates | NOT STARTED | Operator asks for computed markups, or a second formula-shaped rate appears. One at-cost template does not justify the schema + UI work. | `ROADMAP.md:191` |
| `H-1` | Parity test for the compose `APP_VERSION` defaults | NOT STARTED (**new, this pass**) | Nothing. The five compose defaults sat at `2.67.3` for ~25 minor versions with nothing checking them; they tag the **Sentry/GlitchTip release** on both halves and drive the Login/Setup footers. Defaults fixed to 2.92.0 today, **but the host `.env` on BOS-HQ overrides them and is itself stale (`APP_VERSION=2.67.4`)** — so production is still mis-tagged until that is fixed too. ~1 hour. | `ROADMAP.md:452` |
| — | Device-upload Celery decoupling (audit P2-2, full leg) | DESIGNED, not started | A DroneOpsSync client release. **Recommended fast-follow:** ship the DroneOpsSync socket-timeout-is-per-file fix standalone (one line, backend-independent) ahead of the full async route. | `ROADMAP.md` § FU-8; `docs/plans/2026-06-15-device-upload-async-decoupling.md` |
| — | Trigram (`pg_trgm`) indexes for leading-wildcard ILIKE search | Rejected, revisit-if | Flight search slows at scale. B-tree cannot serve leading wildcards, which is why it was rejected from migration `0002`. | `ROADMAP.md` § FU-8 |
| — | Companion APK instrumentation (`SentryAndroid.init`) | OPEN | **Note the original path is dead** — `companion/` was deleted from this repo in `4b87e65`; the real companion is `BigBill1418/DroneOpsSync` (native Kotlin). Work happens there, not here. | `PROGRESS.md` § Follow-ups |
| — | **No pytest and no cargo job in CI** | Standing gap, no ID | `.github/workflows/` holds only `auto-merge-claude.yml`, `secret-scan.yml`, `self-hosted-smoke-test.yml`. **Nothing runs the test suites at merge time**, so every "tests pass" claim in this repo's history is a local, hand-quoted number with nothing enforcing it. Documented in CLAUDE.md; not currently a ROADMAP item. | `CLAUDE.md` § "Tests & CI" |

---

## 4. What the prior inventory claimed that could not be substantiated

Recorded so the next session does not re-import it. A read-only pass that
preceded this one produced an item list using IDs `BK-4`–`BK-8`, `OBS-1`/`OBS-2`,
`CI-1`, `RQ-1`/`RQ-2`, `PARSE-1`/`PARSE-2`, `MIG-2`/`MIG-3`, `ATT-2`–`ATT-4`,
`ASY-A`–`ASY-E`, `TRG-1`, `DL-1`, `DEMO-1`/`DEMO-2`, `FLEET-1`, `UX-1`, `SER-1`,
`MNT-1`, `MAP-0`/`MAP-1` and `P7-2`–`P7-5`.

**A `grep` across every `.md`, `.py`, `.ts` and `.sh` in the repo returns zero
hits for any of them.** They were not renamed and they were not removed — they
never existed here. The real IDs are the ones in §3.

Three of those labels were kept, because the *underlying work* is real and
verifiable even though the ID was not:

- **`BK-3`** — the Grafana rule description. Real (`PROGRESS.md` § "Also at
  cutover"; ADR-0041 residual 2). Now a ROADMAP entry under that ID.
- **`BK-4`** — the n8n sqlite disposal. Real (ADR-0041 residual 4). Now a
  ROADMAP entry under that ID.
- **`P7-6`** — the post-deploy client-IP check. Real (ADR-0045 verification
  section). Tracked as **O-13** above and as a note in the ADR.
- **`OBS-3`** — `NTFY_DRONEOPS_PUBLISHER_TOKEN`. Real, and **closed**: verified
  set in BOS-HQ `~/droneops/.env`.
- **`BK-CUT`** — the §5.7 cutover. Real, and **closed** today. It was never a
  ROADMAP item; it lived in `PROGRESS.md`.

Everything else on that list was dropped. `DEMO-2` (the CHAD-HQ demo clone,
still on `dfad0a3`) is a real open thing but has no ID and no ROADMAP entry —
noted here so it is not lost.

---

## 5. Things that look open and are not

Checked today against the running system, because each of these reads as
outstanding in at least one document:

| Reads as open | Actually | Evidence |
|---|---|---|
| Phase 7 "awaiting operator merge" | Merged `d30eb5b` 13:53 PDT, v2.91.0 live 13:58 PDT | live `openapi.json`; git log |
| Maps "SHIPPED, pending deploy" | v2.92.0 live 14:52 PDT | live `openapi.json` → `2.92.0` |
| ADR-0041 "§5.7 cutover was not executed" | Executed ~14:55 PDT | BOS crontab holds only the CallSign + demo-reset lines |
| `FU-AI-RUNTIME-GATE` "no deploy yet" | Deployed since `4953edf` (2026-05-14) | `_apply_audience_findings` at `celery_tasks.py:307`; `reports.has_audience_leak` + `reports.audience_leak_details` both present in the production DB |
| ADR-0043 "implementation PLANNED, not started" | P0 + P1 live (v2.82.0, v2.83.0 + parser 1.2.0) | alembic head `0011_battery_src_truth` |
| FP-1 "P7 BLOCKED on the log-inventory hunt" | Hunt closed 2026-09-04/05 | `docs/reports/2026-09-05-fp1-log-recovery-hunt.md` |
| 2026-05-03 "v2.66.0 IN-FLIGHT, awaiting orchestrator merge" | Shipped 2026-05-03; product is 26 minor versions past it | ADR-0013 opens by describing an incident five hours after v2.66.0 shipped |
| 2026-04-24 "awaiting operator action on 3 pending flight records" | Closed — `M4TD.last_used_at` = **2026-09-21 20:03 UTC**, far past the `2026-04-19 23:07:44` this item named as its success signal | production `device_api_keys` |
| ADR-0041 "7-year yearly retention — confirm the posture" | Answered 2026-08-18: `KEEP_YEARLY=unlimited` | CHANGELOG 2026-08-18 |
| ADR-0041 "legacy volumes — keep or remove?" | Removed 2026-08-18, archived first (snapshot `66ed2135`) | CHANGELOG 2026-08-18 |
| `.deployer-disabled` "this repo is not auto-deployed" | **Never true.** Nothing in the fleet deployer reads that marker | ADR-0018; CLAUDE.md § Deployment topology |
| "deploy via `update.sh`" | `update.sh` deleted in `e4610b5` | CLAUDE.md § Branch Workflow |
