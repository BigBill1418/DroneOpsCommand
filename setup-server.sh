#!/bin/bash
# ── DroneOpsCommand Server Setup ─────────────────────────────────
#
# Installs the boot-time stack-start systemd unit:
#   1. droneops.service  — auto-start the Docker Compose stack on boot
#
# DEPLOYS ARE NOT HANDLED HERE. Continuous deployment of this repo is
# the responsibility of the **NOC Master Control fleet deployer**
# (`swarmpilot_deployer` on HSH-HQ), which polls origin/main and
# rebuilds + recreates the containers on the BOS-HQ production host.
# The former per-repo autopull (`autopull.sh` + `droneops-autopull.timer`
# + the long-deleted `update.sh`) was RETIRED — see ADR-0018 and the
# original removal in commit e4610b5 ("Remove per-repo deployer — now
# managed by NOC Master Control auto-deployer"). Do not reintroduce a
# second deploy path; the fleet deployer is the single authority.
#
# Usage:
#   sudo ./setup-server.sh             # install droneops.service
#   sudo ./setup-server.sh --uninstall # remove droneops.service
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
UNINSTALL=false

# ── Minimum requirements (warn-only, never blocks install) ───────
MIN_RAM_GB=8
REC_RAM_GB=16
MIN_CPU=4
REC_CPU=6
MIN_DISK_GB=30

while [[ $# -gt 0 ]]; do
  case "$1" in
    --uninstall) UNINSTALL=true; shift ;;
    -h|--help)
      echo "Usage: sudo $0 [--uninstall]"
      echo "  --uninstall  Remove the DroneOpsCommand boot-start systemd unit"
      echo ""
      echo "Note: deploys are handled by the NOC fleet deployer, not this script."
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
  systemctl stop droneops 2>/dev/null || true
  systemctl disable droneops 2>/dev/null || true
  rm -f /etc/systemd/system/droneops.service
  # Belt-and-suspenders: clean up the long-retired autopull units if a
  # legacy install of this script ever put them on disk.
  systemctl stop droneops-autopull.timer 2>/dev/null || true
  systemctl disable droneops-autopull.timer 2>/dev/null || true
  rm -f /etc/systemd/system/droneops-autopull.service
  rm -f /etc/systemd/system/droneops-autopull.timer
  systemctl daemon-reload
  echo -e "${GREEN}All DroneOpsCommand systemd units removed.${NC}"
  exit 0
fi

# ── Detect install directory ─────────────────────────────────────
INSTALL_DIR="$(cd "$(dirname "$0")" && pwd)"

echo ""
echo -e "${CYAN}╔══════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║${NC}  ${BOLD}DroneOpsCommand${NC} — Server Setup                  ${CYAN}║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  Install dir:  ${BOLD}$INSTALL_DIR${NC}"
echo ""
echo -e "  ${YELLOW}Deploys are handled by the NOC fleet deployer (swarmpilot_deployer),${NC}"
echo -e "  ${YELLOW}not by this host. This script only installs the boot stack-start unit.${NC}"
echo ""

# ── Validate ─────────────────────────────────────────────────────
if [ ! -f "$INSTALL_DIR/docker-compose.yml" ]; then
  echo -e "${RED}ERROR: docker-compose.yml not found in $INSTALL_DIR${NC}"
  exit 1
fi

if [ ! -f "$INSTALL_DIR/droneops.service" ]; then
  echo -e "${RED}ERROR: droneops.service not found in $INSTALL_DIR${NC}"
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

# ── Enable and start ─────────────────────────────────────────────
systemctl daemon-reload

echo -e "${CYAN}Enabling droneops.service (auto-start on boot)...${NC}"
systemctl enable droneops.service

# ── Verify ───────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║${NC}  ${BOLD}Setup complete!${NC}                                  ${GREEN}║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  ${BOLD}Boot auto-start:${NC}"
echo -e "    systemctl status droneops"
echo ""
echo -e "  ${BOLD}Manual controls:${NC}"
echo -e "    sudo systemctl start droneops        # start stack"
echo -e "    sudo systemctl stop droneops          # stop stack"
echo -e "    sudo systemctl restart droneops       # restart stack"
echo ""
echo -e "  ${BOLD}Deploys:${NC} handled automatically by the NOC fleet deployer"
echo -e "    (swarmpilot_deployer on HSH-HQ → builds + recreates on push to main)."
echo -e "    Deploy history: https://noc-mastercontrol.barnardhq.com/deploys"
echo ""
echo -e "  ${BOLD}Uninstall:${NC}"
echo -e "    sudo ./setup-server.sh --uninstall"
echo ""
