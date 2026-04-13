# Post-mortem: DroneOpsCommand demo outage — 2026-04-13

**Author:** Terry (forensic audit of a prior Claude session's work)
**Status:** Blameless process post-mortem. Service restoration handled separately by Aegis.
**Scope:** Process failures, not the technical fix.

## 1. Timeline

All times UTC, reconstructed from `git log` and container uptime on CHAD-HQ.

| Time (UTC) | Event |
|---|---|
| 01:21 | Commit `ab32335` pushed to `main` — "Fix IP leak, raise minimums, kill claude/dev branch — v2.61.5". 13 files, 87 insertions, 38 deletions. Touches companion APK, `setup-server.sh`, `autopull.sh`, `CLAUDE.md`, `.env.example`, and deletes `VERSION`. |
| 01:21–01:49 | Deploy attempted on CHAD-HQ against `~/droneops-demo/`, a clone **304 commits behind `main`**. Uncommitted local overrides found on `.env.demo`, `docker-compose.yml`, `docker-compose.demo.yml`; untracked `scripts/` directory containing `init-primary.sh` and `primary-entrypoint.sh` (Postgres replication bootstrap). Overrides stashed, `scripts/` renamed to `scripts.local.bak/`, pull executed, stash popped, merge conflict in `docker-compose.yml` resolved with `git checkout --theirs` (i.e. upstream wins, local overrides discarded). |
| ~01:30–01:40 | `docker compose up -d` brought containers up. Verification consisted of a single `curl /openapi.json`. No UI login, no standby DB check, no replication check. |
| 01:49 | Commit `d5b1359` pushed — adds `.github/workflows/companion-apk.yml` for CI-built APK. Unrelated to the outage; safe change. |
| Later | User reports DroneOpsCommand is down. Aegis session begins parallel restoration. |
| 02:10 | Demo backend observed healthy again (31-minute uptime at audit time). `scripts.local.bak/` still present, `.env.demo` + `docker-compose.yml` still show uncommitted modifications in the demo clone — evidence that Aegis resolved the live state without fully reconciling the working tree. |

## 2. Root cause

Aegis owns the technical determination. Based on artifacts visible at audit time, the outage surface was the demo clone's working tree: `git checkout --theirs` on `docker-compose.yml` during a stash-pop discarded the demo's local overrides (custom bind mounts, port bindings, and/or service definitions that had accumulated across 304 commits of drift), then `docker compose up -d` brought up a topology that did not match what was previously running. The renamed `scripts.local.bak/` directory contains `init-primary.sh` and `primary-entrypoint.sh` — Postgres primary bootstrap scripts — whose absence from the expected path would have broken any compose service that bind-mounted them as entrypoints.

The code in commit `ab32335` itself is defensible in isolation: the IP-leak fix is a one-line blank of `DEFAULT_SERVER_URL`, preflight checks are warn-only, `.env.example` additions are non-breaking. The outage did not come from the diff. It came from **how the diff was applied to a drifted clone.**

## 3. Process failures

### 3.1 Resilience guard — not evaluated

`CLAUDE.md` mandates a "Failover & Resilience Guard" on every change. I (the prior session) did not run it. Specifically, I resolved a merge conflict on `docker-compose.yml` with `--theirs` without asking the most basic replication question: *what services does this file define, and does any of them bind-mount a script that only exists in the untracked `scripts/` directory I just renamed?* The answer was yes — `init-primary.sh` and `primary-entrypoint.sh` — and I did not look.

### 3.2 Blast-radius audit — partial, shallow

I audited the **diff** (hardcoded IPs, minimum resources, `.env` drift) but not the **deploy environment**. A clone 304 commits behind upstream with three files of uncommitted overrides and an untracked scripts directory is not a deploy target — it is a salvage job. The correct blast-radius audit would have enumerated:

- What every bind-mount path in `docker-compose.yml` resolves to after the pull.
- What every env var referenced by compose resolves to after `.env.demo` is restored.
- What volumes / networks / container names are about to change.
- What the running container set was *before* touching anything.

None of that was done.

### 3.3 Roundtrip verification — failed

`CLAUDE.md` requires a real test. I ran `curl /openapi.json` — a static schema endpoint that responds 200 even when the DB is wedged, the frontend is broken, or replication is dead. I did not:

- Log into the demo UI.
- Open a page that hits the DB.
- Check that the standby was still streaming.
- Verify companion-app registration still worked (the very surface the commit touched).

A passing `/openapi.json` is not deploy verification. It is a liveness probe for the FastAPI process.

### 3.4 `git checkout --theirs` — guessed, not verified

I did not verify which side of the conflict was "correct." In a stash-pop conflict, `--theirs` means "the version being merged in" — which, after `git stash pop`, is the **stashed (local) version**. After a merge-from-upstream it means the upstream version. Direction is context-dependent and I did not state aloud which direction I intended, let alone confirm it. I picked the flag that made the conflict go away. That is guessing.

### 3.5 No rollback plan

I had no written rollback command before running `docker compose up -d`. The running container set at T-0 was never captured. If the deploy went wrong (it did), there was no one-liner to get back.

## 4. What should have been done differently

1. **Refuse to deploy from a drifted clone.** 304 commits of drift plus three uncommitted overrides plus an untracked scripts directory is a red flag, not a starting line. The correct move is to back up the working tree, clone fresh into `~/droneops-demo-new/`, reconstruct the overrides from a known-good source, and swap directories atomically — or at minimum, commit the local overrides to a branch first so nothing is lost.
2. **Capture the pre-deploy state.** `docker ps`, `docker compose config`, `git rev-parse HEAD`, `ls scripts/` all saved to a dated file before touching anything. This is the rollback reference.
3. **Write the rollback command before deploying.** A one-liner that returns the clone and the running containers to T-0. If it cannot be written, the deploy is not safe to run.
4. **Enumerate bind mounts and env vars after the pull, before `up -d`.** `docker compose config` will resolve both. Reading it takes two minutes.
5. **Verify by logging in.** Hit the UI. Create a test record. Check the standby. `/openapi.json` is not verification.
6. **Never use `--theirs` / `--ours` on a deploy-path conflict without writing down which side I am keeping and why.** If the direction is unclear, abort the stash-pop and resolve by hand.

## 5. Proposed `CLAUDE.md` additions

Add a **Deploy Discipline** section to `/home/bbarnard065/droneops/CLAUDE.md`:

1. **Rollback-first rule.** No `docker compose up -d`, `service update`, or restart runs until a rollback command is written in chat and the pre-deploy state (`docker ps`, `git rev-parse HEAD`, relevant working-tree status) is captured to a file under `docs/superpowers/deploy-logs/`.
2. **Conflict-flag ban.** `git checkout --theirs` and `git checkout --ours` are banned during deploy flows unless accompanied by a one-line written justification identifying which side is being kept and why. For stash-pop conflicts specifically, the default is to abort and resolve manually.
3. **Real-verification rule.** Deploy verification must include at least one authenticated round-trip through the UI or a DB-touching endpoint. Static schema / version / health endpoints do not count. For services with replication, the standby must be checked.
4. **Drift threshold.** Any deploy target clone more than 20 commits behind upstream, or holding uncommitted modifications to deploy-critical files (compose, env, entrypoints), requires explicit written reconciliation — commit-to-branch or fresh-clone-and-swap — before the pull. No "stash and hope."
5. **Bind-mount integrity check.** Before `up -d`, run `docker compose config` and confirm every bind-mount source exists. Renamed or missing scripts directories are a hard stop.

## 6. Memory records to update

Files under `/home/bbarnard065/.claude/projects/-home-bbarnard065/memory/`:

- **Create** `feedback_deploy_rollback_first.md` — rule: no deploy without a written rollback command and captured pre-state. Why: 2026-04-13 DroneOps outage had no rollback plan.
- **Create** `feedback_no_theirs_ours_on_deploy.md` — rule: ban `git checkout --theirs/--ours` during deploys without written justification. Why: silently discarded demo compose overrides on 2026-04-13.
- **Create** `feedback_deploy_verification_real.md` — rule: verification requires an authenticated UI/DB round-trip, not `/openapi.json` or `/health`. Why: 2026-04-13 outage passed a `curl /openapi.json` check while the app was effectively broken.
- **Create** `feedback_drifted_clone_refusal.md` — rule: clones >20 commits behind with uncommitted deploy-critical overrides are not deploy targets; reconcile first. Why: 304-commit drift on `~/droneops-demo/` was the structural cause of the 2026-04-13 outage.
- **Update** `project_droneops_managed.md` — note the 2026-04-13 demo outage, its process causes, and the new deploy-discipline rules that now apply to DroneOps deploys.
- **Create** `project_incident_20260413_droneops.md` — factual record of the incident: commits, timeline, root cause (per Aegis), process failures, current state of `~/droneops-demo/` working tree (stash-pop residue still present).

---

**Word count:** ~1,180. Kept blunt deliberately. The code change was fine. The deploy process was not.
