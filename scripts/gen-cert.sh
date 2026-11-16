#!/usr/bin/env bash
# Create a self-signed HTTPS certificate for vCentral.
# Usage: scripts/gen-cert.sh [hostname-or-ip ...]   (defaults to this machine's hostname and IPs)
# To use a certificate from your own CA instead, place it at deploy/certs/vcentral.crt and .key.
# CERT_DIR=/etc/vcentral/certs scripts/gen-cert.sh ...   writes there instead (native installs).
set -euo pipefail
cd "$(dirname "$0")/.."
CERT_DIR=${CERT_DIR:-deploy/certs}
mkdir -p "$CERT_DIR"

names=("$@")
if [ ${#names[@]} -eq 0 ]; then
  names=("$(hostname -f 2>/dev/null || hostname)")
  for ip in $(hostname -I 2>/dev/null); do names+=("$ip"); done
fi

san=""
for n in "${names[@]}"; do
  if [[ "$n" =~ ^[0-9.]+$ || "$n" == *:* ]]; then san+="IP:$n,"; else san+="DNS:$n,"; fi
done
san="${san%,}"

openssl req -x509 -newkey rsa:3072 -sha256 -days 825 -nodes \
  -keyout "$CERT_DIR/vcentral.key" -out "$CERT_DIR/vcentral.crt" \
  -subj "/CN=${names[0]}" -addext "subjectAltName=${san}" 2>/dev/null
chmod 600 "$CERT_DIR/vcentral.key"
chmod 644 "$CERT_DIR/vcentral.crt"
echo "Created $CERT_DIR/vcentral.crt for: ${san}"
