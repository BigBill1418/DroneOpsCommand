# DroneOpsReport

Invoicing and after-action reporting tool for drone operations. Built for [BarnardHQ](https://barnardhq.com).

## Features

- **Mission Management** — Create and track drone operations (SAR, videography, lost pet recovery, inspections, mapping)
- **OpenDroneLog Integration** — Pull flight logs from your self-hosted OpenDroneLog instance, select flights per mission
- **Multi-Flight Path Map** — Interactive Leaflet map showing all flight paths with colored overlays and area coverage in acres
- **AI Report Generation** — Local LLM (Ollama/Mistral) interprets flight data + operator notes to write professional client reports
- **Aircraft Profiles** — DJI fleet management with product specs displayed in reports
- **PDF Export** — Branded PDF reports with BarnardHQ styling, flight maps, aircraft specs, imagery, and invoicing
- **Invoicing** — Line items for travel, billed time, rapid deployment, equipment, and custom fees with rate templates
- **Rich Text Editing** — TipTap-powered editor for report narrative with full formatting controls
- **Customer CRM** — Track customers, contact info, and job history
- **Email Delivery** — Send PDF reports directly to customers with social media links
- **JWT Authentication** — Secure API ready for future Android companion app

## Quick Start

```bash
# 1. Copy environment config
cp .env.example .env
# Edit .env with your settings (SMTP, OpenDroneLog URL, admin password, JWT secret)

# 2. Launch all services
docker-compose up -d

# 3. Wait for Ollama to pull the Mistral model (first run only, ~4GB download)
docker-compose logs -f ollama-setup

# 4. Access the app
# Web UI: http://localhost
# API: http://localhost:8000/docs
# Default login: admin / changeme_in_production
```

## Architecture

| Service | Tech | Port |
|---------|------|------|
| Frontend | React + Vite + Mantine | 80 |
| Backend | Python FastAPI | 8000 |
| Database | PostgreSQL 16 | 5432 |
| LLM | Ollama (Mistral 7B) | 11434 |
| Queue | Redis + Celery | 6379 |

## Configuration

All settings via environment variables (`.env` file):

- `OPENDRONELOG_URL` — Your OpenDroneLog instance URL
- `OLLAMA_MODEL` — LLM model (default: `mistral:7b-instruct-v0.3-q4_K_M`)
- `SMTP_HOST/PORT/USER/PASSWORD` — Email server for sending reports
- `JWT_SECRET_KEY` — Change this to a random secret for production
- `ADMIN_USERNAME/PASSWORD` — Initial admin credentials

## Phase 2 Roadmap

- **React Native Android App** — Mission creation, photo capture on-site, report review, and customer lookup from the field. Communicates with the stack via JWT-authenticated HTTPS API.
- **Voice-to-Text** — On-device speech recognition in the Android app for dictating operator field notes hands-free during or after missions.
- **Report Templates** — Multiple PDF templates for different mission types
- **Analytics** — Revenue tracking and mission type breakdown
- **Live Flight Tracking** — WebSocket integration for real-time drone position

## Development

```bash
# Backend
cd backend && pip install -r requirements.txt
uvicorn app.main:app --reload

# Frontend
cd frontend && npm install && npm run dev
```

## License

Private — BarnardHQ
