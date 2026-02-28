#!/usr/bin/env bash
# backup_db.sh — create a consistent timestamped backup of autopost.db.
#
# Uses SQLite's ".backup" command which is safe to run while the bot is live
# (it creates a clean snapshot even with WAL mode and active connections).
#
# ── Suggested cron (run as the autopost user) ─────────────────────────────────
#   0 4 * * * /home/autopost/automation-network/autopost/scripts/backup_db.sh >> /home/autopost/automation-network/autopost/logs/backup.log 2>&1
#
# ── Optional: sync to DigitalOcean Spaces ────────────────────────────────────
#   Install s3cmd: sudo apt install s3cmd
#   Configure:    s3cmd --configure
#   Set DO_SPACES_BUCKET below and uncomment the s3cmd line.
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

# ── Config ────────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AUTOPOST_DIR="$(dirname "$SCRIPT_DIR")"
DB_FILE="$AUTOPOST_DIR/data/autopost.db"
BACKUP_DIR="$AUTOPOST_DIR/data/backups"
KEEP_DAYS=7                          # how many daily backups to retain locally
DO_SPACES_BUCKET=""                  # e.g. "s3://my-bucket/autopost-backups" — leave blank to skip

# ── Helpers ───────────────────────────────────────────────────────────────────
info()  { echo "[backup] $(date -u '+%Y-%m-%d %H:%M UTC') $*"; }
error() { echo "[backup] ERROR: $*" >&2; exit 1; }

# ── Guard ─────────────────────────────────────────────────────────────────────
[ -f "$DB_FILE" ] || error "DB not found: $DB_FILE"

# ── Create backup ─────────────────────────────────────────────────────────────
mkdir -p "$BACKUP_DIR"
TIMESTAMP=$(date -u +"%Y%m%d_%H%M")
BACKUP_FILE="$BACKUP_DIR/autopost_${TIMESTAMP}.db"

info "Backing up $DB_FILE → $BACKUP_FILE"
sqlite3 "$DB_FILE" ".backup '$BACKUP_FILE'"

SIZE=$(du -sh "$BACKUP_FILE" | cut -f1)
info "Backup created: $BACKUP_FILE ($SIZE)"

# ── Optional: upload to DigitalOcean Spaces ───────────────────────────────────
if [ -n "$DO_SPACES_BUCKET" ]; then
    if command -v s3cmd &>/dev/null; then
        info "Uploading to $DO_SPACES_BUCKET..."
        s3cmd put "$BACKUP_FILE" "$DO_SPACES_BUCKET/" --quiet
        info "Upload complete."
    else
        info "WARNING: DO_SPACES_BUCKET set but s3cmd not found — skipping upload"
    fi
fi

# ── Rotate old local backups ──────────────────────────────────────────────────
BEFORE=$(ls "$BACKUP_DIR"/autopost_*.db 2>/dev/null | wc -l)
find "$BACKUP_DIR" -name "autopost_*.db" -mtime "+${KEEP_DAYS}" -delete
AFTER=$(ls "$BACKUP_DIR"/autopost_*.db 2>/dev/null | wc -l)
REMOVED=$(( BEFORE - AFTER ))

if [ "$REMOVED" -gt 0 ]; then
    info "Rotated $REMOVED old backup(s). Keeping $AFTER."
else
    info "No rotation needed. Local backups: $AFTER."
fi
