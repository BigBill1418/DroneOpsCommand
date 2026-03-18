# DroneOpsCommand

**Self-hosted mission management, AI report generation, and invoicing for commercial drone operators.**

**Version 2.10.0** | [Quick Start](#quick-start) | [Features](#features) | [Configuration](#configuration) | [Contributing](CONTRIBUTING.md) | [License](LICENSE)

---

DroneOpsCommand is a self-hosted, full-stack platform for managing commercial drone operations end-to-end. It covers the complete lifecycle from flight data ingestion through AI-powered report generation, invoicing, and client delivery.

Designed for FAA Part 107 certified operators running missions such as search & rescue, inspections, mapping, videography, and more.

### Why DroneOpsCommand?

- **100% self-hosted** — runs on your own hardware via Docker Compose. No cloud dependencies, no per-seat licensing.
- **AI stays local** — report generation uses Ollama (Mistral 7B) so client data never leaves your network.
- **White-label ready** — company name, tagline, and branding are fully configurable from the Settings UI. No code changes needed to make it yours.
- **Full lifecycle** — mission creation, flight log import, map generation, AI reports, PDF export, invoicing, and email delivery in one platform.

---

## Table of Contents

- [Quick Start](#quick-start)
- [Features](#features)
- [Architecture](#architecture)
- [Configuration](#configuration)
- [Updating](#updating)
- [Pages & Workflows](#pages--workflows)
- [Backend Services](#backend-services)
- [API Reference](#api-reference)
- [Roadmap](#roadmap)
- [Development](#development)
- [Contributing](#contributing)
- [License](#license)

---

## Quick Start

### Prerequisites

- [Docker](https://docs.docker.com/get-docker/) and [Docker Compose](https://docs.docker.com/compose/install/) (v2+)
- 8GB+ RAM recommended (Ollama loads a 4GB quantized model)
- x86_64 or ARM64 host

### Setup

```bash
# 1. Clone and configure
git clone https://github.com/YOUR_USERNAME/DroneOpsCommand.git
cd DroneOpsCommand
cp .env.example .env

# 2. Set your secrets (IMPORTANT: change these before first run)
#    Edit .env and update at minimum:
#    - POSTGRES_PASSWORD
#    - JWT_SECRET_KEY
#    - ADMIN_PASSWORD

# 3. Launch
docker compose up -d

# 4. Wait for the AI model to download (first run only, ~4GB)
docker compose logs -f ollama-setup

# 5. Open the app
#    Web UI:   http://localhost:3080
#    API docs: http://localhost:8000/docs
#    Login:    admin / (your ADMIN_PASSWORD from .env)
```

### What happens on first startup

1. PostgreSQL schema is created automatically
2. Admin user is seeded with your configured credentials
3. Aircraft fleet (6 DJI models) and rate templates (8 billing presets) are pre-loaded
4. Ollama downloads and loads the Mistral 7B model
5. All storage directories are created

### Personalize it

After logging in, go to **Settings > Branding** to set your company name, tagline, website, and contact email. These appear on PDF reports, emails, the login page, and customer-facing pages.

---

## Features

### Mission Management
- Create and track drone missions across 9 mission types: Search & Rescue, Videography, Lost Pet Recovery, Inspection, Mapping, Photography, Survey, Security & Investigations, Other
- Multi-step mission wizard: Details, Flights, Images, Report, Invoice
- Mission status tracking: Draft, Completed, Sent
- Billable/non-billable designation per mission
- Edit existing missions at any step

### OpenDroneLog Integration
- Pull flight logs from your self-hosted OpenDroneLog instance
- Select and attach specific flights to each mission
- GPS track extraction with telemetry data (altitude, speed, distance, duration)
- Automatic data normalization across OpenDroneLog API versions
- Connection testing from Settings page

### Flight Data & Statistics
- Aggregate flight statistics: total flights, total time, total distance, max altitude, max speed
- Per-drone breakdown of flight time (visual bar chart)
- Top flights by duration and distance
- Searchable flight log table with unit conversions (meters to feet/miles, m/s to mph)
- Average distance and duration per flight

### Multi-Flight Path Maps
- Interactive Leaflet map showing all flight paths with color-coded overlays
- Convex hull polygon showing total coverage area boundary
- Start/end point markers for each flight
- Coverage area calculation in acres (with 30m buffer for camera swath simulation)
- Static map PNG generation for PDF embedding (OpenStreetMap tiles)
- UTM coordinate conversion for accurate area measurement via Shapely/PyProj

### AI Report Generation
- Local LLM via Ollama (Mistral 7B by default) — your data stays on your hardware
- Operator enters field notes/narrative, LLM generates professional after-action report
- Structured report sections: Mission Overview, Area Coverage, Flight Operations Summary, Key Findings, Recommendations
- Async generation via Celery worker with status polling
- Editable output — review and modify the generated report before finalizing
- LLM status monitoring on Settings page (online/offline, loaded model)
- Configurable model, temperature, and token limits

### Rich Text Editing
- TipTap-powered WYSIWYG editor for report narratives
- Full formatting: bold, italic, underline, strikethrough, headings, lists, blockquotes, code, alignment, links
- Edit both operator narrative and final report content

### Aircraft Fleet Management
- Pre-seeded DJI aircraft profiles: Matrice 30T, Matrice 4TD, Mavic 3 Pro, Avata 2, FPV, Mini 5 Pro
- Detailed specifications per aircraft: flight time, max speed, camera, thermal imaging, sensors, weight, transmission range
- Add/edit/delete aircraft with custom specs (stored as JSON)
- Assign aircraft to individual flights within a mission
- Aircraft cards displayed in mission detail and PDF reports

### PDF Export
- Branded PDF reports with custom company branding via WeasyPrint
- Includes: mission metadata, report narrative, flight map, aircraft specs, mission images with captions
- Invoice section with line items, totals, tax calculation
- Payment links (PayPal/Venmo) for unpaid invoices
- Optional client download link for mission footage
- Generated timestamp and mission ID

### Invoicing
- Per-mission invoicing with automatic duplicate prevention
- Line items with 6 categories: Billed Time, Travel, Rapid Deployment, Equipment, Special Circumstances, Other
- Quantity, unit price, and calculated line totals
- Automatic subtotal, configurable tax rate, tax amount, and grand total
- Paid-in-full tracking
- Rate templates for quick line item creation (configurable in Settings)
- Sort ordering for line item display

### Rate Templates
- Reusable billing templates: Standard Hourly Rate, Mileage, Flat Rate Travel, Rapid Deployment, Night Operations Surcharge, Thermal Imaging, Video Editing, Report Preparation
- Configurable default quantity, unit (hours, miles, flat, each), and rate
- Active/inactive toggle
- Add/edit/delete from Settings page

### Customer CRM
- Customer profiles: name, company, email, phone, address, notes
- Address auto-complete via OpenStreetMap/Nominatim geocoding
- Search across all customer fields
- Customer linked to missions for reporting and email delivery
- Job history tracking

### Email Delivery
- Send PDF reports directly to customer email
- Async SMTP with TLS support via aiosmtplib
- HTML email body with mission title and optional download link
- PDF attached automatically
- SMTP configuration via Settings page with test email button
- Mission status updated to "Sent" after successful delivery

### UNAS NAS Integration
- Store mission footage folder paths (UNAS/Synology NAS)
- Paste share links from UNAS web interface with expiration dates
- Active/expired link status badge with date tracking
- Optional inclusion of download link in client reports and emails
- Supports file paths with special characters, unicode, and spaces

### Weather & Airspace Intelligence
- Real-time weather conditions via Open-Meteo API: temperature, humidity, wind speed/direction/gusts, cloud cover, visibility, pressure
- METAR aviation weather from AviationWeather.gov: flight category (VFR/MVFR/IFR/LIFR), raw METAR string, cloud layers
- FAA Temporary Flight Restrictions (TFRs) from AviationWeather.gov/FAA GeoJSON
- NOTAMs (Notices to Airmen) with classification and effective dates
- National Weather Service alerts with severity levels
- Wind severity indicator: favorable, caution, hazardous
- Configurable location from Settings page

### Financial Dashboard
- Total billed revenue, average per mission, billable mission count
- Revenue breakdown by: drone/aircraft, line item category, mission type, month, customer
- Top customers by revenue with mission count
- Paid vs. outstanding tracking and collection rate percentage
- Searchable invoice table across all missions
- Prepaid status badges

### Authentication & Security
- JWT access tokens (configurable expiration, default 30 min)
- Refresh token rotation (configurable, default 30 days)
- Secure password hashing via passlib/bcrypt
- All API endpoints require authentication
- Admin account seeded on first startup

### Dashboard
- At-a-glance stats: total flights, total missions, drafts, customers
- Recent missions table with status badges
- Live weather conditions and flight conditions assessment
- METAR aviation weather with color-coded flight categories
- FAA TFR and NOTAM alerts
- NWS weather alerts with severity
- Animated drone graphic with dark theme styling

### Image Management
- Upload mission images with drag-and-drop or file picker
- Automatic image resizing (max 1920px) for report optimization
- EXIF orientation correction
- JPEG conversion for consistency and file size reduction
- Captions per image
- Sort ordering for report display
- Images embedded in PDF reports

---

## Architecture

| Service | Technology | Port | Purpose |
|---------|-----------|------|---------|
| Frontend | React 18 + Vite + Mantine UI | 80 (nginx) | SPA web interface |
| Backend | Python 3.12, FastAPI, SQLAlchemy 2.0 | 8000 | REST API |
| Database | PostgreSQL 16 Alpine | 5432 | Persistent storage |
| LLM | Ollama (Mistral 7B quantized) | 11434 | Local AI report generation |
| Queue | Redis 7 Alpine | 6379 | Celery task broker |
| Worker | Celery (same backend image) | — | Async report generation |

### Frontend Stack
- **React 18** with TypeScript
- **Mantine UI v7** component library with dark theme
- **Vite** build tool
- **React Router** for SPA navigation
- **TipTap** rich text editor
- **Leaflet** interactive maps
- **Axios** HTTP client with JWT interceptors and token refresh
- **Mantine Dates** for date inputs
- **Tabler Icons** icon set
- Custom fonts: Bebas Neue (display), Share Tech Mono (monospace), Rajdhani (UI)

### Backend Stack
- **FastAPI** async REST framework
- **SQLAlchemy 2.0** async ORM with asyncpg driver
- **Pydantic v2** request/response validation
- **WeasyPrint** HTML-to-PDF rendering (Cairo/Pango)
- **Jinja2** HTML templates for PDF and email
- **Shapely + PyProj** geospatial calculations
- **staticmap** static map image generation
- **Pillow** image processing
- **aiosmtplib** async email delivery
- **httpx** async HTTP client (Ollama, OpenDroneLog, weather APIs)
- **Celery** distributed task queue
- **passlib + bcrypt** password hashing

### Nginx Reverse Proxy
- Proxies `/api/` to backend on port 8000
- Proxies `/static/` and `/uploads/` to backend
- SPA fallback (`try_files` to `index.html`)
- Gzip compression enabled
- 50MB max upload size, 300s read timeout

---

## Configuration

All settings are configured via environment variables in the `.env` file.

### Database
| Variable | Default | Description |
|----------|---------|-------------|
| `POSTGRES_USER` | `doc` | PostgreSQL username |
| `POSTGRES_PASSWORD` | `changeme_in_production` | PostgreSQL password |
| `POSTGRES_DB` | `doc` | Database name |
| `DATABASE_URL` | `postgresql+asyncpg://...` | Full async connection string |

### Authentication
| Variable | Default | Description |
|----------|---------|-------------|
| `JWT_SECRET_KEY` | `changeme_generate_a_random_secret` | **Change this** — used to sign tokens |
| `JWT_ALGORITHM` | `HS256` | Token signing algorithm |
| `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` | `30` | Access token lifetime |
| `JWT_REFRESH_TOKEN_EXPIRE_DAYS` | `30` | Refresh token lifetime |
| `ADMIN_USERNAME` | `admin` | Initial admin account |
| `ADMIN_PASSWORD` | `changeme_in_production` | Initial admin password |

### Integrations
| Variable | Default | Description |
|----------|---------|-------------|
| `OPENDRONELOG_URL` | *(empty)* | Your OpenDroneLog server URL (e.g., `http://192.168.1.50:8080`) |
| `OLLAMA_BASE_URL` | `http://ollama:11434` | Ollama API endpoint |
| `OLLAMA_MODEL` | `mistral:7b-instruct-v0.3-q4_K_M` | LLM model for report generation |

### Email (SMTP)
| Variable | Default | Description |
|----------|---------|-------------|
| `SMTP_HOST` | *(empty)* | SMTP server hostname |
| `SMTP_PORT` | `587` | SMTP port |
| `SMTP_USER` | *(empty)* | SMTP username |
| `SMTP_PASSWORD` | *(empty)* | SMTP password |
| `SMTP_FROM_EMAIL` | *(empty)* | Sender email address |
| `SMTP_FROM_NAME` | *(empty)* | Sender display name |
| `SMTP_USE_TLS` | `true` | Enable TLS encryption |

SMTP settings can also be configured from the Settings page in the web UI (stored in database, overrides env vars).

### Storage
| Variable | Default | Description |
|----------|---------|-------------|
| `UPLOAD_DIR` | `/data/uploads` | Mission image storage path |
| `REPORTS_DIR` | `/data/reports` | Generated PDFs and map images |

### Ollama Performance Tuning
The `docker-compose.yml` pins Ollama to 6 CPU cores (leaving 2 for the OS and database), sets 8GB RAM reservation, enables flash attention, and keeps the model loaded permanently (`OLLAMA_KEEP_ALIVE=-1`).

---

## Updating

An `update.sh` script is included for deployments. It:

1. Pulls the latest code from the branch
2. Detects which services changed (frontend, backend, or both)
3. Rebuilds only the changed Docker images
4. Restarts services with `docker compose up -d`
5. Tracks deployed commits to avoid unnecessary rebuilds

```bash
# Standard update — rebuilds only changed services
./update.sh

# Force rebuild everything (no Docker cache)
./update.sh --clean

# Rebuild all services even if no changes detected
./update.sh --all
```

---

## Pages & Workflows

### Dashboard (`/`)
Landing page with mission stats, recent missions table, real-time weather conditions, METAR aviation data, FAA TFR/NOTAM alerts, NWS weather alerts, and flight condition assessment.

### Missions (`/missions`)
List all missions with status badges (Draft/Completed/Sent), billable indicators, search, and quick actions (edit, delete). Click a row to view mission details.

### New Mission (`/missions/new`)
Multi-step wizard with 5 stages:

1. **Details** — Customer, title, type, date, location, description, billable toggle, UNAS folder path, download link URL with expiration
2. **Flights** — Browse OpenDroneLog flights, select flights for the mission, assign aircraft to each flight, view flight map and coverage area
3. **Images** — Upload mission photos (drag-and-drop or file picker), auto-resized and EXIF-corrected
4. **Report** — Enter operator narrative, generate LLM report, edit in rich text editor, generate PDF, email to customer
5. **Invoice** — Add line items from rate templates or manually, set quantities and rates, configure tax, mark paid/unpaid

### Mission Detail (`/missions/:id`)
Full mission view with metadata, assigned aircraft cards, interactive flight map, coverage stats, download link status, report content, and action buttons (edit, delete, generate PDF, email report).

### Flights (`/flights`)
Aggregate flight statistics from OpenDroneLog with per-drone breakdowns, top flights, and a searchable table of all flights.

### Customers (`/customers`)
Customer CRM with add/edit/delete, address auto-complete via OpenStreetMap geocoding, and search.

### Financials (`/financials`)
Revenue dashboard with total/average/outstanding metrics, breakdowns by drone, category, mission type, month, and customer. Full invoice table with search.

### Settings (`/settings`)
System configuration:
- **LLM Status** — Ollama connection status and loaded model
- **OpenDroneLog** — Server URL with connection test
- **SMTP** — Email server configuration with test email
- **Payment Links** — PayPal and Venmo URLs for invoices
- **Aircraft Fleet** — Add/edit/delete aircraft with specifications
- **Rate Templates** — Add/edit/delete billing rate presets
- **Branding** — Company name, tagline, website, social media, contact email — used in PDF reports, emails, login page, and customer-facing pages

---

## Backend Services

### PDF Generator
Renders branded PDF reports from Jinja2 HTML templates via WeasyPrint. Includes mission metadata, report narrative, flight path map, aircraft specs, mission images, invoice with line items and totals, payment links, and optional download link.

### Email Service
Async SMTP client that sends HTML emails with the PDF report attached. Loads configuration from database settings first, falls back to environment variables. Supports TLS.

### Ollama LLM Client
HTTP client to the Ollama `/api/generate` endpoint. Sends a structured prompt with mission data, flight telemetry, and operator notes. Returns a professional after-action report. Temperature 0.3 for consistency, 300s timeout, 6 CPU threads.

### OpenDroneLog Client
REST client that fetches flight data from a self-hosted OpenDroneLog instance. Handles multiple API endpoint patterns for version compatibility. Normalizes field names between camelCase and snake_case. Extracts GPS tracks for map rendering.

### Map Renderer
Generates GeoJSON FeatureCollections with flight path LineStrings, start/end markers, and convex hull polygons. Calculates coverage area in acres using UTM projection and Shapely geometry with configurable buffer distance. Renders static PNG maps using OpenStreetMap tiles.

### Weather Service
Aggregates data from 4 external APIs: Open-Meteo (current conditions), AviationWeather.gov (METAR, TFRs), aviationapi.com (NOTAMs fallback), and NWS (weather alerts). Location configurable from Settings.

---

## API Reference

Full interactive API documentation is available at `http://localhost:8000/docs` (Swagger UI) when the backend is running.

### Core Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/auth/login` | Authenticate and receive JWT tokens |
| POST | `/api/auth/refresh` | Refresh access token |
| GET/POST | `/api/missions` | List or create missions |
| GET/PUT/DELETE | `/api/missions/{id}` | Read, update, or delete a mission |
| POST | `/api/missions/{id}/flights` | Attach a flight to a mission |
| POST | `/api/missions/{id}/images` | Upload a mission image |
| GET/POST | `/api/missions/{id}/report/generate` | Generate LLM report (async) |
| GET | `/api/missions/{id}/report/status/{task_id}` | Poll report generation status |
| PUT | `/api/missions/{id}/report` | Save/update report content |
| POST | `/api/missions/{id}/report/pdf` | Generate and download PDF |
| POST | `/api/missions/{id}/report/send` | Email PDF report to customer |
| GET/POST | `/api/missions/{id}/invoice` | Get or create invoice |
| POST | `/api/missions/{id}/invoice/items` | Add line item to invoice |
| GET | `/api/missions/{id}/map` | Get flight path GeoJSON |
| GET | `/api/missions/{id}/map/coverage` | Get coverage area in acres |
| POST | `/api/missions/{id}/map/render` | Generate static map PNG |
| GET/POST/PUT/DELETE | `/api/customers` | Customer CRUD |
| GET/POST/PUT/DELETE | `/api/aircraft` | Aircraft CRUD |
| GET | `/api/flights` | List flights from OpenDroneLog |
| GET | `/api/financials/summary` | Financial dashboard data |
| GET | `/api/weather/current` | Weather, METAR, TFRs, NOTAMs, NWS alerts |
| GET/PUT | `/api/settings/smtp` | SMTP configuration |
| POST | `/api/settings/smtp/test` | Send test email |
| GET/PUT | `/api/settings/opendronelog` | OpenDroneLog URL |
| GET/PUT | `/api/settings/payment` | PayPal/Venmo payment links |
| GET/POST/PUT/DELETE | `/api/rate-templates` | Rate template CRUD |
| GET | `/api/llm/status` | Ollama/LLM connection status |
| GET | `/api/health` | Health check |

---

## Roadmap

- **React Native Android App** — Mission creation, photo capture on-site, report review, and customer lookup from the field. Communicates with the stack via JWT-authenticated HTTPS API.
- **Voice-to-Text** — On-device speech recognition in the Android app for dictating operator field notes hands-free during or after missions.
- **Report Templates** — Multiple PDF templates for different mission types
- **Analytics** — Revenue tracking and mission type breakdown
- **Live Flight Tracking** — WebSocket integration for real-time drone position

---

## Development

```bash
# Backend
cd backend && pip install -r requirements.txt
uvicorn app.main:app --reload

# Frontend
cd frontend && npm install && npm run dev
```

### Docker Volumes

| Volume | Purpose |
|--------|---------|
| `postgres_data` | PostgreSQL database files |
| `ollama_data` | Downloaded LLM model weights |
| `app_data` | Uploaded images and generated PDFs/maps |

To fully reset the database (destroys all data):
```bash
docker compose down -v
docker compose up -d
```

---

## Contributing

Contributions are welcome! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

---

## License

MIT License — see [LICENSE](LICENSE) for details.
