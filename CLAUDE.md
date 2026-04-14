# CLAUDE.md — Project instructions for Claude Code

## Failover & Resilience Guard (MANDATORY)

Every code change MUST be evaluated for its impact on failover, blue-green deployment, and replication BEFORE being made. This is non-negotiable.

Before committing ANY change, ask:
1. Will this break PostgreSQL streaming replication? (port bindings, pg_hba, connection strings)
2. Will this survive a container recreation? (runtime-only changes vs init scripts/volumes)
3. Will this break the blue-green swap flow? (standby-first deploy, fencing, promotion)
4. Will this break the failover engine? (quorum voting, health monitoring, WireGuard connectivity)
5. Will this affect any customer-facing service during a site failover?

If the answer to ANY of these is yes — either find an alternative approach that preserves resilience, or do not make the change. There are no exceptions.


## Version Bumping (REQUIRED on every code change)

Every commit that changes application code MUST include a version bump. Use semantic versioning (MAJOR.MINOR.PATCH). Bump PATCH for fixes/tweaks, MINOR for new features, MAJOR for breaking changes.

Update the version in ALL 4 of these files:

1. `README.md` — line near top: `**Version X.Y.Z**`
2. `frontend/package.json` — `"version": "X.Y.Z"`
3. `backend/app/main.py` — `version="X.Y.Z"` in the FastAPI() call
4. `frontend/src/components/Layout/AppShell.tsx` — `vX.Y.Z` displayed in the navbar footer

Include the version tag in the commit message (e.g. `— v1.7.8`).

## Tech Stack

- **Backend**: Python / FastAPI, SQLAlchemy, PostgreSQL
- **Frontend**: React / TypeScript, Mantine UI, Vite
- **Infrastructure**: Docker Compose (self-hosted)
- **Deploy**: `update.sh` pulls latest, rebuilds changed services

## Branch Workflow (REQUIRED)

All commits go directly to **`main`**. There is no `claude/dev` branch anymore — commit, push, deploy. No promotion step, no dev/prod split.

**Server update commands:**
- `./update.sh` — pull `main`, rebuild changed services, restart
- `./update.sh --clean` — full rebuild, no Docker cache
- `./update.sh status` — show branch info & running services

## Decision-Making (REQUIRED)

**Before proposing or making any major architectural change** (removing features, dropping platforms, decommissioning endpoints, changing core workflows), ALWAYS:
1. Research the full context — read related code, git history, and understand WHY the feature exists
2. Identify who/what depends on it and what breaks if it's removed
3. Present findings and trade-offs to the user BEFORE taking action
4. Do NOT remove working functionality just to "simplify" — if it works, keep it unless there's a clear reason to cut it

**Example of what NOT to do:** The DroneOpsSync device upload API was decommissioned in v2.30.0 without fully considering that the browser file picker cannot access DJI app folders on Android/RC Pro. The companion app approach was correct and should not have been removed.

## Logging & Troubleshooting (REQUIRED)

Every new feature or change MUST include logging and troubleshooting support, especially for:

- **Networking / API communication** — Log request/response status, errors, timeouts, and connection failures. On the frontend, failed API calls should show clear user-facing error messages (not silent failures or blank reloads).
- **Authentication & authorization flows** — Log login attempts, token refresh failures, lockouts, and password resets with enough context to diagnose issues from logs alone.
- **Complex operations** — Database migrations, seed operations, file uploads, background tasks, external service calls — all should log start/success/failure with relevant context (IDs, durations, error details).
- **Frontend error handling** — API errors must propagate to the UI as visible notifications. Never swallow errors silently. Use try/catch with meaningful messages, not empty catch blocks.

**Minimum standards:**
- Backend: Use Python `logging` with the `doc.*` logger namespace. Include context (user, IP, entity ID) in log messages.
- Frontend: Failed API calls → Mantine notification with actionable error message. Console.error for debugging detail.
- New endpoints: Log entry, success, and failure at minimum.
- If a feature can fail in a way that's hard to diagnose remotely, add a health-check or diagnostic endpoint/log.

**Lesson learned:** The v2.38.x login lockout took multiple attempts to diagnose because the axios interceptor silently swallowed 401 errors and reloaded the page — no error was logged or shown to the user.

## Repair & Fix Quality Standard (REQUIRED)

When asked to repair or fix something, you MUST be thorough. Do not apply a surface-level patch and move on. Follow this process:

1. **Audit the full blast radius** — Find EVERY file, config, environment variable, Docker setting, and dependency that touches the broken system. Use grep/search across the entire codebase, not just the obvious files.
2. **Research best practices** — Look at what others have done that works. Check for known incompatibilities (e.g. passlib + bcrypt >= 4.0). Don't just fix symptoms — fix root causes.
3. **Check for secondary failures** — After making a fix, trace through what happens on container rebuild, server restart, database migration, and fresh deploy. If the fix only works until the next restart, it's not a fix.
4. **Verify defaults and environment** — Check config defaults, docker-compose.yml env vars, .env files. A config that defaults to the wrong value will silently undo your fix on every restart.
5. **Test the roundtrip** — If you change how data is written (e.g. password hashing), verify it can also be READ back correctly before committing. Don't assume it works.
6. **Log everything** — Every fix must include logging that would make the NEXT failure diagnosable from logs alone, without needing another debugging session.

**Lesson learned:** The v2.38.6 auth rebuild replaced passlib with direct bcrypt (correct fix) but missed that `reset_admin_password` defaulted to `True` in config.py, causing every container restart to overwrite the admin password. The fix only lasted until the next deploy.

## Conventions

- Commit messages: short summary, optional detail paragraph, always end with session link
- Keep UI styling consistent: dark theme, cyan accents, Bebas Neue headings, Share Tech Mono for data


# Documentation Discipline (MANDATORY)

Any code change, feature change, or forward-looking plan MUST be recorded in a logical place in this repo as part of the same change. The goal: any future session can pick up exactly where the last one left off.

- **Code/feature changes** → update `CHANGELOG.md` (or equivalent) with date + summary.
- **Future plans / roadmap items** → add to `ROADMAP.md`, `docs/plans/`, or an ADR under `docs/adr/`.
- **Non-obvious decisions** → record as an ADR (`docs/adr/NNNN-title.md`).
- **Progress on in-flight work** → update `PROGRESS.md` or the relevant plan doc.

No undocumented changes. If a repo lacks the right file, create it. Commit the docs alongside the code — never in a separate follow-up that might get forgotten.
