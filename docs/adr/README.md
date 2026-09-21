# Architecture Decision Records — index

**Generated 2026-09-21** from the files in this directory, as part of the
exhaustive docs-freshness pass. Every row's status was read from the ADR's own
`Status:` line after that pass reconciled it against the running system.

- **Numbering:** `0001`–`0046`, **contiguous, no gaps, no duplicates** (verified
  `2026-09-21`). The next ADR is **`0047`**.
- **Repo state when this index was generated:** live app **v2.92.1**,
  flight-parser **1.2.0**, alembic head **`0011_battery_src_truth`**, `main` at
  `28b9c98`.
- **Keep this table current.** When you add or re-status an ADR, add or edit its
  row here in the same commit.

---

## Two traps that cost real time in this directory

**1. `ADR-0036` and `ADR-0037` are ambiguous in older documents.**
Between 2026-04-25 and 2026-07-03 this repo had no local ADR-0036 or ADR-0037,
so a dozen documents wrote bare `ADR-0036` / `ADR-0037` meaning the **fleet**
(noc-master) ADRs — *ntfy transport* and *notification-noise policy*. On
2026-07-03 those numbers were taken locally by **migration single-path
hardening** and **airspace/LAANC**. Anything dated before 2026-07-03 that says
`ADR-0036`/`ADR-0037` almost certainly means the fleet ones; the affected files
(0009, 0011, 0012, 0015, 0027, 0041, 0042, 0046, the 2026-05-14 incident and the
basemap eval) now carry a dated disambiguation note. Files written *after*
2026-07-03 that mean the local ones — 0039, 0040 — are correct as written.

**2. The advisory-lock ADR is `0036`, not `0035`.**
[ADR-0038](0038-flight-attach-unification-phase1-live-aircraft.md)'s header
records a number reshuffle that did not land as planned. Residue of it survives
in `backend/tests/test_db_migrations.py`'s comments and in ADR-0042's *Related*
list, both of which call the advisory lock "ADR-0035". **ADR-0035 is report
narrative quality.**

---

## Index

| # | Title | Status | Date | Superseded / amended by |
|---|---|---|---|---|
| [0001](0001-observability.md) | Observability: structured JSON logging + Sentry/GlitchTip + OpenTelemetry | Accepted | 2026-04-18 | — *(topology + release-tag source moved; dated note in file)* |
| [0002](0002-droneopssync-upload-auth.md) | DroneOpsSync upload auth model + HTTPS-only base URL | Accepted — shipped (backend v2.63.4/.5) | 2026-04-24 | Transport → [0006](0006-pushover-to-ntfy-migration-addendum.md); `companion/` deleted `4b87e65` |
| [0003](0003-zero-touch-device-key-rotation.md) | Zero-touch device API key rotation | Accepted — shipped v2.63.6 | 2026-04-24 | Transport → [0006](0006-pushover-to-ntfy-migration-addendum.md); migration path → [0022](0022-alembic-adoption-and-health-gate-trim.md) |
| [0004](0004-perf-audit-baseline.md) | Performance audit baseline | Accepted — all 4 fixes shipped | 2026-04-24 | — |
| [0005](0005-perf-audit-results.md) | Performance audit results | Accepted — all thresholds met | 2026-04-24 | — |
| [0006](0006-pushover-to-ntfy-migration-addendum.md) | Pushover → ntfy transport migration (addendum) | Accepted | 2026-04-25 | — |
| [0007](0007-strict-fleet-attribution-matcher.md) | Strict fleet-attribution matcher (no fuzzy fallback) | Accepted | 2026-05-01 | **Amended by [0044](0044-serial-prefix-matcher-odl-canonical-serials.md)** |
| [0008](0008-customer-payment-gated-on-mission-completion.md) | Customer payment gated on mission completion | Accepted — v2.64.0 | 2026-05-02 | Visibility extended by [0009](0009-deposit-feature.md) §4 |
| [0009](0009-deposit-feature.md) | Two-phase invoice billing (deposit + balance) | Accepted — v2.65.0 | 2026-05-03 | — *(the "no Alembic" premise is stale; see note in file)* |
| [0010](0010-tos-acceptance-acroform.md) | TOS acceptance via PDF AcroForm fill + SHA-256 anchor | Accepted | 2026-05-03 | — *(supersedes the canvas-signature flow for new acceptances)* |
| [0011](0011-payment-idempotency-and-invoice-numbering.md) | Payment idempotency, sequential invoice numbering, webhook-signature alerting | Accepted — v2.66.0 | 2026-05-03 | — |
| [0012](0012-secret-hygiene-and-leak-remediation.md) | Secret hygiene and leak remediation | Accepted | 2026-05-03 | — |
| [0013](0013-customer-flow-contract-tests-4xx-burst-alerting.md) | Customer-flow contract tests + 4xx-burst alerting | Accepted — **decisions §2 and §3 never built** | 2026-05-03 | — *(open; see dated note in file)* |
| [0014](0014-mission-hub-redesign.md) | Mission Hub redesign — Hub + Facet pattern | Accepted — v2.67.0 | 2026-05-03 | — *(legacy wizard still on disk; deletion criteria never evaluated)* |
| [0015](0015-mission-report-audience-separation.md) | Mission reports are client-facing; operator coaching is out of scope | Accepted | 2026-05-14 | Prompt extended by [0029](0029-mission-reports-are-client-deliverables-not-compliance-audits.md), [0031](0031-odl-max-altitude-is-verified-remove-unverified-peak-caveat.md), [0035](0035-report-narrative-quality-levers.md) |
| [0016](0016-mission-source-attribution.md) | Lead-source attribution on missions | Accepted | 2026-05-25 | Extended by [0024](0024-financials-summary-customer-contact-for-marketing-review-engine.md); migration mechanism → [0022](0022-alembic-adoption-and-health-gate-trim.md) |
| [0017](0017-flight-date-operator-timezone.md) | Flight calendar dates are operator-local, not UTC | Accepted | 2026-06-02 | — |
| [0018](0018-deploy-path-is-noc-fleet-deployer.md) | Deploy path is the NOC fleet deployer; per-repo autopull retired | Accepted | 2026-06-02 | — *(the canonical deploy-path record for this repo)* |
| [0019](0019-flight-library-list-defers-heavy-json-columns.md) | Flight-library list defers heavy per-flight JSON columns | Accepted — v2.68.5 | 2026-06-10 | OOM family continues in [0020](0020-report-geo-buffer-oom.md)/[0025](0025-large-mission-flight-handling-oom-and-bulk-attach.md) |
| [0020](0020-report-geo-buffer-oom.md) | Report generation OOM: simplify GPS tracks before buffering | Accepted | 2026-06-11 | Extended by [0025](0025-large-mission-flight-handling-oom-and-bulk-attach.md) A3, [0026](0026-duplicate-flight-attachment-and-report-metric-accuracy.md) §5 |
| [0021](0021-startup-recovery-guard-and-hot-indexes.md) | Startup schema-write recovery guard + hot-path indexes | Accepted | 2026-06-11 | Its "Alembic future work" → [0022](0022-alembic-adoption-and-health-gate-trim.md) |
| [0022](0022-alembic-adoption-and-health-gate-trim.md) | Alembic adoption (baseline + brownfield stamp), deferred indexes, health-gate trim | Accepted | 2026-06-11 | Hardened by [0036](0036-migration-single-path-hardening.md); idempotency rule from [0042](0042-fresh-install-integrity-and-demo-hygiene.md) |
| [0023](0023-device-upload-async-celery-decoupling.md) | Device-upload async decoupling (Celery + status poll) | **Accepted — shipped both legs** (backend v2.71.0, DroneOpsSync v1.3.29) | 2026-06-15 | Hardened by its own §6 (v2.72.1 / v2.72.2) |
| [0024](0024-financials-summary-customer-contact-for-marketing-review-engine.md) | Customer contact fields on `/api/financials/summary` | Accepted | 2026-06-20 | Extends [0016](0016-mission-source-attribution.md) |
| [0025](0025-large-mission-flight-handling-oom-and-bulk-attach.md) | Large-mission flight handling: kill the GPS-track OOM at source + bulk attach | Accepted — v2.73.0 | 2026-06-29 | Gap closed by [0026](0026-duplicate-flight-attachment-and-report-metric-accuracy.md) |
| [0026](0026-duplicate-flight-attachment-and-report-metric-accuracy.md) | Duplicate flight attachments + accurate, unit-correct metrics | Accepted — v2.74.0 | 2026-06-29 | Altitude-commentary conclusion superseded by [0029](0029-mission-reports-are-client-deliverables-not-compliance-audits.md) |
| [0027](0027-dji-duration-and-flight-name-correction.md) | DJI duration from header airtime; auto flight names unique + start-ordered | Accepted | 2026-06-29 | — |
| [0028](0028-flight-data-integrity-outlier-gate-transaction-and-truthfulness.md) | Flight-data integrity: outlier gate, transaction safety, race-safe dedup, live-scalar reporting | **Accepted EXCEPT §H1, which is superseded** | 2026-06-29 | **§H1 → [0029](0029-mission-reports-are-client-deliverables-not-compliance-audits.md); its residue → [0031](0031-odl-max-altitude-is-verified-remove-unverified-peak-caveat.md)** |
| [0029](0029-mission-reports-are-client-deliverables-not-compliance-audits.md) | Mission reports are client deliverables, not compliance audits | Accepted — v2.76.1 | 2026-06-29 | Supersedes [0028](0028-flight-data-integrity-outlier-gate-transaction-and-truthfulness.md) §H1 |
| [0030](0030-report-output-token-caps-full-after-action-reports.md) | Report output-token caps: full after-action reports must complete | Accepted — v2.76.2 | 2026-06-29 | — |
| [0031](0031-odl-max-altitude-is-verified-remove-unverified-peak-caveat.md) | ODL max-altitude is a verified achieved peak; remove the "unverified peak" caveat | Accepted | 2026-06-30 | Closes the residue left by [0029](0029-mission-reports-are-client-deliverables-not-compliance-audits.md) |
| [0032](0032-flight-parser-unit-correctness-shared-conventions.md) | Flight-parser unit correctness (voltage/speed/altitude) | Accepted | 2026-07-02 | — *(both named follow-ups still open)* |
| [0033](0033-avata2-missing-from-report-incident.md) | Avata 2 missing from client report — RCA, fix, data remediation | Accepted | 2026-07-03 | Root cause structurally closed by [0038](0038-flight-attach-unification-phase1-live-aircraft.md) |
| [0034](0034-mission-map-stream-cross-system-linkage.md) | Minimal cross-system linkage — Mission ↔ Map job ↔ EyesOn stream | **Proposed — awaiting Bill's Tier-0 decision** | 2026-07-03 | — |
| [0035](0035-report-narrative-quality-levers.md) | Client-report narrative quality: authority, signal density, number grounding | Accepted — v2.77.0 | 2026-07-03 | — *(levers §3.4–3.6 gated on operator reaction)* |
| [0036](0036-migration-single-path-hardening.md) | Migration single-path hardening: advisory-lock the Alembic boot path | Accepted | 2026-07-03 | Hardens [0022](0022-alembic-adoption-and-health-gate-trim.md); Phase 2 partial, Phase 3 not started |
| [0037](0037-airspace-laanc-awareness-at-mission-creation.md) | Airspace / LAANC awareness at mission creation (operator-facing) | Accepted | 2026-07-03 | — *(hard boundary: never in the client report, per [0029](0029-mission-reports-are-client-deliverables-not-compliance-audits.md))* |
| [0038](0038-flight-attach-unification-phase1-live-aircraft.md) | Flight-attach unification, Phase 1 — resolve report aircraft from the live flight | Accepted — v2.76.4 | 2026-07-03 | Phases 2–4 remain proposed |
| [0039](0039-unpaid-invoice-download-link-gate.md) | Unpaid-invoice download-link gate (with per-report override) | Accepted — v2.79.0 | 2026-07-05 | Completed by [0040](0040-automated-download-link-delivery-on-payment.md) |
| [0040](0040-automated-download-link-delivery-on-payment.md) | Automated download-link delivery on payment-in-full | Accepted — v2.80.0/.1 | 2026-07-06 | Completes [0039](0039-unpaid-invoice-download-link-gate.md) |
| [0041](0041-comprehensive-encrypted-backup-to-r2.md) | Comprehensive encrypted backup of all state to Cloudflare R2 | Accepted — implemented, verified; **§5.7 cutover EXECUTED 2026-09-21** | 2026-08-17 | Amendment 1 (B2 second provider, noc-master ADR-0232); Amendment 2 (cutover) |
| [0042](0042-fresh-install-integrity-and-demo-hygiene.md) | Fresh-install integrity: idempotent post-baseline migrations, loud startup failures, nightly demo reset | Accepted — v2.80.2/.3/.4 | 2026-08-22 | — *(its "ADR-0035 advisory lock" reference means [0036](0036-migration-single-path-hardening.md))* |
| [0043](0043-flight-details-sidecar-table-for-extended-log-data.md) | Extended DJI log data → `flight_details` + `flight_series` sidecar | Accepted — **P0 + P1 shipped and live; P-EVAL closed; P2–P7 remain** | 2026-09-04 | — |
| [0044](0044-serial-prefix-matcher-odl-canonical-serials.md) | Canonical DJI serials in the fleet-attribution matcher | Accepted — v2.90.0 | 2026-09-05 | **Amends [0007](0007-strict-fleet-attribution-matcher.md)** |
| [0045](0045-phase7-customer-surface-hardening.md) | Phase 7 customer-surface hardening | Accepted — **merged and deployed v2.91.0, 2026-09-21** | 2026-09-21 | Self-corrected same day (`client_ip.py` two-hop chain) |
| [0046](0046-keyless-basemap-registry-and-tile-health-probe.md) | Keyless basemap registry + tile-health probe | Accepted — **shipped and deployed v2.92.0, 2026-09-21** | 2026-09-21 | Supersedes in practice the five hard-coded CARTO/Esri/OSM tile URLs |

---

## Cross-repo ADRs referenced from here

These resolve to **other repositories** and will never be found in this
directory. They are named with their repo in the ADRs that cite them.

| Ref | Repo | Subject |
|---|---|---|
| `ADR-0036` | noc-master | Pushover → ntfy fleet transport standard (`0036-pushover-to-ntfy-migration.md`) |
| `ADR-0037` | noc-master | Fleet notification noise-reduction policy — severity rubric, 5-question gate, cooldowns (`0037-fleet-notification-noise-reduction-policy.md`) |
| `ADR-0056` | noc-master | Fleet deploy gate (`0056-deployer-fleet-build-gate.md`) |
| `ADR-0079` | noc-master | Deployer digest gate observes `build:`-only services (`0079-digest-gate-build-only-image-default.md`) |
| `ADR-0086` | noc-master | 1Password Fleet vault filing (`0086-1password-service-account-fleet-secret-filing.md`) |
| `ADR-0194` | noc-master | Alert-delivery redundancy — the ≤64-char ntfy fallback-topic limit that renamed `droneops-deposits` |
| `ADR-0218` | noc-master | Fleet host clocks moved to `America/Los_Angeles` (2026-08-25) — why crontab expressions changed while wall-clock times did not |
| `ADR-0232` | noc-master | Second provider — immutable nightly Backblaze B2 backup (`0232-second-provider-immutable-nightly-backup.md`) |
| `ADR-0246` | noc-master | Fleet auth-posture standard and the IP-bypass decision (`0246-fleet-auth-posture-standard-and-the-ip-bypass-decision.md`) |
| `ADR-0247` | noc-master | NOC control-plane Phase 1 containment (`0247-noc-control-plane-phase-1-containment.md`) — all ten verified present in `~/noc-master/docs/adr/` on 2026-09-21 |
| `ADR-0001`, `ADR-0002`, `ADR-0008` | BigBill1418/DroneOpsSync | Kotlin resumption; zero-touch key rotation (client); device-upload async poll (client) |
| `ADR-0017`, `ADR-0019`, `ADR-0020` | EyesOn | Camera-less companion UX; managed-tenant discovery |
| `ADR-0018`, `ADR-0020` | CallSign | The auto-chain firewall honoured by [0034](0034-mission-map-stream-cross-system-linkage.md) |

## Where the open work is tracked

`ROADMAP.md` is the durable per-item record.
`docs/reports/2026-09-21-open-items-inventory.md` is the authoritative snapshot
of everything open as of 2026-09-21, including the ADR-borne items this index
flags above.
