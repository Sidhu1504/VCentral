#!/usr/bin/env bash
# First-time setup: creates .env with random secrets, a self-signed certificate, then starts vCentral.
set -euo pipefail
cd "$(dirname "$0")/.."

command -v docker >/dev/null || { echo "Docker is not installed. See https://docs.docker.com/engine/install/"; exit 1; }
docker compose version >/dev/null 2>&1 || { echo "The Docker Compose plugin is missing (docker compose)."; exit 1; }
command -v openssl >/dev/null || { echo "openssl is required."; exit 1; }

if [ ! -f .env ]; then
  cp .env.example .env
  chmod 600 .env
  secret=$(openssl rand -base64 48 | tr -d '\n')
  dbpass=$(openssl rand -hex 24)
  sed -i "s|^VCENTRAL_SECRET_KEY=.*|VCENTRAL_SECRET_KEY=${secret}|" .env
  sed -i "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=${dbpass}|" .env
  echo "Created .env with generated secrets."
  echo
  echo "Now edit .env and set at least:"
  echo "  VCENTRAL_VCENTER_HOST, VCENTRAL_VCENTER_USER, VCENTRAL_VCENTER_PASSWORD, VCENTRAL_SUBNETS"
  echo "  VCENTRAL_ADMIN_PASSWORD (temporary; you change it at first sign-in)"
  echo "Then run this script again."
  exit 0
fi

grep -q '^VCENTRAL_VCENTER_HOST=vcenter.example.local' .env && { echo "Set VCENTRAL_VCENTER_HOST in .env first."; exit 1; }

[ -f deploy/certs/vcentral.crt ] || scripts/gen-cert.sh

# Task signing key. Agents trust the first key they see; if it is lost, agents must be reinstalled.
mkdir -p deploy/keys
if [ ! -f deploy/keys/task-signing.key ]; then
  openssl genpkey -algorithm ed25519 -out deploy/keys/task-signing.key
  echo "Created deploy/keys/task-signing.key (keep it with your backups)."
fi
chmod 600 deploy/keys/task-signing.key
if [ "$(id -u)" -eq 0 ]; then
  chown 10001:10001 deploy/keys/task-signing.key   # the user the app container runs as
else
  echo "Note: run 'sudo chown 10001:10001 deploy/keys/task-signing.key' so the app container can read the key."
fi

docker compose up -d --build
echo
echo "vCentral is starting. Open https://$(hostname -I 2>/dev/null | awk '{print $1}')/"
echo "Logs:  docker compose logs -f app worker"
grep -q '^VCENTRAL_ADMIN_PASSWORD=$' .env && \
  echo "No admin password was set; find the generated one with:  docker compose logs app | grep 'initial admin'"
