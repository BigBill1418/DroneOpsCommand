# ADR-0042 — Fresh-install integrity: idempotent post-baseline migrations, loud startup failures, nightly demo reset

- **Date:** 2026-08-22 (recorded 2026-08-23)
- **Status:** **Accepted** — implemented and verified live on BOS-HQ across
  v2.80.2 (`39dc0c4`), v2.80.3 (`c624fa6`), and v2.80.4 (this commit).
- **Related:** ADR-0021 (failover guard / startup schema sync), ADR-0022
  (Alembic adoption + baseline strategy), ADR-0035 (migration advisory lock),
  ADR-0039/0040 (the migrations that exposed the defect), fleet ADR-0036/0037
  (ntfy transport + noise policy).

---

## Context — the incident

A "confirm everything works" audit on 2026-08-22 found the public demo
(command-demo.barnardhq.com) 48 commits stale, and updating it exposed
something much worse: **every fresh database had crash-looped at startup
since 2026-07-05** — the day migration 0008 landed. That broke, silently:

- the demo instance reseed (any schema wipe → crash loop),
- the README Quick Start (a self-hoster's first `docker compose up`),
- future managed-client provisioning (fresh DB per client).

Production never noticed, which is exactly why it went undetected for seven
weeks: existing databases are stamped past the baseline and never execute
the colliding DDL. CI didn't catch it either — tests run against migrated
or ORM-created schemas, not the fresh-boot path.

### Root cause

`0001_baseline_schema.upgrade()` deliberately builds fresh databases with
`Base.metadata.create_all` **from the live models** (ADR-0022's "the
baseline is whatever the legacy startup produced" decision). This has a
non-obvious consequence: on a fresh DB the baseline always produces the
*current-model* schema — including every column any *later* migration adds.
Migrations 0008/0009 used bare `op.add_column`, so on fresh DBs they raised
`DuplicateColumn` — while passing everywhere anyone was looking.

### The failure was silent — and why (found by reproduction, v2.80.4)

The observable symptom was a container restarting every ~5 s with exit
code 3 and logs that simply *end* mid-migration — no exception, no error
line anywhere. The mechanism, proven by forcing a startup failure in a
scratch container: `alembic/env.py` called
`fileConfig(alembic.ini)` on every invocation, and `fileConfig()` defaults
to `disable_existing_loggers=True` — so the moment `command.upgrade()` ran,
the app's `doc` logger and uvicorn's `uvicorn.error` logger were both
silently disabled. Every message after that point, *including the exception
that explained the crash and uvicorn's own "Application startup failed"
line*, was dropped. (The tell in retrospect: post-alembic log lines switch
from JSON to alembic's plain format.)

The real error only surfaced by running `alembic upgrade head` in a one-off
container (`docker compose … run --rm --no-deps --entrypoint sh backend -c
"alembic upgrade head"`) — still the documented first move if a container
ever dies silently at startup again.

## Decisions

1. **Post-baseline schema migrations MUST be idempotent.** Because 0001 is
   live-model `create_all`, every later `op.add_column` / `op.create_table`
   must first check existence and no-op (see 0008/0009 for the pattern:
   `sa.inspect(conn).get_columns(...)`). A bare additive migration is a
   fresh-install landmine that passes review, CI, and prod. This is the
   standing rule until/unless the baseline is ever frozen to a
   point-in-time schema — an alternative we considered and rejected because
   regenerating a frozen baseline on every model change is the same
   maintenance burden with worse failure modes (drift between baseline and
   models breaks *existing* installs, not just fresh ones).

2. **Startup failures must be loud.** Two layers (v2.80.4):
   - `alembic/env.py` now calls `fileConfig()` **only for the standalone
     CLI** (skipped when `config.attributes["connection"]` is set, i.e. the
     programmatic in-app path) — app logging survives migrations.
   - The entire pre-yield lifespan body runs inside a `try/except` that
     logs the full traceback (`STARTUP FAILED — …`) AND prints it straight
     to stderr before re-raising — so even if some future startup step
     reconfigures logging again, the evidence still reaches `docker logs`.
   Verified by forcing a bogus `alembic_version` revision in a scratch
   container: exit 3 as before, but the logs now carry the full traceback
   ending in the actual error.

3. **The public demo resets nightly.** `DEMO_RESET_INTERVAL_HOURS` is env-
   configured but was never implemented; the demo had accumulated months of
   visitor junk (uploaded flight logs with real third-party GPS, contact
   emails). Until an in-app reset exists, `scripts/demo-nightly-reset.sh`
   runs from the BOS-HQ operator crontab (`23 9 * * *` UTC = 2:23 AM
   Pacific): wipe demo schema → restart backend (startup rebuilds via
   alembic + demo seed) → wait container-healthy → live login probe →
   ntfy `droneops-demo-reset` (priority high) on any failure. Celery beat
   was rejected as the scheduler because the demo worker/beat must stay
   stopped (dunning-email hazard — see `docs/managed-hosting.md` and the
   demo compose). An in-backend asyncio task honoring
   `DEMO_RESET_INTERVAL_HOURS` remains the preferred long-term shape.

4. **No realistic random literals in tests.** The Secret Scan workflow had
   been red on every push because gitleaks (correctly) flagged a realistic
   44-char random `intake_token` fixture. Test fixtures use obviously-fake
   low-entropy stand-ins (`TESTONLY-…`) instead of allowlist entries, so the
   gate stays at full strength and a red X always means something.

## Consequences

- Fresh installs work and are *proven* to work: the verification for this
  ADR ran the full migration chain to `0009 (head)` on a scratch Postgres
  and booted the demo from an empty schema to healthy + live login, twice.
- Migration authors carry a new obligation (existence guards). The
  CHANGELOG v2.80.2 entry and the docstrings in 0008/0009 state the rule
  where the next author will actually see it.
- The demo is self-cleaning; a failed reset pages instead of silently
  serving a broken or junk-filled trial.
- Any future silent container death with logs ending mid-startup is a
  regression of decision 2 and should be treated as a bug, not a mystery.

## Verification evidence (2026-08-22/23, BOS-HQ)

- Scratch fresh DB: `alembic upgrade head` → `0009_mission_dl_email_sent_at (head)`.
- Demo wiped + rebooted: container healthy, `alembic_version` at head, seed
  counts correct (1 user / 4 customers / 12 flights / 6 missions), live
  login 200, UI reviewed by eye at 1440px and 390px.
- `scripts/demo-nightly-reset.sh` fire-drilled live end-to-end before the
  cron was installed; exit "OK — demo reseeded and login verified".
- Secret Scan: first green run `c624fa6` after six consecutive red pushes.
- Prod: `/openapi.json` version matches repo on every step (2.80.2 → 2.80.3),
  migrations log `noop` (already at head), all containers healthy.
