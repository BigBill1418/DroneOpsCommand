#!/bin/bash
set -e

# ── DroneOpsCommand Update Script ─────────────────────────────────
#
# Interactive deploy & promote tool.
# Run ./update.sh and follow the prompts, or pass args directly:
#   ./update.sh dev            Pull claude/dev, rebuild changed services
#   ./update.sh prod           Pull main, rebuild changed services
#   ./update.sh promote        Merge claude/dev → main, rebuild prod
#   ./update.sh status         Show branch & service info
#   ./update.sh dev --clean    Full dev rebuild, no Docker cache
#   ./update.sh prod --clean   Full prod rebuild, no Docker cache
#
# ──────────────────────────────────────────────────────────────────

INSTALL_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$INSTALL_DIR"

DOCKER="docker compose"
if [ "$(id -u)" -ne 0 ] && ! docker info >/dev/null 2>&1; then
  DOCKER="sudo docker compose"
fi

DEPLOY_MARKER=".last_deployed_commit"

# ── Colors ───────────────────────────────────────────────────────
CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BOLD='\033[1m'
NC='\033[0m'

# ── Helper functions ─────────────────────────────────────────────
banner() {
  echo ""
  echo -e "${CYAN}╔══════════════════════════════════════════════════╗${NC}"
  echo -e "${CYAN}║${NC}  ${BOLD}DroneOpsCommand${NC} — $1"
  echo -e "${CYAN}╚══════════════════════════════════════════════════╝${NC}"
  echo ""
}

fetch_branch() {
  local branch="$1"
  echo -e "${CYAN}Fetching ${branch}...${NC}"
  if ! git fetch origin "$branch"; then
    echo -e "${RED}ERROR: Failed to fetch '$branch'. Check remote:${NC}"
    git remote -v
    exit 1
  fi
}

sync_branch() {
  local branch="$1"
  git checkout "$branch" 2>/dev/null || git checkout -b "$branch" "origin/$branch"
  git reset --hard "origin/$branch"
}

detect_and_rebuild() {
  local branch="$1"
  local cache_flag="$2"
  local current_commit
  current_commit=$(git rev-parse HEAD)

  local prev_commit=""
  if [ -f "$DEPLOY_MARKER" ]; then
    prev_commit=$(cat "$DEPLOY_MARKER")
  fi

  # Detect changes
  local rebuild_frontend=false
  local rebuild_backend=false
  local rebuild_parser=false
  local changed

  if [ -n "$prev_commit" ] && git cat-file -t "$prev_commit" >/dev/null 2>&1; then
    # Compare actual tree contents (not commit chain) — handles merge commits correctly
    changed=$(git diff --name-only "$prev_commit".."$current_commit" 2>/dev/null || echo "all")
  else
    changed="all"
  fi

  if [ "$changed" = "all" ]; then
    echo -e "${YELLOW}First deploy or branch switch — rebuilding all${NC}"
    rebuild_frontend=true
    rebuild_backend=true
    rebuild_parser=true
  elif [ -z "$changed" ]; then
    echo -e "${GREEN}Already at latest ($(echo "$current_commit" | head -c 7)), nothing changed${NC}"
  else
    echo -e "Changes: $(echo "${prev_commit:-none}" | head -c 7) → $(echo "$current_commit" | head -c 7)"
    echo "$changed" | grep -q "^frontend/" && rebuild_frontend=true
    echo "$changed" | grep -q "^backend/" && rebuild_backend=true
    echo "$changed" | grep -q "^flight-parser/" && rebuild_parser=true

    $rebuild_frontend && echo "  → frontend"
    $rebuild_backend  && echo "  → backend"
    $rebuild_parser   && echo "  → flight-parser"

    if ! $rebuild_frontend && ! $rebuild_backend && ! $rebuild_parser; then
      echo "  → non-service files only (no rebuild needed)"
    fi
  fi

  # Force rebuild all with --clean or --all
  if [ "$cache_flag" = "--no-cache" ] || [ "$cache_flag" = "--all" ]; then
    rebuild_frontend=true
    rebuild_backend=true
    rebuild_parser=true
  fi

  local docker_cache=""
  [ "$cache_flag" = "--no-cache" ] && docker_cache="--no-cache"

  # Build
  $rebuild_frontend && echo -e "${CYAN}Building frontend...${NC}" && $DOCKER build $docker_cache frontend
  $rebuild_backend  && echo -e "${CYAN}Building backend + worker...${NC}" && $DOCKER build $docker_cache backend worker
  $rebuild_parser   && echo -e "${CYAN}Building flight-parser...${NC}" && $DOCKER build $docker_cache flight-parser

  if $rebuild_frontend || $rebuild_backend || $rebuild_parser; then
    echo -e "${CYAN}Restarting services...${NC}"
    $DOCKER up -d
  else
    echo -e "${GREEN}No services to rebuild${NC}"
  fi

  # Save state
  echo "$current_commit" > "$DEPLOY_MARKER"

  echo ""
  echo -e "${GREEN}Done — $(echo "$current_commit" | head -c 7)${NC}"
  $DOCKER ps
}

do_dev() {
  local flag="$1"
  banner "Update DEV (claude/dev)"
  fetch_branch "claude/dev"
  sync_branch "claude/dev"
  detect_and_rebuild "claude/dev" "$flag"
}

do_prod() {
  local flag="$1"
  banner "Update PROD (main)"
  fetch_branch "main"
  sync_branch "main"
  detect_and_rebuild "main" "$flag"
}

do_promote() {
  banner "Promote DEV → PROD"

  # Fetch both branches
  fetch_branch "claude/dev"
  fetch_branch "main"

  # Show what will be promoted
  local dev_commit main_commit
  dev_commit=$(git rev-parse origin/claude/dev)
  main_commit=$(git rev-parse origin/main)

  if [ "$dev_commit" = "$main_commit" ]; then
    echo -e "${GREEN}claude/dev and main are already identical. Nothing to promote.${NC}"
    return
  fi

  echo -e "${BOLD}Commits to promote:${NC}"
  git log --oneline "origin/main..origin/claude/dev"
  echo ""

  echo -e "${YELLOW}This will merge claude/dev into main (no rebuild).${NC}"
  read -r -p "Continue? [y/N] " confirm
  if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
    echo "Aborted."
    return
  fi

  # Merge dev into main
  echo -e "${CYAN}Merging claude/dev → main...${NC}"
  git checkout main 2>/dev/null || git checkout -b main origin/main
  git reset --hard origin/main
  git merge origin/claude/dev -m "Promote claude/dev to production"

  # Push main
  echo -e "${CYAN}Pushing main...${NC}"
  git push origin main

  # Switch back to dev so the working tree stays on the running branch
  echo -e "${CYAN}Switching back to claude/dev...${NC}"
  git checkout claude/dev

  echo ""
  echo -e "${GREEN}Done — main updated to $(echo "$dev_commit" | head -c 7)${NC}"
}

do_status() {
  banner "Status"

  fetch_branch "claude/dev" 2>/dev/null
  fetch_branch "main" 2>/dev/null

  local dev_commit main_commit
  dev_commit=$(git rev-parse origin/claude/dev 2>/dev/null || echo "not found")
  main_commit=$(git rev-parse origin/main 2>/dev/null || echo "not found")

  echo -e "${BOLD}Branches:${NC}"
  echo -e "  claude/dev : $(echo "$dev_commit" | head -c 7)"
  echo -e "  main       : $(echo "$main_commit" | head -c 7)"
  echo ""

  if [ "$dev_commit" != "$main_commit" ] && [ "$dev_commit" != "not found" ] && [ "$main_commit" != "not found" ]; then
    local ahead
    ahead=$(git rev-list --count "origin/main..origin/claude/dev")
    echo -e "${YELLOW}claude/dev is ${ahead} commit(s) ahead of main${NC}"
    echo ""
    echo -e "${BOLD}Unpromoted commits:${NC}"
    git log --oneline "origin/main..origin/claude/dev"
  else
    echo -e "${GREEN}Branches are in sync${NC}"
  fi

  echo ""
  echo -e "${BOLD}Running services:${NC}"
  $DOCKER ps
}

# ── Interactive menu ─────────────────────────────────────────────
show_menu() {
  echo ""
  echo -e "${CYAN}╔══════════════════════════════════════════════════╗${NC}"
  echo -e "${CYAN}║${NC}  ${BOLD}DroneOpsCommand${NC} — Server Management             ${CYAN}║${NC}"
  echo -e "${CYAN}╠══════════════════════════════════════════════════╣${NC}"
  echo -e "${CYAN}║${NC}                                                  ${CYAN}║${NC}"
  echo -e "${CYAN}║${NC}  ${BOLD}1)${NC}  Update DEV    — pull claude/dev & rebuild    ${CYAN}║${NC}"
  echo -e "${CYAN}║${NC}  ${BOLD}2)${NC}  Update PROD   — pull main & rebuild          ${CYAN}║${NC}"
  echo -e "${CYAN}║${NC}  ${BOLD}3)${NC}  Promote       — merge dev → main & deploy   ${CYAN}║${NC}"
  echo -e "${CYAN}║${NC}  ${BOLD}4)${NC}  Status        — show branch & service info   ${CYAN}║${NC}"
  echo -e "${CYAN}║${NC}  ${BOLD}5)${NC}  Clean DEV     — full rebuild, no cache (dev)  ${CYAN}║${NC}"
  echo -e "${CYAN}║${NC}  ${BOLD}6)${NC}  Clean PROD    — full rebuild, no cache (main) ${CYAN}║${NC}"
  echo -e "${CYAN}║${NC}  ${BOLD}7)${NC}  Exit                                         ${CYAN}║${NC}"
  echo -e "${CYAN}║${NC}                                                  ${CYAN}║${NC}"
  echo -e "${CYAN}╚══════════════════════════════════════════════════╝${NC}"
  echo ""
  read -r -p "Select [1-7]: " choice

  case "$choice" in
    1) do_dev "" ;;
    2) do_prod "" ;;
    3) do_promote ;;
    4) do_status ;;
    5) do_dev "--no-cache" ;;
    6) do_prod "--no-cache" ;;
    7) echo "Bye."; exit 0 ;;
    *) echo -e "${RED}Invalid choice${NC}"; show_menu ;;
  esac
}

# ── Main ─────────────────────────────────────────────────────────
MODE="${1:-}"
FLAG="${2:-}"

# Allow flag as first arg for backwards compat
if [ "$MODE" = "--clean" ] || [ "$MODE" = "--all" ]; then
  FLAG="$MODE"
  MODE="dev"
fi

case "$MODE" in
  dev)      do_dev "$( [ "$FLAG" = "--clean" ] && echo "--no-cache" || [ "$FLAG" = "--all" ] && echo "--all" || echo "" )" ;;
  prod)     do_prod "$( [ "$FLAG" = "--clean" ] && echo "--no-cache" || [ "$FLAG" = "--all" ] && echo "--all" || echo "" )" ;;
  promote)  do_promote ;;
  status)   do_status ;;
  "")       show_menu ;;
  *)
    echo "Usage: $0 [dev|prod|promote|status] [--clean|--all]"
    echo "  Or just run $0 with no args for the interactive menu."
    exit 1
    ;;
esac
