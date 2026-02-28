#!/usr/bin/env bash
# deploy.sh — pull latest code, sync dependencies, restart the service.
#
# Run this on the DigitalOcean droplet as the autopost user:
#   bash /home/autopost/automation-network/autopost/scripts/deploy.sh
#
# The script:
#   1. git pull origin main
#   2. pip install (new/updated packages only)
#   3. Restart the systemd service
#   4. Print live status

set -euo pipefail

# ── Config ────────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AUTOPOST_DIR="$(dirname "$SCRIPT_DIR")"         # .../automation-network/autopost
REPO_ROOT="$(dirname "$AUTOPOST_DIR")"          # .../automation-network
VENV_PYTHON="$AUTOPOST_DIR/venv/bin/python"
SERVICE="autopost"

# ── Helpers ───────────────────────────────────────────────────────────────────
info()  { echo "[deploy] $*"; }
error() { echo "[deploy] ERROR: $*" >&2; exit 1; }

# ── 1. Pull latest code ───────────────────────────────────────────────────────
info "Pulling latest code from origin/main..."
cd "$REPO_ROOT"
git fetch origin
git pull origin main
info "Git: $(git log -1 --oneline)"

# ── 2. Install / update dependencies ─────────────────────────────────────────
info "Syncing Python dependencies..."
if [ ! -f "$VENV_PYTHON" ]; then
    error "Virtualenv not found at $VENV_PYTHON — run setup first (see README)"
fi
"$VENV_PYTHON" -m pip install -q --upgrade pip
"$VENV_PYTHON" -m pip install -q -r "$AUTOPOST_DIR/requirements.txt"
info "Dependencies up to date."

# ── 3. Re-seed sources DB (safe — INSERT OR IGNORE) ──────────────────────────
info "Re-seeding source config into DB..."
cd "$AUTOPOST_DIR"
"$VENV_PYTHON" scripts/setup_db.py

# ── 4. Restart service ────────────────────────────────────────────────────────
info "Restarting systemd service '$SERVICE'..."
sudo systemctl restart "$SERVICE"
sleep 3

# ── 5. Status check ───────────────────────────────────────────────────────────
STATUS=$(systemctl is-active "$SERVICE" || true)
if [ "$STATUS" = "active" ]; then
    info "Service is running (active)."
else
    error "Service failed to start — status: $STATUS"
fi

echo ""
sudo systemctl status "$SERVICE" --no-pager --lines=10
echo ""
info "Deploy complete."
