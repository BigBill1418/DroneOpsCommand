# DroneOpsCommand — Roadmap

Forward-looking work items. Shipped scope is tracked in `CHANGELOG.md`;
in-flight scope is tracked in `PROGRESS.md`. This file holds only
not-yet-started work with a clear trigger, scope, and ADR/decision
reference where applicable.

## Backup + DR follow-ups (from ADR-0041, 2026-08-17)

### BK-1 — PITR via `pg_receivewal` — DEFERRED

- **Scope.** A fifth restic lane: `pg_receivewal` streaming into a dedicated
  volume (never into pgdata), backed up alongside the existing four lanes, with
  its own liveness alert — a silently-dead `pg_receivewal` is a new dead-man
  class and must be monitored as one.
- **Trigger.** Write volume grows ~10×, **or** the RPO requirement drops below
  the current 12 h. Neither holds today: real change is ~1 MB/day, and one
  16 MiB WAL segment was produced in the 26 days before archiving was retired.
- **Explicitly NOT the path.** Re-enabling `archive_command` into pgdata. That
  is what was retired on 2026-08-17: the archive lived inside the volume it was
  meant to protect, was never pruned (5.5 GiB / 358 segments), and with
  `archive_timeout=0` had a 26-day hole — a liability wearing PITR's clothes.
  Setting `archive_timeout=300` instead would write ~4.6 GB/day of
  mostly-empty padded segments to protect ~1 MB/day of change.
- **Reference.** ADR-0041 D5 + Option D.

### BK-2 — Standby `archive_mode` on `10.99.0.2` — ✅ DONE 2026-08-18

- **Scope.** The droneops standby (`droneops-db-standby` on svdp-dev) still
  carries `archive_mode = 'on'` and the old
  `archive_command = cp %p …/wal_archive/%f` in its `postgresql.auto.conf`,
  inherited from the base backup.
- **Why it matters.** Inert today — `on` does not archive during recovery, only
  `always` does. **But a promotion would immediately recreate ADR-0041 Gap 7 on
  that host**, silently accumulating an unpruned archive inside the pgdata
  volume it is supposed to protect.
- **Fix.** `ALTER SYSTEM SET archive_mode='off'; ALTER SYSTEM RESET
  archive_command;` then restart the standby (archive_mode needs a restart, not
  a reload) and confirm it resumes streaming and the slot returns to `active`.
- **Trigger.** Next planned maintenance window on svdp-dev, or immediately
  before any deliberate failover drill. Deferred here only because it needs a
  standby restart, which was out of scope for the backup change.
- **Executed 2026-08-18 (operator-approved).** `ALTER SYSTEM` is unavailable in
  recovery, so `postgresql.auto.conf` was edited directly (`archive_mode='off'`,
  `archive_command` line removed) followed by a container restart. Also found
  and deleted **1.5 GiB / 99 stale WAL segments** already sitting in the
  standby's own `wal_archive` (inherited from the seeding basebackup) — pgdata
  2.3 G → 851 M. Verified after: `pg_is_in_recovery()=t`, `archive_mode=off`,
  wal receiver `streaming`, primary slot `chad_hq_standby` active with empty
  `replay_lag`. A promotion can no longer recreate Gap 7.

## Observability + Fleet Hygiene (follow-ups from ADR-0002, 2026-04-24)

**Context.** ADR-0002 shipped the primary fix for the operator's DJI RC
Pro flight-record upload failure (HTTPS-only base URL + fresh Capacitor
APK + existing `X-Device-Api-Key` auth). The follow-ups below are
legitimately deferred — not load-bearing for the primary fix, but
necessary to prevent the class of failure from recurring silently on
a different controller.

### FU-1 — Fleet APK version audit — ⚠ DE-PRIORITIZED (2026-04-24, ADR-0002 §5)

- **Scope.** Enumerate every DJI RC Pro / DJI Pilot 2 / DJI Fly device
  that has ever paired with DroneOps. Cross-reference against
  `device_api_keys.last_used_at` and, where possible, any version/UA
  hint the server logged on upload.
- **Trigger.** Was: "before a fleet-wide OTA push can be planned".
  Now: **optional hygiene**. ADR-0002 §5's silence watchdog + layer-1
  banner make fleet-wide drift self-detecting (any controller that
  stops uploading for >48h pages Bill via ntfy, any controller with
  a cleared `Preferences` shows the red banner on next launch). The
  audit is still useful for proactive APK OTA planning but is no
  longer load-bearing against silent data loss.
- **Deliverable.** `scripts/audit-device-fleet.py` + a one-page
  operator report listing each device label, last-seen timestamp, last
  observed APK version (if known), and upgrade plan (OTA-capable vs
  sideload-required).
- **Owner.** TBD. Likely ~1 eng day.

### FU-2 — Unauthenticated `GET /health` shim — ✅ SHIPPED v2.63.4 (2026-04-24)

- Delivered as a plain JSON alias (same payload as `/api/health`).
  Reasoning for deviating from the spec'd "update-required" banner: a
  pre-v2.34 Gson client with `setLenient(false)` would choke on any
  payload that doesn't match its expected shape, so custom banner
  fields buy nothing on the failing client and are confusing to
  modern clients. The WARN log on auth-failure in
  `backend/app/auth/device.py` is the actual stale-client tripwire
  (key_prefix + IP + user-agent + path); FU-3's Grafana panel consumes
  that stream directly. FU-2 rate-limiting not needed since `/health`
  never triggers the WARN path.

### FU-3 — Grafana stale-client tripwire

- **Scope.** Add a panel on the DroneOps Grafana dashboard for
  "Device auth failures (24h)" sourced from the structured WARN log
  emitted by `validate_device_api_key` in
  `backend/app/auth/device.py` (fields: `key_prefix`, `ip`,
  `user_agent`, `path`). ntfy alert at ≥5 hits/24h from ≥2
  distinct IPs (filters out a single responder testing an old phone).
  **Transport corrected 2026-08-03:** this item was written pre-cutover and
  said "Pushover". Pushover was decommissioned fleet-wide on 2026-04-26
  (noc-master ADR-0036 + this repo's
  `docs/adr/0006-pushover-to-ntfy-migration-addendum.md`); the alert must be
  built on ntfy (`https://ntfy.barnardhq.com`, topic prefix `droneops`, click
  `https://noc-mastercontrol.barnardhq.com/status/droneops`) and graded
  against the ADR-0037 five-question gate before it pages.
- **Trigger.** v2.63.4 is live. Panel can be shipped any time.
- **Deliverable.** Grafana JSON + alert rule in `~/noc-master` config.
- **Owner.** TBD. ~0.5 eng day.

### FU-4 — Device-key lifecycle policy

- **Scope.** Decide whether `device_api_keys` rows should have an
  automatic expiry/rotation (e.g., 90-day TTL with a 7-day grace
  window where both old+new keys are accepted), or stay indefinite
  revoke-on-demand. Current model is revoke-on-demand only.
- **Trigger.** Before the first real managed-tenant ships (managed
  operators are less likely to tolerate indefinite keys).
- **Deliverable.** One-page decision doc as ADR-0003 or ADR-0004
  (pick next free number), plus schema migration if rotation is
  adopted.
- **Owner.** TBD.

### FU-5 — Managed-tenant discovery (EyesOn ADR-0020 parity)

- **Scope.** When a first DroneOps managed customer is committed,
  port EyesOn's `GET /api/discovery/pair/:code` fan-out pattern +
  tenant-side `GET /api/companion/pair/:code/exists` boolean-only
  endpoint to DroneOps. Companion types a 6-digit code, hits
  discovery on the primary, adopts the tenant URL. No manual URL
  entry, matching `feedback_managed_customer_seamless.md`.
- **Trigger.** First DroneOps managed customer signed / deployment
  scheduled.
- **Deliverable.** Server endpoints + companion integration +
  `MANAGED_TENANT_URLS` env var plumbing on the primary instance's
  `docker-compose.yml`. Copy-paste-with-rename from
  `eyeson-managed`/`EyesOn` repos; estimated 1-2 eng days.
- **Owner.** TBD.

### FU-6 — End-to-end test for `device-upload` auth path

- **Scope.** Integration test that provisions a `DeviceApiKey`,
  hits `/api/flight-library/device-health` with the raw key, then
  `/api/flight-library/device-upload` with a sample DJI flight
  record fixture, and asserts the `Flight` row is created with the
  expected `source_file_hash`. Today the backend has unit coverage
  for the auth dependency; the full upload pipeline is untested
  end-to-end.
- **Trigger.** Any time; good hygiene regardless of ADR-0002's
  immediate fix.
- **Deliverable.** `backend/tests/test_device_upload.py` +
  fixture log file in `backend/tests/fixtures/flight-records/`.
- **Owner.** TBD. ~0.5 eng day.

---

## Older roadmap items

None yet captured here. When a new forward-looking plan is drafted,
append it under its own heading with the same Scope / Trigger /
Deliverable / Owner block structure.

## LLM-assisted report surface (follow-ups from ADR-0015, 2026-05-14)

**Context.** ADR-0015 (Accepted 2026-05-14) pinned the contract:
*mission reports are client-facing artifacts; operator-facing coaching
is explicitly out of scope.* The current LLM dispatch
(`backend/app/services/llm_provider.py`) and shared system prompt
produce one artifact — the customer-facing report — and that is the
entirety of the LLM-assisted surface in this product. The follow-ups
below harden the prompt path against future audience drift; they do
not introduce additional LLM surfaces.

**Note on dropped item:** an earlier draft listed an operator-facing
retrospective surface (FU-AI-1) as a follow-up. The operator confirmed
at close-out that no operator debrief was ever requested; FU-AI-1 is
**dropped**, not deferred. See ADR-0015 §"Rejected alternative" for the
rationale.

### FU-AI-RUNTIME-GATE — Runtime audience-leak soft-block — ✅ SHIPPED at commit `4953edf` (2026-05-14, local; deploy pending operator review)

- **Scope.** Wire `report_audience.has_audience_leak()` (shipped at
  commit `22469ed` as a callable module) as a post-generation gate on
  every LLM-produced report draft. On leak detection: flag the draft
  (do not silently pass), surface the offending phrasings in the
  `MissionReportEdit` editorial UI banner, allow operator override
  ("soft-block" — never block the operator from shipping, but never
  let the leak be invisible). Tripwire on top of the corrected prompt,
  not a substitute for it.
- **Delivered as.** Wire-in lives at the persistence site, not the
  per-provider call paths: `_apply_audience_findings(report, llm_content)`
  in `backend/app/tasks/celery_tasks.py:150-189` runs the detector after
  every LLM generation and persists findings into two new `Report`
  columns (`has_audience_leak BOOL`, `audience_leak_details JSONB`)
  added via the idempotent `_add_missing_columns` migration path in
  `backend/app/main.py:114-122`. Helper never raises (detector failure
  logs and leaves defaults so generation never 500s). No regen loop
  per operator directive — detection + surfacing only, with a
  doc-string-lock test preventing drift toward retry-clean. Yellow
  `IconAlertTriangle` Mantine `Alert` banner above the FINAL REPORT
  editor in `MissionReportEdit.tsx` lists each matched phrase with its
  rule name; Save / PDF / Send remain enabled (editorial review IS the
  gate). Test coverage: 10 new hermetic tests in
  `backend/tests/services/test_audience_leak_persistence.py` (10/10
  green); existing 17-test audience suite stays green; full backend
  suite 240 passed, 1 skipped, 2 pre-existing unrelated failures.
- **Deploy status.** Personal-instance only; no deploy yet
  (`.deployer-disabled` per fleet convention). 24h soak with a real
  report generated against the new gate before any push to
  managed-hosting tenants, per operator's standing close-out preference.

### FU-AI-2 — Prompt regression fixture — ✅ SHIPPED at commit `22469ed` (2026-05-14)

- **Delivered as `backend/tests/services/test_report_audience_guard.py`**
  rather than the originally proposed `test_llm_report_audience.py` path.
  17-test suite: Layer 1 (4 tests) locks structural guarantees of the
  system prompt (audience pin, operator-address ban, Section-5 reframe,
  operator-notes framing); Layer 2 (13 tests) exercises the deterministic
  regex-based detector against nine representative bad phrasings, a
  known-clean third-person sample, empty input, diagnostic snippet
  shape, and the verbatim shape of the operator-reported leak.
  Hermetic — no network, no LLM, no DB. Runs ~1.8s. All 17/17 passing
  on Python 3.12.3.

### FU-AI-3 — Prompt source-of-truth relocation — ⚠ DE-PRIORITIZED (2026-05-14)

- **Scope unchanged.** Move `SYSTEM_PROMPT_TEMPLATE` out of
  `backend/app/services/ollama.py` into
  `backend/app/services/llm_prompts.py`; update imports in
  `claude_llm.py` and `ollama.py`. The prompt is the cross-provider
  contract; it does not belong in one provider's adapter.
- **Why de-prioritized.** Aegis's audience-fix commit (`22469ed`)
  deliberately left the prompt in `ollama.py` to keep the change
  footprint tight (CHANGELOG entry calls this out). The relocation is
  a pure refactor with no behavioral coverage; pairing it with the
  audience fix would have expanded the surgical surface without adding
  safety. Item still stands on its own merits — the cross-provider
  contract does logically belong outside any one provider adapter — but
  is no longer urgent.
- **Trigger.** Next time the prompt is meaningfully edited (e.g., a
  managed-tenant tone addendum per FU-AI-4 lands), bundle the
  relocation. Until then, leave it.
- **Deliverable.** Refactor commit + one-line import update in two
  files. No version bump (no behavior change).
- **Owner.** TBD.

### FU-AI-4 — Per-tenant prompt override (managed-hosting only, optional)

- **Scope.** For managed-hosting tenants that want to brand the report
  voice differently ("warm and conversational" vs "technical and
  terse"), expose a tenant-scoped prompt-fragment override in
  `system_settings` (key e.g. `llm_prompt_tone_addendum`). The
  audience-separation invariant from ADR-0015 stays hard-coded and not
  overridable; only the tone-shaping addendum is tenant-tunable. The
  runtime soft-block gate (FU-AI-RUNTIME-GATE) still applies — a
  managed-tenant tone override that produced an audience leak would be
  flagged in `MissionReportEdit` like any other draft.
- **Trigger.** First managed-hosting customer asks for a different
  voice. Not before — premature flexibility.
- **Deliverable.** One new setting key, one prompt-construction site
  updated, one Settings-page UI element gated to `managed_instance=true`.
- **Owner.** TBD. ~0.5 eng day.

  *Standalone justification (post 2026-05-14 close-out):* FU-AI-4 is
  about tenant-branded voice/tone, not about audience separation. It
  remains coherent under the tightened ADR-0015 scope — the single
  client-facing artifact can still have its voice tuned per tenant
  without re-introducing a second audience.

### FU-AI-QUALITY-PASS — Overall mission-report quality iteration — NOT STARTED (watching brief)

- **Status.** NOT STARTED. Awaiting operator direction on priority and scope.
- **Source.** Operator feedback at the 2026-05-14 ADR-0015 close-out,
  verbatim: *"its ok for now but it needs to get better."* No specific
  changes requested — this is a signal that the current report quality
  bar is not the destination, not a directive to make a specific
  change today.
- **Scope (open-ended).** Quality improvement to the client-facing
  mission report generated by `backend/app/services/llm_provider.py`
  and the shared `SYSTEM_PROMPT_TEMPLATE`. The audience-separation
  contract from ADR-0015 stays load-bearing; quality work happens
  inside that contract, not by relaxing it.
- **Candidate areas (inference, not commitment — operator has not
  specified).** Listed so future sessions have a starting point if
  asked to dig in, *not* as a punch-list to grind through:
  - Section 5 framing strength — currently "Client Follow-Up Items"
    with an explicit OMIT-fallback; possible iterations on how the
    section is structured when it does fire.
  - Weak / hedging language — sentence-level passes against
    "appeared," "seemed," "was observed to" where a definitive
    statement is warranted.
  - Conciseness — current drafts tend toward narrative bulk; signal
    density per paragraph may be a lever.
  - Signal-to-noise on routine flights — when nothing notable
    happened, the report still produces five sections. May be worth a
    "routine flight" prompt variant or an explicit length budget.
  - Consistency across mission types — same prompt drives both
    inspection and survey reports; per-mission-type prompt fragments
    are a possible direction (overlaps with FU-AI-4's per-tenant
    fragment infrastructure if that lands first).
- **Trigger to act.** Operator-driven. This is a watching-brief item:
  next session that touches the prompt should ask the operator what
  specifically he wants improved before opening work. Do not assume
  a direction and start editing.
- **Deliverable.** TBD when scoped. Likely a prompt iteration plus an
  extension of the existing 17-test `test_report_audience_guard.py`
  suite for any new structural guarantees added during the pass.
- **Owner.** TBD. Operator scopes when ready.

### FU-7 — Zero-touch device API key rotation — **CLOSED 2026-04-24** (v2.63.6 / DroneOpsSync v1.3.25)

- **Status.** PR open against `main` on this repo (`claude/zero-touch-key-rotation-backend`); paired DroneOpsSync PR open against `main` (`claude/auto-rotation-client`). Operator reviews + merges.
- **Shipped scope.** Backend grace-window dual-key auth + rotated-key hint in `/api/flight-library/device-health` response + Celery finalizer task (15-min beat) + Pushover FYI + admin endpoint `POST /api/admin/devices/{id}/rotate-key`. Bootstrapped `backend/tests/` infrastructure (15 tests, all green).
- **Trigger.** Bill rotated M4TD in-place 2026-04-24 AM; operator had to manually paste new key on RC Pro Settings. v1.3.24's preflight gate surfaced the invalid-key state correctly, but eliminating the paste step was the real goal.
- **Deliverable.** ADR-0003 (`docs/adr/0003-zero-touch-device-key-rotation.md`) + plan (`docs/plans/2026-04-24-zero-touch-key-rotation.md`) + migration + endpoint + Celery task + tests. Remote routine `trig_01KiBK88vqs6vtRf75rkxcw8` initially shipped an empty branch; aegis re-ran and produced both PRs.
- **Owner.** aegis (scaffold); Bill (review + merge).

### FU-8 — Ground-up audit residuals (2026-06-11 multi-agent pass) — **CLOSED 2026-06-11** (v2.70.0/v2.70.1)

- **Status.** CLOSED. All six residuals + every remaining P2/P3 audit finding
  shipped across v2.70.0 (Alembic ADR-0022, async backup jobs, lean mission
  list, Stripe per-call client, DISTINCT ON maintenance, health-gate trim,
  cpus fences, versioned BOS override) and v2.70.1 (Settings lazy-tab split,
  backup-jobs UI, useApiCache rollout, Airspace visibility guard).
  Consciously NOT done (new follow-ups, operator-triggered):
  * Device-upload Celery decoupling (audit P2-2 full leg) — requires a
    DroneOpsSync client release; backend per-file batch isolation is pinned
    by tests in the meantime. **DESIGNED 2026-06-15** — ADR-0023 + DroneOpsSync
    ADR-0008 + staged plan `docs/plans/2026-06-15-device-upload-async-decoupling.md`.
    Recommended: ship the DroneOpsSync socket-timeout-is-per-file fix as a
    standalone fast-follow (one-line, backend-independent) ahead of the full
    async route. Implementation not yet started.
  * Trigram (pg_trgm) indexes for the leading-wildcard ILIKE searches —
    rejected from 0002 as B-tree can't serve them; revisit if flight search
    slows at scale.
- **Original scope (for the record).** Was: Phases 1–3 of the audit SHIPPED (v2.68.7 image-upload OOM
  fix, v2.68.8 event-loop unblocking sweep + eager-load scoping, v2.69.0
  standby-safe startup + hot-path indexes + streaming flight ingest).
  Full findings: `docs/plans/2026-06-11-ground-up-audit.md`; ADR-0021.
- **Remaining items (deliberately deferred, in priority order):**

  > **Corrected 2026-08-03 — this list is stale; nearly all of it SHIPPED.**
  > It was written when FU-8 was opened and was never trimmed when FU-8 was
  > closed, so the two halves of this entry now contradict each other (the
  > CLOSED note above already names the same work as delivered). Verified
  > against the code at HEAD, not against prose:
  >
  > - **#1 lean mission list** — shipped; `app/routers/missions.py` strips the
  >   heavy `flight_data_cache` keys (`_strip_cache_heavy_keys`,
  >   `_scalar_cache_from_flight`).
  > - **#2 Alembic** — shipped (ADR-0022, v2.70.0). `backend/alembic/versions/`
  >   holds `0001_baseline_schema` … `0009_mission_dl_email_sent_at`, all nine
  >   are present inside the running `droneops-backend-1` container on BOS-HQ,
  >   and ADR-0036 made the advisory-locked Alembic boot the single schema
  >   path. **Anything that still says "DroneOpsCommand has no Alembic; add
  >   columns to `_add_missing_columns()` in `main.py`" is describing the
  >   pre-v2.70.0 repo and must not be followed.**
  > - **#3 per-call `StripeClient`** — shipped;
  >   `app/services/stripe_service.py::stripe_client()`, consumed by
  >   `client_portal.py` and `stripe_webhook.py`.
  > - **#4 backup/restore as a Celery job** — shipped;
  >   `run_backup_job_task` in `app/tasks/celery_tasks.py` (comment there still
  >   cites "FU-8 #4").
  > - **#5** — `_save_original_file` no longer exists in `backend/app`; only
  >   the optional `/reprocess` refactor half may remain.
  > - **#6 P2/P3 index audit** — shipped as Alembic revision `0002_p2_p3_indexes`.

  1. Mission Hub list payload is still O(track) — `flight_data_cache`
     duplicates the GPS track in every list row; needs a lean list schema or
     pagination (contract change → frontend work in the same pass).
  2. Adopt Alembic for schema migrations; move the startup
     `create_all`/`_add_missing_columns`/index block into versioned
     migrations (ADR-0021 future-work section).
  3. Stripe: migrate module-global `stripe.api_key` to per-call
     `StripeClient` instances (closes the key-rotation interleave window
     noted in the v2.68.8 verification).
  4. Backup/restore as a Celery job with progress polling (currently
     executor-offloaded in-request; contract change).
  5. `/reprocess` new-flight branch could reuse `_build_flight_from_parsed`
     if its divergent log lines are acceptable; delete now-unused
     `_save_original_file`.
  6. Audit P2/P3 index candidates (e.g. `flights.start_time`) once Alembic
     lands.
- **Trigger to act.** Operator-driven, or the next perf session.
- **Owner.** TBD.
