# ADR-0012 — Secret hygiene and leak remediation

- **Status:** Accepted
- **Date:** 2026-05-03
- **Owners:** Aegis (executor), Bill Barnard (operator)
- **Supersedes:** none
- **Related:** ADR-0036 (notifications), `.github/workflows/secret-scan.yml`,
  `.pre-commit-config.yaml`, `.gitleaks.toml`

## Context

GitGuardian flagged commit `5ec9392` (pushed 2026-05-03 07:39:27 UTC) as
exposing a "Generic Password." The commit itself was the right kind of
change — it stopped tracking `.env.demo` and added it to `.gitignore` — but
the deletion diff surfaced the existing values one last time, which is what
the scanner caught.

The actual exposure was historical. These three credential values had been
present in the repo (committed, pushed, public) since v2.53.0:

| Value | Where it lived | Used by |
|------|-----------------|---------|
| `POSTGRES_PASSWORD=DemoDb2026!SecureRandom` | `.env.demo` (deleted in `5ec9392`) AND `docker-compose.demo.yml:30` (literal in `DATABASE_URL`) | `droneops-demo-db` on BOS-HQ |
| `REPLICATION_PASSWORD=SecureDemoRepl2026` | `.env.demo` (deleted in `5ec9392`) | demo replication role (no active demo standby at time of writing) |
| `REPLICATION_PASSWORD=SecureDroneRepl2026` | 6 places in main repo: `docker-compose.yml`, `docker-compose.standby.yml`, `docker-compose.demo-standby.yml`, `scripts/init-primary.sh`, `scripts/init-standby.sh`, `scripts/init-demo-standby.sh`, plus `README.md:489` reference | the prod PG replication role used by `droneops-standby-db` (BOS-HQ primary) → `droneops-db-standby` (CHAD-HQ standby) |

The Cloudflare Tunnel token also referenced in `5ec9392` was already
rotated via the API and the live BOS-HQ connector recreated by the time
GitGuardian fired; the historical token in the diff is invalid.

The third item — the prod replication password — is the worst of the
three. It existed as a `${REPLICATION_PASSWORD:-SecureDroneRepl2026}`
fallback default in compose, AND as a hard-coded literal inside two
standby files' `primary_conninfo=` strings AND as a hard-coded literal
inside two of the init scripts. So even on a fresh clone with no `.env`,
`docker compose up` would silently bring up replication using the leaked
default — same value, every install, public knowledge.

## Decision

### 1. Rotate everything that's still live.

- Demo POSTGRES password (`droneops-demo-db` on BOS-HQ): rotated
  in-place, new value placed in `~/droneops-demo/.env` and
  `~/droneops-demo/.env.demo`.
- Demo REPLICATION password: rotated. (No active demo standby exists
  right now per `pg_stat_replication` showing 0 rows on the demo
  primary — but the role's password has been changed regardless.)
- Prod REPLICATION password (`droneops-standby-db` BOS-HQ → `droneops-db-standby`
  CHAD-HQ): rotated atomically on both sides during a brief replication-
  paused window. Verified streaming resumes via
  `SELECT * FROM pg_stat_replication;` returning a row with
  `state=streaming` and `pg_wal_lsn_diff(sent_lsn, replay_lsn) = 0`.

No new credential value lands in this ADR or in any commit.

### 2. Eliminate plaintext fallbacks. `:?required` is the new pattern.

Every place that previously read `${VAR:-<literal_secret>}` becomes
`${VAR:?VAR must be set in .env (no default — see ADR-0012)}`. If the
operator forgets to set the var, the container refuses to start with a
clear error, instead of silently using a leaked default.

Files changed:
- `docker-compose.yml` — `POSTGRES_PASSWORD`, `REPLICATION_PASSWORD`,
  `JWT_SECRET_KEY`, `DATABASE_URL` (the 4 backend-services env blocks).
- `docker-compose.standby.yml`, `docker-compose.demo-standby.yml` —
  `primary_conninfo` now interpolates `${REPLICATION_PASSWORD:?…}` via
  a separately-injected env var instead of a literal in the command line.
- `scripts/init-primary.sh`, `scripts/init-standby.sh`,
  `scripts/init-demo-standby.sh` — `: "${REPLICATION_PASSWORD:?…}"`
  guard at the top, no fallback default.
- `README.md` — table cell that listed the literal default replaced with
  *(no default — required)*.
- `.env.example` — vars that are now required have empty values and a
  REQUIRED comment.

### 3. Pre-commit + CI gates.

- `.pre-commit-config.yaml` adds the `gitleaks` hook (`protect --staged
  --no-banner`) plus baseline file-hygiene + `detect-private-key`. Every
  developer runs `pre-commit install` once after cloning.
- `.gitleaks.toml` extends the upstream default ruleset with two
  repo-specific rules:
  1. Block reintroduction of any of the now-rotated literals by exact
     value (so a paste-from-history can't slip back in).
  2. Block compose `${VAR:-<long_literal>}` fallback patterns for any
     `*PASSWORD/SECRET/TOKEN/KEY` env var.
- `.github/workflows/secret-scan.yml` runs the same gitleaks binary on
  every push and PR, on the BOS-HQ self-hosted runner. The job fails the
  PR if any secret is detected. This is the authoritative gate.

### 4. `.gitignore` hardened.

`.env`, `.env.*` (with explicit `!.env.example` allow), `*.pem`, `*.key`,
`*.p12`, `*.pfx`, `*.crt`, `.secrets/`, `secrets.{yaml,yml}`, `.netrc`,
`.pgpass`, `google-credentials.json`, `service-account*.json`. The old
`.env.demo` line is now redundant under `.env.*`.

## Trade-offs

### What we did NOT do: history rewrite (`git filter-repo`)

We **did not** rewrite git history to scrub the leaked values from past
commits. Reasoning:

1. The leaked values are now ROTATED. The historical exposure becomes
   a record of values that no longer authenticate anything. Functional
   impact of the historical leak going forward: zero.
2. `git filter-repo --replace-text` requires a force-push to `main`
   that breaks every existing clone of the repo (CI runners, deploy
   workspaces on BOS-HQ + CHAD-HQ + HSH-HQ, every developer machine).
   On this fleet that's enough surface area to be a meaningful risk.
3. GitGuardian and similar scanners will continue to flag the
   historical commits. We accept that — a future maintainer reading the
   alerts can be pointed at this ADR as the "why these are stale"
   answer.

If the operator decides the historical scrub is worth the breakage at a
later date, the commit range to rewrite is everything from `v2.53.0` (when
the literals first landed) through commit `5ec9392`. The exact replacement
list is:

```
SecureDroneRepl2026   -> ROTATED-ADR0012
DemoDb2026!SecureRandom -> ROTATED-ADR0012
SecureDemoRepl2026    -> ROTATED-ADR0012
```

### What we did NOT do: rotate `JWT_SECRET_KEY`

The JWT secret has not been observed in the public repo as a literal — it
has always been a `:-changeme_generate_a_random_secret` placeholder. We
tightened the compose to `:?required` so future installs can't fall back
to that placeholder, but no rotation of the live JWT secret was performed.
If a JWT key was ever set to the literal placeholder on any host (operator
audit-only check), that key should also be rotated; if it was set from
`openssl rand` like the runbook says, it's fine.

## Operator follow-ups

- [ ] Run `pre-commit install` in your local clone once.
- [ ] Audit any `~/droneops/.env` or `~/droneops-demo/.env` on hosts you
      operate — ensure they explicitly set `JWT_SECRET_KEY`,
      `POSTGRES_PASSWORD`, `REPLICATION_PASSWORD`, `DATABASE_URL` (none of
      these will fall back to anything anymore).
- [ ] Decide whether to commission a `git filter-repo` history rewrite
      (see trade-off above).

## Verification artifacts

- `gitleaks detect --source . --config .gitleaks.toml --no-git` returns
  exit-code 0 on the v2.66.1 tree.
- `docker compose config` against the v2.66.1 tree, with no `.env`
  present, exits non-zero with a clear "is required" error message
  (proves the `:?required` change is wired in).
- BOS-HQ post-rotation: `pg_stat_replication` on `droneops-standby-db`
  returns one row with `state=streaming` and `lag_bytes=0` for the
  CHAD-HQ standby. Demo HTTPS endpoint
  `https://command-demo.barnardhq.com` returns HTTP 200.
