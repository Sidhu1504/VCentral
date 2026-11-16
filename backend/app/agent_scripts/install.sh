#!/bin/sh
# vCentral agent installer for Linux.
#   sh install.sh --server https://vcentral.example --token vct_... [--fingerprint HEX] [--cacert FILE] [--template]
#   sh install.sh --server https://vcentral.example --upgrade [--cacert FILE]
# --upgrade: replace an installed agent (any version) with the one on this vCentral, keeping its identity.
# --template: install in a VM template; every VM deployed from it enrolls itself (needs a reusable token).
# --cacert lets curl/wget trust a self-signed vCentral certificate (the UI's command creates it after
# checking its fingerprint). The agent itself pins the certificate with --fingerprint.
set -eu

SERVER=""; TOKEN=""; FP=""; CACERT=""; EXTRA=""; UPGRADE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --server) SERVER="$2"; shift 2 ;;
    --token) TOKEN="$2"; shift 2 ;;
    --fingerprint) FP="$2"; shift 2 ;;
    --cacert) CACERT="$2"; shift 2 ;;
    --template|--disable-tasks) EXTRA="$EXTRA $1"; shift ;;
    --upgrade) UPGRADE=1; shift ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done
[ -n "$SERVER" ] && { [ -n "$TOKEN" ] || [ "$UPGRADE" = 1 ]; } || { echo "Usage: install.sh --server URL (--token TOKEN | --upgrade) [--fingerprint HEX] [--cacert FILE]" >&2; exit 2; }
[ "$(id -u)" -eq 0 ] || { echo "Please run as root (use sudo)." >&2; exit 1; }
command -v systemctl >/dev/null 2>&1 || { echo "This installer needs systemd." >&2; exit 1; }

case "$(uname -m)" in
  x86_64|amd64) ARCH=amd64 ;;
  i386|i486|i586|i686) ARCH=386 ;;
  aarch64|arm64) ARCH=arm64 ;;
  *) echo "Unsupported CPU architecture: $(uname -m)" >&2; exit 1 ;;
esac

# Download next to the final location: /tmp is often mounted noexec on hardened servers.
mkdir -p /usr/local/bin
BIN=/usr/local/bin/vcentral-agent.download
trap 'rm -f "$BIN"' EXIT
URL="${SERVER%/}/api/agent/download/linux-$ARCH"
echo "Downloading vCentral agent (linux-$ARCH)..."
if command -v curl >/dev/null 2>&1; then
  if [ -n "$CACERT" ]; then curl -fsSL --cacert "$CACERT" -o "$BIN" "$URL"; else curl -fsSL -o "$BIN" "$URL"; fi
elif command -v wget >/dev/null 2>&1; then
  if [ -n "$CACERT" ]; then wget -q --ca-certificate="$CACERT" -O "$BIN" "$URL"; else wget -q -O "$BIN" "$URL"; fi
else
  echo "curl or wget is required." >&2; exit 1
fi
chmod 755 "$BIN"

if [ "$UPGRADE" = 1 ]; then
  "$BIN" upgrade
  exit $?
fi

set -- install --server "$SERVER" --token "$TOKEN"
[ -n "$FP" ] && set -- "$@" --fingerprint "$FP"
# shellcheck disable=SC2086
set -- "$@" $EXTRA
"$BIN" "$@"
