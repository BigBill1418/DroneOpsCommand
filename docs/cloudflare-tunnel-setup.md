# Cloudflare Tunnel Setup for DroneOpsCommand

Cloudflare Tunnel lets customers reach intake forms at `https://droneops.example.com` without opening any ports on your router or exposing your server IP.

## Prerequisites

- A Cloudflare account (free tier works)
- Your domain (e.g. `example.com`) added to Cloudflare (DNS managed by Cloudflare)
- Docker Compose already running on your server

---

## Step 1: Create the Tunnel in Cloudflare Dashboard

You can use either dashboard — both work:

| Dashboard | Navigation |
|-----------|------------|
| **Main Cloudflare** ([dash.cloudflare.com](https://dash.cloudflare.com)) | **Networking → Tunnels** |
| **Zero Trust** ([one.dash.cloudflare.com](https://one.dash.cloudflare.com)) | **Networks → Connectors → Cloudflare Tunnels** |

1. Click **Create a tunnel**
2. Choose **Cloudflared** as the connector type
3. Name it something like `doc-server`
4. **Copy the tunnel token** — it looks like `eyJhIjoiN2...` (a long base64 string)

## Step 2: Add a Public Hostname Route

When adding a route you'll see two options — **Public Hostname** and **Private Network (CIDR)**. Choose **Public Hostname** (CIDR is for routing entire IP subnets through WARP clients, which is not what we need).

1. Click **Add a public hostname**
2. Set:
   - **Subdomain**: `doc`
   - **Domain**: `example.com`
   - **Service type**: `HTTP`
   - **URL**: `frontend:80`

   > This tells Cloudflare to route `https://droneops.example.com` → your frontend nginx container, which already proxies `/api/*` to the backend. Cloudflare automatically creates the DNS CNAME record if it manages your domain's nameservers.

3. Click **Save**

## Step 3: Add the Token to Your .env

On your server, edit your `.env` file:

```bash
# Cloudflare Tunnel
CLOUDFLARE_TUNNEL_TOKEN=eyJhIjoiN2...your_token_here
```

Also make sure `FRONTEND_URL` matches:

```bash
FRONTEND_URL=https://droneops.example.com  # Replace with your actual domain
```

## Step 4: Start the Tunnel

The `cloudflared` service is included in `docker-compose.yml` and starts automatically with `docker compose up -d`. It requires the `CLOUDFLARE_TUNNEL_TOKEN` environment variable — without it, the container exits immediately (no-op).

```bash
# If your stack is already running, restart to pick up the new token
docker compose up -d

# Or restart just the tunnel service
docker compose restart cloudflared
```

To stop the tunnel without affecting other services:
```bash
docker compose stop cloudflared
```

## Step 5: Verify

1. Check the tunnel is connected:
   ```bash
   docker compose logs cloudflared
   ```
   You should see: `Connection ... registered`

2. In the Cloudflare dashboard, the tunnel status should show **Healthy**

3. Open `https://droneops.example.com` in your browser — you should see the DroneOpsCommand login page

4. Test an intake link: `https://droneops.example.com/intake/<token>`

---

## Optional: Lock Down Admin with Cloudflare Access

This is the recommended security step — it makes intake forms public but locks the admin dashboard behind authentication.

### Create an Access Application

1. In Cloudflare Zero Trust, go to **Access → Applications**
2. Click **Add an application → Self-hosted**
3. Configure:
   - **Application name**: `DroneOps Admin`
   - **Session duration**: 24 hours
   - **Subdomain**: `doc` / **Domain**: `example.com`
   - **Path**: leave empty (protects the whole site)
4. Under **Policies**, create an "Allow" policy:
   - **Policy name**: `Admin Only`
   - **Selector**: `Emails` → enter your email address(es)
   - **Authentication method**: One-time PIN (sent to your email) or Google/GitHub SSO

### Bypass Public Routes

Cloudflare Access limits the number of hostnames/policies per application,
so use wildcard paths to consolidate rules. Still in the same application:

1. Add a second policy **above** the Allow policy:
   - **Policy name**: `Public Intake`
   - **Action**: **Bypass**
   - **Selector**: `Everyone`
2. Under **Additional settings** for this bypass policy:
   - **Path**: `/intake/*`

3. Add another Bypass policy for the intake API (covers form submission + TOS PDF):
   - **Path**: `/api/intake/*`

4. Add another Bypass policy for the companion app (DroneOpsSync device upload/health):
   - **Policy name**: `Device Sync API`
   - **Action**: **Bypass**
   - **Selector**: `Everyone`
   - **Path**: `/api/flight-library/device-*`

   > These endpoints are already secured by `X-Device-Api-Key` header auth — unauthorized requests get a 401. Cloudflare Access bypass is safe here.

**3 bypass rules** needed:

| Path | What it covers |
|------|---------------|
| `/intake/*` | Customer intake frontend SPA |
| `/api/intake/*` | Intake form GET/POST + TOS PDF download |
| `/api/flight-library/device-*` | DroneOpsSync companion app (health check + upload) |

Everything else requires Cloudflare Access login.

> **Note:** The PDF.js worker used for inline TOS viewing is loaded from
> `cdnjs.cloudflare.com`, so it does not require a bypass rule.

---

## Optional: Restrict to Localhost When Tunnel Is Active

If you only want to access the app through the tunnel (not via LAN), change the frontend port binding in `.env`:

```bash
# Only accessible from the server itself (and via tunnel)
FRONTEND_PORT=127.0.0.1:3080
```

To keep LAN access as well (for admin use on your local network):
```bash
# Accessible from any device on your LAN + via tunnel
FRONTEND_PORT=3080
```

---

## Architecture Overview

```
Customer's Browser
        │
        ▼
 Cloudflare Edge (TLS, DDoS, WAF)
        │
        ▼ (encrypted tunnel, outbound-only from server)
   cloudflared container
        │
        ▼
   frontend (nginx:80)
     ├── /intake/*         → React SPA
     ├── /api/intake/*     → proxy to backend:8000
     └── /api/*            → proxy to backend:8000
        │
        ▼
   backend (FastAPI:8000)
        │
        ├── PostgreSQL
        ├── Redis
        └── Ollama
```

**No inbound ports are opened on your router. The tunnel connection is outbound-only from your server to Cloudflare.**

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `cloudflared` exits immediately | Check `CLOUDFLARE_TUNNEL_TOKEN` is set in `.env` |
| Tunnel shows "Inactive" in dashboard | Run `docker compose logs cloudflared` to check errors |
| Intake form loads but API calls fail | Make sure the tunnel hostname points to `frontend:80`, not `backend:8000` |
| "Access Denied" on intake form | Check your Cloudflare Access bypass policies include `/intake/*` and `/api/intake/*` |
| TOS PDF won't load | Add bypass for `/api/intake/*` in Access policies |
| DroneOpsSync companion app won't connect via cloud | Add bypass for `/api/flight-library/device-*` in Access policies |
