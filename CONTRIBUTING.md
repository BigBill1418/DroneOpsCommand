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
# Edit .env with your settings
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

## Code Style

- **Backend**: Python with type hints. FastAPI async endpoints. SQLAlchemy 2.0 async ORM.
- **Frontend**: TypeScript, React 18, Mantine UI v7. Dark theme with cyan accents.
- **Fonts**: Bebas Neue for headings, Share Tech Mono for data/monospace, Rajdhani for general UI.
- **Commit messages**: Short summary line, optional detail paragraph.

## What to Work On

- Check the [Issues](../../issues) tab for open bugs and feature requests
- Items in the **Roadmap** section of the README
- Bug fixes and documentation improvements are always welcome

## Pull Request Guidelines

- Keep PRs focused — one feature or fix per PR
- Include a description of what changed and why
- Test your changes locally before submitting
- Update the README if your change adds new configuration or features

## Architecture Overview

| Service | Tech | Purpose |
|---------|------|---------|
| Frontend | React 18 + Vite + Mantine UI v7 | SPA web interface |
| Backend | FastAPI + SQLAlchemy 2.0 (async) | REST API |
| Database | PostgreSQL 16 | Persistent storage |
| Flight Parser | Python microservice | DJI flight log decryption |
| LLM | Ollama (Qwen 2.5 3B) or Claude API | AI report generation |
| Queue | Redis 7 + Celery | Async task processing |

## Reporting Bugs

Open an issue with:
- Steps to reproduce
- Expected vs actual behavior
- Docker Compose logs if applicable (`docker compose logs backend`)

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
