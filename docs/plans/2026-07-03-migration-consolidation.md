# Plan: DB-Migration Consolidation — retire the legacy schema helpers, harden the boot path

- **Date:** 2026-07-03
- **Status:** Proposed (phased; low-risk, live-DB-safe)
- **Owner:** engineering (DroneOpsCommand)
- **Related:** ADR-0016 (mission source attribution — used the legacy
  `_add_missing_columns` mechanism), ADR-0021 (standby-safe startup / recovery
  guard), ADR-0022 (Alembic adoption — baseline + brownfield stamp), v2.75.1
  hotfix (revision-id length crash-loop).

---

## 1. Reality check — the split-brain is *already mostly resolved*

The task framed three mechanisms "running simultaneously." Investigation shows
the convergence has largely happened, and stating that precisely is what makes
this plan low-risk instead of a redo:

- **`backend/app/db_migrations.py` is not a third mechanism — it IS the Alembic
  runner.** `run_migrations_sync()` (`db_migrations.py:82-155`) detects DB state
  (fresh / brownfield / pending / noop) and runs `command.upgrade(cfg, "head")`
  inside a single `engine.begin()` transaction. ADR-0022 (2026-06-11) made this
  the startup schema mechanism.
- **Alembic is the live path.** Boot calls it at
  `main.py:387-392` (`await loop.run_in_executor(None, run_migrations_sync)`),
  primary-only, behind the ADR-0021 `pg_is_in_recovery()` guard. Head revision:
  `0007_strip_legacy_cache_track`. Seven revisions exist.
- **`_add_missing_columns()` is no longer the runtime mechanism.** It survives in
  `main.py:67-253` but is now **imported by baseline migration
  `0001_baseline_schema.py:80`** to reproduce the exact legacy schema for
  brownfield stamping. `Base.metadata.create_all` is likewise called only inside
  the baseline. ADR-0016's rationale ("this repo has no alembic.ini / env.py /
  versions/") was true at v2.70.0 and was **superseded** by ADR-0022 — the two
  ADRs do not actually conflict in the current tree; ADR-0016's columns are now
  captured in baseline 0001.

**So the "converge on ONE (Alembic)" decision is effectively made and shipped.**
What is left is not a migration *of mechanisms* but the removal of a **latent
foot-gun** and the **hardening of the boot path** the task correctly flags. The
v2.75.1 crash-loop is the proof the boot path is still fragile.

---

## 2. The true residual risks (what this plan actually fixes)

1. **`_add_missing_columns` / `_create_hot_indexes` / `create_all` still live in
   `main.py` and are importable at runtime.** Nothing structurally *prevents* a
   future change from calling them at boot again, re-opening the split-brain. A
   contributor adding a column there instead of a migration would get silent
   drift between Alembic's `alembic_version` head and the actual schema.
2. **No advisory lock on the migration run.** Safety today rests on **two
   fragile assumptions**: (a) `--workers 1` (Dockerfile CMD) and (b) a single
   backend replica. `run_migrations_sync` relies only on transaction atomicity —
   if DOC ever runs 2 workers, 2 replicas, or a blue-green pair briefly points
   two backends at the same writable primary, they can race
   `command.upgrade`. Contrast: **seed already takes `pg_advisory_lock(8675309)`**
   (`seed.py:29,49`) — the migration path should have the same protection and
   does not. The task explicitly asks for "an advisory-lock'd boot path."
3. **Revision-id length is an un-enforced invariant.** v2.75.1 crash-looped
   because `0004_dji_duration_and_flight_name_restamp` (41 chars) overflowed
   `alembic_version.version_num VARCHAR(32)`; the DDL ran but the stamp rolled
   back on **every** startup. The lesson is in a docstring, not in CI.
4. **Autogenerate drift risk.** `alembic.ini`'s `sqlalchemy.url` is blank
   (resolved at runtime in `env.py`), and `env.py` filters some indexes from
   autogenerate. There is no CI check that the models and the migration head
   actually agree, so schema drift can accumulate silently between releases.

---

## 3. Target end-state

- **Alembic is the *only* schema mechanism, and the legacy helpers cannot run at
  runtime** — they exist (if at all) only frozen inside the baseline migration
  file, not importable from `main.py`'s startup path.
- **The migration run holds a Postgres advisory lock**, so it is safe under
  multiple workers/replicas/blue-green overlap — matching the seed path's
  posture.
- **CI enforces the invariants** that have already bitten us: revision-id ≤ 32
  chars, and models-vs-head are in sync (autogenerate produces an empty diff).

---

## 4. Phased plan (each phase independently shippable + reversible)

**Deploy reality:** `.deployer-disabled` repo → manual rebuild on BOS-HQ,
verify container build time not deployer status (ADR-0033 note). Migrations run
primary-only behind the ADR-0021 recovery guard — **do not** remove that guard;
it is what stops the backend from crash-looping DDL against a read-only standby
during a failover.

### Phase 1 — Advisory-lock the migration boot path (highest value, lowest risk)
- Wrap the body of `run_migrations_sync()` in a session-level advisory lock
  acquired on its own short-lived psycopg2 connection **before** the state-detect
  + upgrade, released in a `finally`. Use a **distinct** lock id from seed's
  `8675309` (e.g. `8675310`) so migration and seed don't serialize against each
  other unnecessarily.
- Use **`pg_advisory_lock`** (blocking) so a losing racer *waits* for the winner
  to finish, then re-checks state and no-ops (`current == head` → `"noop"`) —
  not `pg_try_advisory_lock` (which would let the loser skip and proceed
  un-migrated).
- The lock must be **session-scoped on the migration's own connection**, taken
  and released around the whole detect+upgrade, so it is held across the
  `engine.begin()` transaction. Ensure it is released even on exception
  (`try/finally` + `engine.dispose()` in the existing `finally`).
- **Test:** spawn two threads calling `run_migrations_sync` against the same test
  DB; assert exactly one performs `upgraded`/`upgraded:fresh` and the other
  returns `noop`, with no error and no double-applied revision.
- **Rollback:** revert; single-worker safety is unchanged.

### Phase 2 — CI guards for the invariants that already bit us
- **Revision-id length check:** a lint/test that scans `alembic/versions/*.py`
  and fails if any `revision = "…"` exceeds 32 characters. (Directly prevents the
  v2.75.1 crash-loop class.)
- **Model-vs-head sync check:** a CI job that spins an ephemeral Postgres,
  `alembic upgrade head`, then `alembic revision --autogenerate` and fails if the
  produced migration is non-empty (accounting for `env.py`'s documented index
  filter). Catches silent drift between the ORM models and the migration head.
- **Rollback:** these are CI-only; reverting removes the guard, no runtime
  impact.

### Phase 3 — Sever the legacy helpers from the runtime path
- **Vendor-freeze** the DDL the baseline needs: copy the exact
  `_add_missing_columns` / `_create_hot_indexes` bodies **into**
  `0001_baseline_schema.py` as private module-level functions of that migration
  file, and change the baseline to call its own frozen copies.
- **Remove** `_add_missing_columns`, `_create_hot_indexes`, and any runtime
  `create_all` from `main.py`. After this, `main.py`'s only schema action is
  `run_migrations_sync`. Nothing at runtime can ALTER outside Alembic.
- Add a **guard test** asserting `main.py`'s startup module does not import or
  call `create_all` / `_add_missing_columns` (import-graph or source-scan test) —
  so the split-brain cannot silently return.
- **Why freeze rather than keep the import:** baseline 0001 must be immutable and
  reproducible forever; letting it import from `main.py` means a future edit to a
  live helper would retroactively change what "baseline" means for any fresh DB.
  Freezing decouples the historical baseline from live code.
- **Rollback:** the frozen baseline is functionally identical to the imported
  version (same DDL); reverting restores the import. Verify by building a fresh
  DB from scratch and diffing the resulting schema against a brownfield-stamped
  DB — they must match (this is the ADR-0022 brownfield contract).

### Phase 4 — Documentation + convention lock
- Add a short `docs/adr/00NN-migration-single-path-hardening.md` (next free DOC
  number after 0033 = **0034**, unless the mission↔map↔stream ADR takes it — see
  note) recording: Alembic is the sole mechanism, the advisory-lock contract, the
  ≤32-char revision rule, and the CI sync gate. Update ROADMAP "remaining item
  #2" (adopt Alembic / move the startup block) to **done**, and the CLAUDE.md /
  CONTRIBUTING note "all schema changes are Alembic revisions; never add a column
  in `main.py`."

> **ADR-number coordination:** this consolidation and the mission↔map↔stream
> unification ADR both want the next free DOC number. The unification ADR is the
> more strategic artifact — give it **0034** and give this consolidation
> **0035**, or fold this plan's decision into a single ADR-0035. Confirm the free
> number with `ls docs/adr` at authoring time.

---

## 5. Sequencing & safety notes

- **Order matters:** Phase 1 (advisory lock) first — it is the one that closes a
  real correctness hole (racing migrations) and is fully independent. Phase 3
  (helper removal) last among code changes — it is the most invasive and should
  ride on the CI guards from Phase 2 that prove the baseline still reproduces the
  schema.
- **Never touch the ADR-0021 recovery guard.** Every phase must preserve
  "skip all DDL when `pg_is_in_recovery()`." This is load-bearing for failover.
- **Live-DB safety:** none of these phases alter existing data. Phase 1 changes
  *how* the migration runs (adds a lock), Phase 2 is CI-only, Phase 3 moves DDL
  *source* without changing the DDL, Phase 4 is docs. The only DB writes are
  whatever Alembic revisions are already pending at head.
- **Backup before Phase 3 deploy** (the helper-removal release) even though it is
  DDL-neutral, because it is the release most likely to expose a fresh-vs-
  brownfield schema divergence if the freeze copy drifted.

---

## 6. Decision for Bill

**Converge on Alembic as the single mechanism (already de facto true), and
harden the boot path with an advisory lock + CI invariants rather than a
rewrite.** The only judgment call is ADR numbering / whether to bundle this into
one ADR with the mission↔map↔stream work (recommend: separate ADRs, this one
gets 0035).
