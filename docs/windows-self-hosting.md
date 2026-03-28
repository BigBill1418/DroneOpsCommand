# Self-Hosting DroneOpsCommand on Windows

Complete guide for running DroneOpsCommand on a Windows 10/11 machine using Docker Desktop.

---

## System Requirements

| Requirement | Minimum | Recommended |
|---|---|---|
| OS | Windows 10 (version 2004+) or Windows 11 | Windows 11 |
| RAM | 8 GB | 16 GB |
| Disk | 25 GB free | 50 GB free |
| CPU | 4 cores | 8 cores |
| Virtualization | Enabled in BIOS | — |

> The AI report engine (Ollama/Llama 3.1 8B) reserves ~8 GB of RAM by itself. With 8 GB total you will be tight — 16 GB is strongly recommended.

---

## Step 1: Enable WSL 2

Docker Desktop on Windows requires WSL 2 (Windows Subsystem for Linux). Open **PowerShell as Administrator** and run:

```powershell
wsl --install
```

This enables WSL 2 and installs Ubuntu. **Restart your computer** when prompted.

After reboot, verify:

```powershell
wsl --version
```

You should see WSL version 2.x. If you see version 1, upgrade:

```powershell
wsl --set-default-version 2
```

### Troubleshooting WSL 2

- **"Virtualization not enabled"** — Enter your BIOS/UEFI settings and enable Intel VT-x or AMD-V. The setting is usually under CPU Configuration or Security.
- **Windows Home edition** — WSL 2 works on Windows Home. No need for Pro/Enterprise.
- **Corporate machines with Group Policy restrictions** — Contact your IT department to enable Hyper-V and WSL 2.

---

## Step 2: Install Docker Desktop

1. Download Docker Desktop from [docker.com/products/docker-desktop](https://www.docker.com/products/docker-desktop/)
2. Run the installer — accept defaults
3. When prompted, choose **"Use WSL 2 instead of Hyper-V"**
4. Restart if required

After installation, open Docker Desktop and verify it's running (whale icon in the system tray).

### Configure Docker Resources

Open Docker Desktop → Settings → Resources → WSL Integration:

- Ensure your WSL 2 distro (Ubuntu) is enabled
- Under **Resources → Advanced**, allocate at least:
  - **CPUs:** 6 (if you have 8 total)
  - **Memory:** 10 GB (if you have 16 GB total)
  - **Disk:** 40 GB

Click **Apply & Restart**.

---

## Step 3: Install Git for Windows

If you don't already have Git:

1. Download from [git-scm.com/download/win](https://git-scm.com/download/win)
2. Run the installer — accept defaults
3. **Important:** When asked about line endings, choose **"Checkout as-is, commit as-is"** to avoid CRLF issues

Verify in PowerShell:

```powershell
git --version
```

---

## Step 4: Clone and Configure

Open **PowerShell** (regular, not Admin) and run:

```powershell
# Clone the repo
git clone https://github.com/BigBill1418/DroneOpsCommand.git
cd DroneOpsCommand

# Create your config file
copy .env.example .env
```

Now edit `.env` with Notepad (or any text editor):

```powershell
notepad .env
```

**Change these three values at minimum** (do not use the defaults in production):

```
POSTGRES_PASSWORD=your_secure_database_password
JWT_SECRET_KEY=your_random_secret_string_here
ADMIN_PASSWORD=your_admin_login_password
```

> **Tip:** To generate a random secret, run this in PowerShell:
> ```powershell
> [Convert]::ToBase64String((1..32 | ForEach-Object { Get-Random -Maximum 256 }) -as [byte[]])
> ```

Also update the `DATABASE_URL` to match your new password:

```
DATABASE_URL=postgresql+asyncpg://doc:your_secure_database_password@db:5432/doc
```

Save and close the file.

---

## Step 5: Launch

In PowerShell, from the `DroneOpsCommand` directory:

```powershell
docker compose up -d
```

The first run will:
1. **Build** the backend, frontend, and flight-parser images (5-10 minutes)
2. **Download** PostgreSQL, Redis, and Ollama base images (~2 GB)
3. **Pull the AI model** — Llama 3.1 8B is ~4 GB

Watch the model download progress:

```powershell
docker compose logs -f ollama-setup
```

Press `Ctrl+C` when you see "Model pulled." — the app is ready.

---

## Step 6: Open the App

Open your browser and go to:

- **Web UI:** [http://localhost:3080](http://localhost:3080)
- **API docs:** [http://localhost:3080/docs](http://localhost:3080/docs)

Log in with:
- **Username:** `admin` (or whatever you set for `ADMIN_USERNAME`)
- **Password:** the `ADMIN_PASSWORD` you set in `.env`

---

## Common Windows Issues

### Port 3080 is already in use

Another application is using port 3080. Edit `.env` and change:

```
FRONTEND_PORT=3090
```

Then restart:

```powershell
docker compose down
docker compose up -d
```

Access the app at `http://localhost:3090` instead.

### Docker Desktop says "Docker Engine stopped"

1. Open Docker Desktop
2. Click the restart button, or quit and reopen
3. If it persists, run in PowerShell as Admin:
   ```powershell
   wsl --shutdown
   ```
   Then reopen Docker Desktop.

### Build fails with "no space left on device"

Docker's virtual disk is full. Open Docker Desktop → Settings → Resources → Disk image size → increase it. Or clean up unused images:

```powershell
docker system prune -a
```

> **Warning:** This removes all unused images. You will need to rebuild on next `docker compose up -d`.

### WSL 2 is using too much memory

Create or edit the file `C:\Users\<YourUsername>\.wslconfig`:

```ini
[wsl2]
memory=10GB
processors=6
swap=4GB
```

Then restart WSL:

```powershell
wsl --shutdown
```

### Windows Defender / Antivirus blocking Docker

Add these exclusions to your antivirus:
- `C:\Program Files\Docker\`
- `C:\Users\<YourUsername>\AppData\Local\Docker\`
- The `DroneOpsCommand` folder

### Cannot access from other devices on the network

By default the app listens on all interfaces. In `.env`, make sure `FRONTEND_PORT` is set to just the port number (not bound to localhost):

```
FRONTEND_PORT=3080
```

Then access from other devices using your Windows machine's IP address (find it with `ipconfig`):

```
http://192.168.1.xxx:3080
```

You may need to allow port 3080 through Windows Firewall:

```powershell
# Run as Administrator
netsh advfirewall firewall add rule name="DroneOpsCommand" dir=in action=allow protocol=TCP localport=3080
```

---

## Managing the App

### Start / Stop / Restart

```powershell
# Start (runs in background)
docker compose up -d

# Stop
docker compose down

# Restart
docker compose restart

# View logs
docker compose logs -f

# View logs for a specific service
docker compose logs -f backend
```

### Updating

```powershell
# Pull latest code
git pull origin main

# Rebuild and restart
docker compose up -d --build
```

### Backup Your Data

The app stores data in Docker volumes. To back up:

```powershell
# Database backup
docker compose exec db pg_dump -U doc doc > backup.sql

# Find where uploads/reports are stored
docker volume inspect droneopscommand_app_data
```

Or use the built-in backup feature in **Settings → Account → Backup & Restore** in the web UI.

### Reset Everything

If you need a clean start:

```powershell
docker compose down -v
docker compose up -d
```

> **Warning:** `docker compose down -v` deletes all data (database, uploads, reports). Back up first.

---

## Optional: DJI API Key

If you fly newer DJI drones with encrypted flight logs, you need a DJI API key for the flight parser:

1. Register at [developer.dji.com](https://developer.dji.com)
2. Create an application and obtain an API key
3. Add it to `.env`:
   ```
   DJI_API_KEY=your_key_here
   ```
4. Restart: `docker compose restart flight-parser`

---

## Optional: Cloudflare Tunnel (Remote Access)

To access your DroneOpsCommand instance from anywhere without opening router ports, set `CLOUDFLARE_TUNNEL_TOKEN` in your `.env` file. Get a token from the Cloudflare Zero Trust dashboard under Networks → Tunnels.

---

## Optional: Email (SMTP) Setup

Configure email delivery for sending reports to customers. Either:

- Edit `.env` with your SMTP settings, **or**
- Configure in the web UI: **Settings → Email & Billing**

After configuring, use the **Test Email** button in Settings to verify.
