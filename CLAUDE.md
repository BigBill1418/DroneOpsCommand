# CLAUDE.md — Project instructions for Claude Code

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

## Conventions

- Commit messages: short summary, optional detail paragraph, always end with session link
- Keep UI styling consistent: dark theme, cyan accents, Bebas Neue headings, Share Tech Mono for data

## Decision-Making

Research full context before any major architectural change. Read every affected file, trace the full call chain, and understand existing patterns before proposing anything. Never remove working functionality just to simplify. If something works, preserve it — refactor around it, not through it. When in doubt, add redundancy rather than strip it away.

## Logging & Troubleshooting

Every change must include logging. Backend: use the `doc.*` logger namespace. Frontend: surface API errors via Mantine notifications — never swallow errors silently. Log enough context to diagnose the next failure without a debugger: what was attempted, what the inputs were, and what the outcome was.

## Repair & Fix Quality Standard — 6-Step Process

Before shipping any fix, work through all six steps:

1. **Audit full blast radius** — map every file, service, and code path touched by the change
2. **Research best practices** — check existing patterns in the codebase first, then external references
3. **Check for secondary failures** — will this survive a container restart, a DB reconnect, a cold start?
4. **Verify defaults and environment** — confirm env vars, config files, and fallback values are correct
5. **Test the roundtrip** — trace the full path: UI action → API call → DB write → response → UI update
6. **Log everything** — instrument the fix so the next failure is diagnosable in seconds

## Engineering Mindset

Think redundantly. Plan for the failure case before the happy path. When something is complicated, the goal is to make it simple for the operator — not to expose the complexity. Find the pattern that makes the hard thing automatic. The system must function at all times; resilience is a feature, not an afterthought.
