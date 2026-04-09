# Migration Guide: Synology Container Manager → Ubuntu Server VM

**Zero data loss. Every step has a verification gate — do not proceed until the current step passes.**

---

## PHASE 1: INVENTORY & BACKUP (on Synology — change nothing)

### Step 1.1 — Document the current state

SSH into the Synology and record everything:

```bash
cd /path/to/DroneOpsCommand

# Record running containers and their status
sudo docker compose ps > /tmp/doc_service_snapshot.txt

# Record volume names and mount points
sudo docker volume ls | grep -i drone > /tmp/doc_volumes.txt
sudo docker volume inspect $(sudo docker volume ls -q | grep -i drone) > /tmp/doc_volume_details.json

# Record the current .env (contains all secrets/config)
cp .env /tmp/doc_env_backup

# Record database table row counts for verification later
sudo docker compose exec db psql -U doc -d doc -c "
  SELECT schemaname, relname, n_live_tup
  FROM pg_stat_user_tables
  ORDER BY relname;
" > /tmp/doc_row_counts.txt

# Record total flight count, mission count, customer count
sudo docker compose exec db psql -U doc -d doc -c "
  SELECT 'flights' as entity, count(*) FROM flights
  UNION ALL SELECT 'missions', count(*) FROM missions
  UNION ALL SELECT 'customers', count(*) FROM customers
  UNION ALL SELECT 'aircraft', count(*) FROM aircraft
  UNION ALL SELECT 'batteries', count(*) FROM batteries
  UNION ALL SELECT 'invoices', count(*) FROM invoices
  UNION ALL SELECT 'users', count(*) FROM users;
" > /tmp/doc_entity_counts.txt

cat /tmp/doc_entity_counts.txt
```

**GATE:** Write down these counts. You will verify them on the Ubuntu VM later.

### Step 1.2 — Full database backup

```bash
# Create backup using PostgreSQL custom format (supports selective restore)
sudo docker compose exec db pg_dump \
  -U doc -d doc \
  -Fc --verbose \
  --file=/tmp/doc_backup.dump

# Copy it out of the container
sudo docker compose cp db:/tmp/doc_backup.dump /tmp/doc_backup.dump

# Verify the backup is valid (pg_restore --list reads the TOC without restoring)
sudo docker compose exec db pg_restore --list /tmp/doc_backup.dump > /tmp/doc_backup_toc.txt

# Check file size — should be at least a few MB if you have real data
ls -lh /tmp/doc_backup.dump
```

**GATE:** Confirm `pg_restore --list` outputs table names (flights, missions, customers, etc.) with no errors. If the dump is tiny (< 100KB) something is wrong — investigate before proceeding.

### Step 1.3 — Full app_data volume backup (uploads, reports, flight logs)

```bash
# Find the volume mount path
APPDATA_PATH=$(sudo docker volume inspect --format '{{ .Mountpoint }}' $(sudo docker volume ls -q | grep app_data))
echo "app_data path: $APPDATA_PATH"

# Count files inside for verification
sudo find "$APPDATA_PATH" -type f | wc -l > /tmp/doc_file_count.txt
echo "Total files in app_data: $(cat /tmp/doc_file_count.txt)"

# List subdirectories and their sizes
sudo du -sh "$APPDATA_PATH"/* > /tmp/doc_dir_sizes.txt
cat /tmp/doc_dir_sizes.txt

# Create tarball preserving permissions and ownership
sudo tar czf /tmp/doc_appdata.tar.gz -C "$APPDATA_PATH" .

# Verify tarball integrity
tar tzf /tmp/doc_appdata.tar.gz | wc -l
ls -lh /tmp/doc_appdata.tar.gz
```

**GATE:** File count in tarball listing must match the file count from `find`. Tarball size must be reasonable (compare to `du -sh` output).

### Step 1.4 — Transfer everything to the Ubuntu VM

```bash
# From the Ubuntu VM (pull files from Synology):
mkdir -p ~/migration
scp synology-ip:/tmp/doc_backup.dump ~/migration/doc_backup.dump
scp synology-ip:/tmp/doc_appdata.tar.gz ~/migration/doc_appdata.tar.gz
scp synology-ip:/tmp/doc_env_backup ~/migration/doc_env_backup
scp synology-ip:/tmp/doc_entity_counts.txt ~/migration/doc_entity_counts.txt
scp synology-ip:/tmp/doc_file_count.txt ~/migration/doc_file_count.txt

# Verify file sizes match (check both ends)
ls -lh ~/migration/
```

**GATE:** All 5 files transferred. Sizes match the originals on Synology (compare `ls -lh`). If any file is truncated, re-transfer.

---

## PHASE 2: PREPARE UBUNTU VM

### Step 2.1 — Verify system requirements

```bash
# Docker version (need 20.10+, ideally 24+)
docker --version

# Docker Compose v2 plugin
docker compose version

# Disk space (need at least 15GB free — images + data + Ollama model)
df -h /

# RAM (8GB minimum — Ollama loads the AI model into RAM)
free -h

# Git
git --version

# Your user is in docker group (no sudo needed)
docker ps
# If permission denied:
sudo usermod -aG docker $USER
# Then log out and back in, or:
newgrp docker
```

**GATE:** All commands succeed without errors. At least 15GB free disk, 8GB+ RAM.

### Step 2.2 — Clone the repository

```bash
sudo mkdir -p /opt/droneops
sudo chown $USER:$USER /opt/droneops
git clone https://github.com/BigBill1418/DroneOpsCommand.git /opt/droneops
cd /opt/droneops

# Checkout the branch your Synology was running
# (check your Synology: git branch --show-current)
git checkout main   # or claude/dev — match what was deployed
```

### Step 2.3 — Configure environment

```bash
# Start from the backup of your real .env
cp ~/migration/doc_env_backup /opt/droneops/.env

# Edit to verify/update these values:
nano /opt/droneops/.env
```

**Values that MUST stay the same** (or you lose data / break auth):

| Variable | Why |
|----------|-----|
| `POSTGRES_USER` | Must match backup |
| `POSTGRES_PASSWORD` | Must match backup |
| `POSTGRES_DB` | Must match backup |
| `DATABASE_URL` | Same password in connection string |
| `JWT_SECRET_KEY` | Or all sessions invalidate |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | Same as Synology |
| `DJI_API_KEY` | Same |
| `SMTP_*` | All SMTP settings same |
| `CLOUDFLARE_TUNNEL_TOKEN` | Same (if using tunnel) |

**Values that MAY need to change:**

| Variable | Action |
|----------|--------|
| `FRONTEND_URL` | Update to `http://<ubuntu-vm-ip>:3080` |
| `FRONTEND_PORT` | `3080` unless you want a different port |
| `LLM_PROVIDER` | Set to `ollama` or `claude` depending on your preference |

**Values that stay as-is (internal Docker networking):**
- `DATABASE_URL` host stays `db` (Docker service name)
- `REDIS_URL` stays `redis://redis:6379/0`
- `OLLAMA_BASE_URL` stays `http://ollama:11434`

**GATE:** Diff your .env against .env.example:
```bash
diff <(grep -v '^#' .env.example | grep -v '^$' | sort) <(grep -v '^#' .env | grep -v '^$' | sort)
```

---

## PHASE 3: BUILD & RESTORE

### Step 3.1 — Build all Docker images

```bash
cd /opt/droneops

# Build all images (flight-parser may be slow on first build)
docker compose build

# Verify all images built
docker images | grep -E "droneops|doc"
```

**GATE:** Build completes with no errors. Images exist for backend, frontend, flight-parser.

### Step 3.2 — Start ONLY the database

```bash
# Start just PostgreSQL — restore data before the backend touches it
docker compose up -d db

# Wait for healthy
docker compose exec db pg_isready -U doc
# Should output: "accepting connections"
```

### Step 3.3 — Restore the database backup

```bash
# Drop auto-created empty tables and restore from backup
docker compose exec -T db pg_restore \
  -U doc -d doc \
  --clean --if-exists \
  --no-owner --no-privileges \
  --verbose \
  < ~/migration/doc_backup.dump 2>&1 | tee ~/migration/restore_log.txt

# Check for real errors (some "does not exist" warnings during --clean are normal)
grep -i "error" ~/migration/restore_log.txt
```

**GATE:** Verify entity counts match exactly:
```bash
docker compose exec db psql -U doc -d doc -c "
  SELECT 'flights' as entity, count(*) FROM flights
  UNION ALL SELECT 'missions', count(*) FROM missions
  UNION ALL SELECT 'customers', count(*) FROM customers
  UNION ALL SELECT 'aircraft', count(*) FROM aircraft
  UNION ALL SELECT 'batteries', count(*) FROM batteries
  UNION ALL SELECT 'invoices', count(*) FROM invoices
  UNION ALL SELECT 'users', count(*) FROM users;
"
```
Compare to `~/migration/doc_entity_counts.txt`. **Every count must match exactly.** If any count is 0 when it shouldn't be — STOP.

### Step 3.4 — Restore the app_data volume

```bash
# Find the Docker volume path
APPDATA_VOL=$(docker volume inspect --format '{{ .Mountpoint }}' droneops_app_data 2>/dev/null || docker volume inspect --format '{{ .Mountpoint }}' $(docker volume ls -q | grep app_data))
echo "Volume path: $APPDATA_VOL"

# Extract the tarball
sudo tar xzf ~/migration/doc_appdata.tar.gz -C "$APPDATA_VOL"

# Fix ownership — backend runs as uid 1000 (user 'doc' inside container)
sudo chown -R 1000:1000 "$APPDATA_VOL"

# Verify subdirectories exist
sudo ls -la "$APPDATA_VOL"/
# Should show: uploads/  reports/  backups/ (and possibly flight_logs/)
```

**GATE:** Verify file count matches:
```bash
sudo find "$APPDATA_VOL" -type f | wc -l
cat ~/migration/doc_file_count.txt
# These two numbers must match
```

### Step 3.5 — Start all remaining services

```bash
docker compose up -d

# Watch logs for startup
docker compose logs -f --tail=50
# Wait until you see:
#   backend:  "Application startup complete"
#   worker:   "celery@... ready"
#   ollama-setup: "Model pulled" (first time — downloads ~1.5GB, be patient)
```

**GATE:** All services running:
```bash
docker compose ps
```
Every service `Up` except `ollama-setup` (`Exited (0)` is normal — one-shot job).

---

## PHASE 4: FULL VERIFICATION (do NOT skip any)

| # | Check | What it proves |
|---|-------|----------------|
| 4.1 | Web UI loads at `http://<vm-ip>:3080` | Frontend/nginx working |
| 4.2 | Log in with existing credentials | Database auth + JWT intact |
| 4.3 | Dashboard stats match Synology | Data integrity |
| 4.4 | Flights page — all flights, click one — map + telemetry load | Flight data + GPS tracks intact |
| 4.5 | Open a mission — images load, flight map renders | app_data volume intact |
| 4.6 | Generate a PDF from a completed mission | WeasyPrint + templates + images |
| 4.7 | Upload a test flight log | nginx + backend + flight-parser + DB + file storage |
| 4.8 | Settings page — branding, SMTP, OpenSky, rate templates | System settings intact |
| 4.9 | Airspace page — map loads, aircraft appear | OpenSky proxy working |
| 4.10 | Settings > LLM Status shows "Online" | Ollama loaded and responding |

**GATE:** All 10 pass. If ANY fail — troubleshoot. Do NOT shut down Synology.

---

## PHASE 5: CUTOVER

### Step 5.1 — Update DNS / Tunnel / Bookmarks

- **Cloudflare Tunnel:** Shut down Synology first (tunnel token conflict), then the Ubuntu VM takes over automatically
- **LAN bookmarks:** Update from `synology-ip:3080` → `ubuntu-vm-ip:3080`
- **DroneOpsSync app:** Update server URL in app settings

### Step 5.2 — Shut down the Synology instance

```bash
# On Synology:
cd /path/to/DroneOpsCommand
sudo docker compose down
```

Volumes are preserved. If anything goes wrong: `docker compose up -d` on Synology.

### Step 5.3 — (Optional) Clean up Synology volumes

**Only after running on Ubuntu for at least one week:**

```bash
# On Synology — permanently delete old data
sudo docker compose down -v
```

---

## PHASE 6: FIREWALL (Ubuntu VM)

```bash
sudo ufw allow 3080/tcp comment "DroneOpsCommand UI"
sudo ufw allow 22/tcp comment "SSH"

# Do NOT expose: 8000, 5432, 6379, 8100, 11434 (internal Docker services)

sudo ufw enable
sudo ufw status
```

---

## ONGOING MANAGEMENT

```bash
cd /opt/droneops
./update.sh           # Interactive menu
./update.sh dev       # Pull + deploy dev branch
./update.sh prod      # Pull + deploy production
./update.sh status    # Show running services
./update.sh dev --clean   # Full rebuild, no Docker cache
```

---

## ROLLBACK

At any phase, the Synology is still intact:

1. Ubuntu: `docker compose down`
2. Synology: `docker compose up -d`
3. Zero data loss — you're back where you started
