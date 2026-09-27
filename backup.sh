#!/bin/bash
# Backs up the Disc Golf Tracker's actual data (SQLite DB + uploaded
# photos — the code itself is already safe in GitHub) and pushes it to
# cloud storage via rclone, so a local drive failure doesn't lose it.
set -e

APP_DIR="/opt/discgolf-tracker"
LOCAL_BACKUP_DIR="/opt/discgolf-backups"
RCLONE_REMOTE="b2backup:discgolf-backups"   # change to match your `rclone config` remote name
KEEP_LOCAL=7                                 # how many local copies to retain

mkdir -p "$LOCAL_BACKUP_DIR"

TIMESTAMP=$(date +%Y%m%d-%H%M%S)
ARCHIVE="$LOCAL_BACKUP_DIR/discgolf-backup-$TIMESTAMP.tar.gz"

tar czf "$ARCHIVE" -C "$APP_DIR" instance

# Upload to cloud storage
rclone copy "$ARCHIVE" "$RCLONE_REMOTE"

# Keep only the most recent local copies — the cloud copy is the real
# long-term backup, this just avoids filling the local disk.
ls -1t "$LOCAL_BACKUP_DIR"/discgolf-backup-*.tar.gz 2>/dev/null | tail -n +$((KEEP_LOCAL + 1)) | xargs -r rm --

echo "Backup complete: $ARCHIVE"
