# Contributing to DroneOpsCommand

Thanks for your interest in contributing! This guide covers how to get started.

## Getting Started

1. Fork the repository
2. Clone your fork: `git clone https://github.com/BigBill1418/DroneOpsCommand.git`
3. Create a feature branch: `git checkout -b feature/your-feature-name`
4. Make your changes
5. Test locally with Docker Compose (see below)
6. Commit and push to your fork
7. Open a Pull Request against `main`

## Local Development

### Full stack (Docker)

```bash
cp .env.example .env
# Edit .env. Four values have NO default and compose refuses to start without
# them (ADR-0012): POSTGRES_PASSWORD, DATABASE_URL, REPLICATION_PASSWORD,
# JWT_SECRET_KEY.
docker compose up -d
```

The app will be available at `http://localhost:3080`.

### Frontend only

```bash
cd frontend
npm install
npm run dev
```

Vite dev server runs on `http://localhost:5173` and proxies API calls to the backend.

### Backend only

```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Requires PostgreSQL and Redis running (easiest via `docker compose up db redis -d`).

## Tests

There is **no pytest job and no cargo job in CI** — `.github/workflows/` holds
only `auto-merge-claude.yml`, `secret-scan.yml` (gitleaks) and
`self-hosted-smoke-test.yml`. Nothing runs the suites at merge time, so run them
yourself and quote the real output in your PR description:

```bash
cd backend && OTEL_EXPORTER_OTLP_ENDPOINT="" pytest -q   # needs the Dockerfile's native libs (pango/cairo/geos/proj) + aiosqlite
cd flight-parser && cargo test
```

Blank `OTEL_EXPORTER_OTLP_ENDPOINT` explicitly — unset, it falls back to a real
fleet collector and buries the pytest summary in exporter errors.

## Code Style

- **Backend**: Python with type hints. FastAPI async endpoints. SQLAlchemy 2.0 async ORM.
- **Frontend**: TypeScript, React 18, Mantine UI v7. Dark theme with cyan accents.
- **Fonts**: Bebas Neue for headings, Share Tech Mono for data/monospace, Rajdhani for general UI.
- **Commit messages**: Short summary line, optional detail paragraph.

## What to Work On

- Check the [Issues](https://github.com/BigBill1418/DroneOpsCommand/issues) tab for open bugs and feature requests
- Items in [`ROADMAP.md`](ROADMAP.md) and the **Roadmap** section of the README
- Bug fixes and documentation improvements are always welcome

## Pull Request Guidelines

- Keep PRs focused — one feature or fix per PR
- Include a description of what changed and why
- Test your changes locally before submitting
- Update the README **and `.env.example`** if your change adds or removes configuration, and add a dated `CHANGELOG.md` entry
- Bump the version — see `CLAUDE.md` for the 6-file / 7-location list

## Architecture Overview

| Service | Tech | Purpose |
|---------|------|---------|
| Frontend | React 18 + Vite + Mantine UI v7 | SPA web interface |
| Backend | FastAPI + SQLAlchemy 2.0 (async) | REST API |
| Database | PostgreSQL 16 | Persistent storage |
| Flight Parser | Rust (axum) microservice, port 8100 | DJI / Litchi / Airdata flight log decryption |
| LLM | Ollama (Llama 3.1 8B Instruct `q4_K_M`) or Claude API | AI report generation |
| Queue | Redis 7 + Celery | Async task processing |

## Reporting Bugs

Open an issue with:
- Steps to reproduce
- Expected vs actual behavior
- Docker Compose logs if applicable (`docker compose logs backend`)

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
