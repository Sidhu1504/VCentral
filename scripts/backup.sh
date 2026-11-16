#!/usr/bin/env bash
# Dump the vCentral database to backups/vcentral-YYYYmmdd-HHMMSS.sql.gz
# Restore with:  gunzip -c FILE | docker compose exec -T db psql -U vcentral -d vcentral
# Also keep a copy of .env somewhere safe: it holds the secret key and database password.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p backups
out="backups/vcentral-$(date +%Y%m%d-%H%M%S).sql.gz"
docker compose exec -T db pg_dump -U vcentral -d vcentral --no-owner | gzip > "$out"
chmod 600 "$out"
echo "Backup written to $out"
# Keep the 14 most recent backups
ls -1t backups/vcentral-*.sql.gz | tail -n +15 | xargs -r rm --
