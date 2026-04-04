#!/usr/bin/env bash
# LLM Proxy — database backup script
# Usage: backup.sh [--keep N] [--dry-run]
#
# Environment variables (with defaults):
#   RAW_DB        /data/logs/raw.db
#   ANALYTICS_DB  /data/analytics/analytics.db
#   BACKUP_DIR    /data/backups
set -euo pipefail

RAW_DB="${RAW_DB:-/data/logs/raw.db}"
ANALYTICS_DB="${ANALYTICS_DB:-/data/analytics/analytics.db}"
BACKUP_DIR="${BACKUP_DIR:-/data/backups}"
KEEP=14
DRY_RUN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --keep)   KEEP="$2";  shift 2 ;;
    --keep=*) KEEP="${1#*=}";  shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

TS=$(date -u +"%Y%m%dT%H%M%SZ")

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "[dry-run] Would back up to: $BACKUP_DIR"
  echo "[dry-run] raw.db      -> $BACKUP_DIR/raw_${TS}.db"
  echo "[dry-run] analytics.db -> $BACKUP_DIR/analytics_${TS}.db"
  exit 0
fi

mkdir -p "$BACKUP_DIR"

for DB_PATH in "$RAW_DB" "$ANALYTICS_DB"; do
  if [[ ! -f "$DB_PATH" ]]; then
    echo "Skipping (not found): $DB_PATH"
    continue
  fi
  STEM=$(basename "$DB_PATH" .db)
  DEST="$BACKUP_DIR/${STEM}_${TS}.db"
  cp "$DB_PATH" "$DEST"
  echo "Backed up: $DB_PATH -> $DEST ($(du -h "$DEST" | cut -f1))"
done

# Prune old backups per database stem
for STEM in raw analytics; do
  mapfile -t OLD_FILES < <(ls -t "$BACKUP_DIR/${STEM}_"*.db 2>/dev/null || true)
  if [[ ${#OLD_FILES[@]} -gt $KEEP ]]; then
    for OLD in "${OLD_FILES[@]:$KEEP}"; do
      echo "Pruning old backup: $OLD"
      rm -f "$OLD"
    done
  fi
done

echo "Backup complete: $BACKUP_DIR (keeping last $KEEP per DB)"
