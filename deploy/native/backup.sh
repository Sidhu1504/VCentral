#!/usr/bin/env bash
# Back up the vCentral database (native install) to /var/backups/vcentral, keeping the 14 most recent.
#   sudo deploy/native/backup.sh
# Restore:  gunzip -c FILE | sudo -u postgres psql -d vcentral
# Also keep a copy of /etc/vcentral (configuration, secrets and the HTTPS certificate) somewhere safe.
set -euo pipefail
DEST=${1:-/var/backups/vcentral}
mkdir -p "$DEST"
chmod 700 "$DEST"
out="$DEST/vcentral-$(date +%Y%m%d-%H%M%S).sql.gz"
sudo -u postgres pg_dump -d vcentral --no-owner | gzip > "$out"
chmod 600 "$out"
echo "Backup written to $out"
ls -1t "$DEST"/vcentral-*.sql.gz | tail -n +15 | xargs -r rm --
