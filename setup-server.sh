#!/bin/bash
# ── DroneOpsCommand Server Setup ─────────────────────────────────
#
# One-command installer for systemd services:
#   1. droneops.service        — auto-start Docker stack on boot
#   2. droneops-autopull.timer — auto-deploy new git commits (every 60s)
#
# Usage:
#   sudo ./setup-server.sh                  # defaults: main branch
#   sudo ./setup-server.sh --branch <name>  # track a different branch
#   sudo ./setup-server.sh --uninstall      # remove all systemd units
#
# ──────────────────────────────────────────────────────────────────

set -euo pipefail

# ── Colors ───────────────────────────────────────────────────────
CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BOLD='\033[1m'
NC='\033[0m'

# ── Parse args ───────────────────────────────────────────────────
BRANCH="main"
UNINSTALL=false

# ── Minimum requirements (warn-only, never blocks install) ───────
MIN_RAM_GB=8
REC_RAM_GB=16
MIN_CPU=4
REC_CPU=6
MIN_DISK_GB=30

while [[ $# -gt 0 ]]; do
  case "$1" in
    --branch)   BRANCH="$2"; shift 2 ;;
    --uninstall) UNINSTALL=true; shift ;;
    -h|--help)
      echo "Usage: sudo $0 [--branch <branch>] [--uninstall]"
      echo "  --branch <branch>  Git branch to auto-deploy (default: main)"
      echo "  --uninstall        Remove all DroneOpsCommand systemd units"
      exit 0
      ;;
    *) echo -e "${RED}Unknown option: $1${NC}"; exit 1 ;;
  esac
done

# ── Must run as root ─────────────────────────────────────────────
if [ "$(id -u)" -ne 0 ]; then
  echo -e "${RED}ERROR: This script must be run as root (sudo ./setup-server.sh)${NC}"
  exit 1
fi

# ── Uninstall ────────────────────────────────────────────────────
if [ "$UNINSTALL" = true ]; then
  echo -e "${CYAN}Removing DroneOpsCommand systemd units...${NC}"
  systemctl stop droneops-autopull.timer 2>/dev/null || true
  systemctl disable droneops-autopull.timer 2>/dev/null || true
  systemctl stop droneops 2>/dev/null || true
  systemctl disable droneops 2>/dev/null || true
  rm -f /etc/systemd/system/droneops.service
  rm -f /etc/systemd/system/droneops-autopull.service
  rm -f /etc/systemd/system/droneops-autopull.timer
  systemctl daemon-reload
  echo -e "${GREEN}All DroneOpsCommand systemd units removed.${NC}"
  exit 0
fi

# ── Detect install directory and user ────────────────────────────
INSTALL_DIR="$(cd "$(dirname "$0")" && pwd)"
# Find the user who owns the repo (not root)
REPO_OWNER=$(stat -c '%U' "$INSTALL_DIR/.git" 2>/dev/null || stat -f '%Su' "$INSTALL_DIR/.git" 2>/dev/null)

if [ -z "$REPO_OWNER" ] || [ "$REPO_OWNER" = "root" ]; then
  # Fallback: check SUDO_USER
  REPO_OWNER="${SUDO_USER:-}"
fi

if [ -z "$REPO_OWNER" ]; then
  echo -e "${RED}ERROR: Could not determine repo owner. Run with sudo from the repo directory.${NC}"
  exit 1
fi

echo ""
echo -e "${CYAN}╔══════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║${NC}  ${BOLD}DroneOpsCommand${NC} — Server Setup                  ${CYAN}║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  Install dir:  ${BOLD}$INSTALL_DIR${NC}"
echo -e "  Repo owner:   ${BOLD}$REPO_OWNER${NC}"
echo -e "  Branch:       ${BOLD}$BRANCH${NC}"
echo ""

# ── Validate ─────────────────────────────────────────────────────
if [ ! -f "$INSTALL_DIR/docker-compose.yml" ]; then
  echo -e "${RED}ERROR: docker-compose.yml not found in $INSTALL_DIR${NC}"
  exit 1
fi

if [ ! -f "$INSTALL_DIR/autopull.sh" ]; then
  echo -e "${RED}ERROR: autopull.sh not found in $INSTALL_DIR${NC}"
  exit 1
fi

if ! command -v docker &>/dev/null; then
  echo -e "${RED}ERROR: Docker is not installed${NC}"
  exit 1
fi

# ── Preflight: host resource check (warn-only) ───────────────────
echo -e "${CYAN}Checking host resources...${NC}"
HOST_RAM_GB=$(awk '/MemTotal/ {printf "%.0f", $2/1024/1024}' /proc/meminfo 2>/dev/null || echo 0)
HOST_CPU=$(nproc 2>/dev/null || echo 0)
HOST_DISK_GB=$(df -BG "$INSTALL_DIR" 2>/dev/null | awk 'NR==2 {gsub("G","",$4); print $4}' || echo 0)

PREFLIGHT_WARN=0
if [ "$HOST_RAM_GB" -lt "$MIN_RAM_GB" ]; then
  echo -e "${YELLOW}  ⚠  RAM: ${HOST_RAM_GB}GB detected — below minimum ${MIN_RAM_GB}GB (recommended ${REC_RAM_GB}GB)${NC}"
  echo -e "${YELLOW}     Ollama alone needs ~4GB for the quantized model. You may hit OOM crashes under load.${NC}"
  PREFLIGHT_WARN=1
fi
if [ "$HOST_CPU" -lt "$MIN_CPU" ]; then
  echo -e "${YELLOW}  ⚠  CPU: ${HOST_CPU} cores — below minimum ${MIN_CPU} (recommended ${REC_CPU})${NC}"
  echo -e "${YELLOW}     docker-compose.yml pins Ollama to 6 cores; on fewer cores, AI reports will be slow.${NC}"
  PREFLIGHT_WARN=1
fi
if [ -n "$HOST_DISK_GB" ] && [ "$HOST_DISK_GB" -lt "$MIN_DISK_GB" ] 2>/dev/null; then
  echo -e "${YELLOW}  ⚠  Disk: ${HOST_DISK_GB}GB free at $INSTALL_DIR — below minimum ${MIN_DISK_GB}GB${NC}"
  echo -e "${YELLOW}     Flight logs, Postgres, and the Ollama model can grow quickly.${NC}"
  PREFLIGHT_WARN=1
fi

if [ "$PREFLIGHT_WARN" -eq 1 ]; then
  echo -e "${YELLOW}  Install will continue, but expect crashes or slowness. Docker Desktop users: raise the VM resource allocation in Settings → Resources.${NC}"
  sleep 2
else
  echo -e "${GREEN}  ✓ RAM: ${HOST_RAM_GB}GB, CPU: ${HOST_CPU} cores, Disk: ${HOST_DISK_GB}GB free${NC}"
fi

# ── Install: droneops.service ────────────────────────────────────
echo -e "${CYAN}Installing droneops.service...${NC}"
sed \
  -e "s|__INSTALL_DIR__|$INSTALL_DIR|g" \
  "$INSTALL_DIR/droneops.service" > /etc/systemd/system/droneops.service

# ── Install: droneops-autopull.service + timer ───────────────────
echo -e "${CYAN}Installing droneops-autopull.service + timer...${NC}"
sed \
  -e "s|__INSTALL_DIR__|$INSTALL_DIR|g" \
  -e "s|__BRANCH__|$BRANCH|g" \
  -e "s|__USER__|$REPO_OWNER|g" \
  "$INSTALL_DIR/droneops-autopull.service" > /etc/systemd/system/droneops-autopull.service

cp "$INSTALL_DIR/droneops-autopull.timer" /etc/systemd/system/droneops-autopull.timer

# ── Make sure autopull.sh is executable ──────────────────────────
chmod +x "$INSTALL_DIR/autopull.sh"
chmod +x "$INSTALL_DIR/update.sh"

# ── Ensure the repo owner can run docker ─────────────────────────
if ! id -nG "$REPO_OWNER" | grep -qw docker; then
  echo -e "${YELLOW}Adding $REPO_OWNER to the docker group...${NC}"
  usermod -aG docker "$REPO_OWNER"
  echo -e "${YELLOW}NOTE: User may need to log out/in for docker group to take effect.${NC}"
fi

# ── Enable and start ─────────────────────────────────────────────
systemctl daemon-reload

echo -e "${CYAN}Enabling droneops.service (auto-start on boot)...${NC}"
systemctl enable droneops.service

echo -e "${CYAN}Enabling droneops-autopull.timer (auto-deploy every 60s)...${NC}"
systemctl enable droneops-autopull.timer
systemctl start droneops-autopull.timer

# ── Remove any old cron entries for autopull ─────────────────────
if crontab -u "$REPO_OWNER" -l 2>/dev/null | grep -q "autopull"; then
  echo -e "${YELLOW}Removing old autopull cron entry...${NC}"
  crontab -u "$REPO_OWNER" -l 2>/dev/null | grep -v "autopull" | crontab -u "$REPO_OWNER" - 2>/dev/null || true
fi

# ── Verify ───────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║${NC}  ${BOLD}Setup complete!${NC}                                  ${GREEN}║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  ${BOLD}Boot auto-start:${NC}"
echo -e "    systemctl status droneops"
echo ""
echo -e "  ${BOLD}Auto-deploy (polls ${BRANCH} every 60s):${NC}"
echo -e "    systemctl status droneops-autopull.timer"
echo -e "    systemctl list-timers droneops-autopull*"
echo -e "    journalctl -u droneops-autopull -f"
echo -e "    tail -f $INSTALL_DIR/autopull.log"
echo ""
echo -e "  ${BOLD}Manual controls:${NC}"
echo -e "    sudo systemctl start droneops        # start stack"
echo -e "    sudo systemctl stop droneops          # stop stack"
echo -e "    sudo systemctl restart droneops       # restart stack"
echo -e "    sudo ./update.sh                      # interactive deploy menu"
echo ""
echo -e "  ${BOLD}Uninstall:${NC}"
echo -e "    sudo ./setup-server.sh --uninstall"
echo ""
