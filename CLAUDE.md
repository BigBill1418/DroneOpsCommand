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

## Branch Workflow (REQUIRED)

- **`claude/dev`** — development & testing branch (all new work goes here first)
- **`main`** — production-stable (only promoted, tested code)

**Before committing any code change, ALWAYS ask the user:** "Should this go to `claude/dev`, `main`, or both?"
- Do NOT assume a target branch — always ask first.
- Default to `claude/dev` unless told otherwise.

**Server update commands:**
- `./update.sh` — interactive menu (prompts for action)
- `./update.sh dev` — pull `claude/dev`, rebuild, test
- `./update.sh prod` — pull `main`, rebuild, deploy production
- `./update.sh promote` — merge `claude/dev` → `main`, rebuild production
- `./update.sh status` — show branch info & running services
- `./update.sh dev --clean` — full rebuild, no Docker cache

## Decision-Making (REQUIRED)

**Before proposing or making any major architectural change** (removing features, dropping platforms, decommissioning endpoints, changing core workflows), ALWAYS:
1. Research the full context — read related code, git history, and understand WHY the feature exists
2. Identify who/what depends on it and what breaks if it's removed
3. Present findings and trade-offs to the user BEFORE taking action
4. Do NOT remove working functionality just to "simplify" — if it works, keep it unless there's a clear reason to cut it

**Example of what NOT to do:** The DroneOpsSync device upload API was decommissioned in v2.30.0 without fully considering that the browser file picker cannot access DJI app folders on Android/RC Pro. The companion app approach was correct and should not have been removed.

## Conventions

- Commit messages: short summary, optional detail paragraph, always end with session link
- Keep UI styling consistent: dark theme, cyan accents, Bebas Neue headings, Share Tech Mono for data
