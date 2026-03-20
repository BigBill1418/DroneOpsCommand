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
- `./update.sh dev` — pull `claude/dev`, rebuild, test
- `./update.sh prod` — pull `main`, rebuild, deploy production
- `./update.sh dev --clean` — full rebuild, no Docker cache

## Conventions

- Commit messages: short summary, optional detail paragraph, always end with session link
- Keep UI styling consistent: dark theme, cyan accents, Bebas Neue headings, Share Tech Mono for data
