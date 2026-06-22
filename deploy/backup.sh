#!/bin/bash
# Daily backup: SQLite + JSON catalog
set -euo pipefail
DEST="/var/backups/poselyanov3dprint"
STAMP=$(date +%Y%m%d-%H%M)
mkdir -p "$DEST"
cp /var/lib/poselyanov3dprint/users.db "$DEST/users-$STAMP.db" 2>/dev/null || true
tar -czf "$DEST/catalog-$STAMP.tar.gz" -C /opt/poselyanov3dprint products.json custom_products.json categories.json filaments.json 2>/dev/null || true
find "$DEST" -type f -mtime +14 -delete
