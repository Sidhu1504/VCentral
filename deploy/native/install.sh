#!/usr/bin/env bash
# vCentral native install / upgrade for Ubuntu 22.04 or 24.04 (no Docker).
#
#   sudo deploy/native/install.sh [name-or-ip ...]
#       1st run: installs packages and creates /etc/vcentral/vcentral.env, then stops so you can edit it.
#       2nd run: builds the UI and agents, sets up PostgreSQL, systemd services, nginx and HTTPS, starts vCentral.
#       Later runs: upgrade (copy new files first, then run again). Safe to repeat.
#       Optional names/IPs go into the self-signed HTTPS certificate (default: this host's name and IPs).
#
#   deploy/native/install.sh --build-only      build the Python environment, UI and agents only (no root needed;
#                                              set VCENTRAL_PREFIX to a writable folder)
set -euo pipefail

VCENTRAL_VERSION=0.6.0
PREFIX=${VCENTRAL_PREFIX:-/opt/vcentral}
CONF_DIR=/etc/vcentral
ENV_FILE=$CONF_DIR/vcentral.env
CERT_DIR=$CONF_DIR/certs
NODE_VERSION=20.18.0
GO_VERSION=1.23.2
GO_LEGACY_VERSION=1.20.14   # last Go release that supports Windows Server 2008 R2 / 2012 / 2012 R2
SRC=$(cd "$(dirname "$0")/../.." && pwd)
TOOLS=$PREFIX/toolchains

log() { printf '\n==> %s\n' "$*"; }
die() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }

BUILD_ONLY=0
NAMES=()
for arg in "$@"; do
  case "$arg" in
    --build-only) BUILD_ONLY=1 ;;
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
    *) NAMES+=("$arg") ;;
  esac
done

case "$(uname -m)" in
  x86_64) NODE_ARCH=x64; GO_ARCH=amd64 ;;
  aarch64) NODE_ARCH=arm64; GO_ARCH=arm64 ;;
  *) die "Unsupported CPU architecture $(uname -m)" ;;
esac

# -- steps --------------------------------------------------------------------------------------------

install_packages() {
  log "Installing Ubuntu packages"
  # Read os-release in a subshell: it defines VERSION, NAME, ID... which must not leak into this script.
  local distro
  distro=$(. /etc/os-release && echo "${ID:-}|${PRETTY_NAME:-unknown}")
  [ "${distro%%|*}" = ubuntu ] || echo "Warning: this script is written for Ubuntu; continuing on ${distro#*|}."
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -q
  apt-get install -y -q python3 python3-venv postgresql nginx openssl curl xz-utils rsync ca-certificates
  id vcentral >/dev/null 2>&1 || useradd --system --home-dir "$PREFIX" --no-create-home --shell /usr/sbin/nologin vcentral
}

# Browser SSH/RDP: guacd (Apache Guacamole's proxy) from Ubuntu's packages, reachable only from this server.
# Let the worker send ping (ICMP echo) without root, for the IP scan on the IP & subnets page. Only for the
# vcentral group, and only if unprivileged ping is switched off entirely (the Ubuntu default on some installs).
setup_ping() {
  local gid current
  gid=$(getent group vcentral | cut -d: -f3)
  current=$(cat /proc/sys/net/ipv4/ping_group_range 2>/dev/null | tr -s '[:space:]' ' ' | sed 's/ $//')
  [ -n "$gid" ] || return 0
  if [ "$current" = "1 0" ] || [ -f /etc/sysctl.d/60-vcentral-ping.conf ]; then
    echo "net.ipv4.ping_group_range = $gid $gid" > /etc/sysctl.d/60-vcentral-ping.conf
    sysctl -q -p /etc/sysctl.d/60-vcentral-ping.conf || echo "Warning: could not enable ping for vCentral; the IP scan will use TCP probes"
  fi
}

setup_guacd() {
  log "Setting up remote access (guacd)"
  # The SSH and RDP plugins are "recommended" packages of guacd (names differ between Ubuntu releases).
  if ! apt-get install -y -q --install-recommends guacd; then
    echo "Warning: could not install guacd; browser SSH/RDP will be unavailable (everything else works)."
    return 0
  fi
  mkdir -p /etc/guacamole
  cat > /etc/guacamole/guacd.conf <<'EOF'
# Written by the vCentral installer. Only vCentral (on this server) may talk to guacd.
[server]
bind_host = 127.0.0.1
bind_port = 4822
EOF
  systemctl enable guacd >/dev/null 2>&1 || true
  systemctl restart guacd || echo "Warning: guacd did not start; check: journalctl -u guacd"
}

create_env_file() {
  [ -f "$ENV_FILE" ] && return 1
  log "Creating $ENV_FILE with generated secrets"
  mkdir -p "$CONF_DIR"
  local secret dbpass
  secret=$(openssl rand -base64 48 | tr -d '\n')
  dbpass=$(openssl rand -hex 24)
  {
    sed -e '/^POSTGRES_PASSWORD=/d' -e '/^# Database password/d' -e '/^# Random string, at least 32/d' \
        -e "s|^VCENTRAL_SECRET_KEY=.*|VCENTRAL_SECRET_KEY=${secret}|" "$SRC/.env.example"
    cat <<EOF

# --- Native install (set by deploy/native/install.sh) ---------------------------------------------
VCENTRAL_DATABASE_URL=postgresql+psycopg2://vcentral:${dbpass}@127.0.0.1:5432/vcentral
VCENTRAL_STATIC_DIR=$PREFIX/frontend/dist
VCENTRAL_AGENT_DIR=$PREFIX/agent-bin
VCENTRAL_TLS_CERT_FILE=$CERT_DIR/vcentral.crt
VCENTRAL_TASK_SIGNING_KEY_FILE=$CONF_DIR/task-signing.key
EOF
  } > "$ENV_FILE"
  chown root:vcentral "$ENV_FILE"
  chmod 640 "$ENV_FILE"
  return 0
}

copy_source() {
  if [ "$SRC" != "$PREFIX" ]; then
    log "Copying vCentral from $SRC to $PREFIX"
    mkdir -p "$PREFIX"
    rsync -a --delete \
      --exclude .venv --exclude venv --exclude toolchains --exclude agent-bin --exclude .git \
      --exclude frontend/node_modules --exclude frontend/dist --exclude '*.db' --exclude '__pycache__' \
      --exclude .pytest_cache --exclude deploy/certs --exclude .env --exclude .claude \
      "$SRC/" "$PREFIX/"
  fi
}

fetch() { # name url dir check-file
  if [ ! -x "$3/$4" ]; then
    echo "  downloading $1"
    rm -rf "$3" && mkdir -p "$3"
    curl -fsSL "$2" | tar -x$( [[ "$2" == *.xz ]] && echo J || echo z ) -C "$3" --strip-components=1
  fi
}

build() {
  log "Preparing build tools in $TOOLS"
  fetch "Node.js $NODE_VERSION" "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-${NODE_ARCH}.tar.xz" "$TOOLS/node" bin/node
  fetch "Go $GO_VERSION" "https://go.dev/dl/go${GO_VERSION}.linux-${GO_ARCH}.tar.gz" "$TOOLS/go" bin/go
  fetch "Go $GO_LEGACY_VERSION" "https://go.dev/dl/go${GO_LEGACY_VERSION}.linux-${GO_ARCH}.tar.gz" "$TOOLS/go1.20" bin/go

  log "Python environment"
  if [ ! -x "$PREFIX/venv/bin/pip" ]; then  # missing, or left half-made by an earlier failed run
    rm -rf "$PREFIX/venv"
    python3 -m venv "$PREFIX/venv" || die "Could not create the Python environment (is python3-venv installed?)"
  fi
  "$PREFIX/venv/bin/pip" install -q --upgrade pip
  "$PREFIX/venv/bin/pip" install -q -r "$PREFIX/backend/requirements.txt"

  log "Building the web UI"
  (cd "$PREFIX/frontend" && PATH="$TOOLS/node/bin:$PATH" npm ci --no-audit --no-fund --loglevel=error \
     && PATH="$TOOLS/node/bin:$PATH" npm run build --silent)

  log "Building the agents (Linux, Windows, legacy Windows)"
  (cd "$PREFIX/agent" && PATH="$TOOLS/go/bin:$PATH" GOPATH="$TOOLS/gopath" GOCACHE="$TOOLS/gocache" \
     GOFLAGS=-modcacherw GOTOOLCHAIN=local ./build.sh "$PREFIX/agent-bin" "$VCENTRAL_VERSION" "$TOOLS/go1.20/bin/go")
}

env_value() { grep -E "^$1=" "$ENV_FILE" | tail -1 | cut -d= -f2-; }

setup_database() {
  log "PostgreSQL database"
  systemctl enable --now postgresql >/dev/null
  local url pass
  url=$(env_value VCENTRAL_DATABASE_URL)
  pass=$(sed -E 's|^[^:]+://[^:]+:([^@]+)@.*|\1|' <<<"$url")
  [ -n "$pass" ] || die "Could not read the database password from VCENTRAL_DATABASE_URL in $ENV_FILE"
  # Statements go through stdin so the password never appears in the process list.
  sudo -u postgres psql -q -v ON_ERROR_STOP=1 >/dev/null <<SQL
DO \$\$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'vcentral') THEN CREATE ROLE vcentral LOGIN; END IF;
END \$\$;
ALTER ROLE vcentral WITH LOGIN PASSWORD '${pass}';
SQL
  if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='vcentral'" | grep -q 1; then
    sudo -u postgres createdb -O vcentral vcentral
  fi
}

setup_signing_key() {
  # Signs tasks for agents. Agents trust the first key they see and refuse others, so never replace it casually:
  # if it is lost, every agent has to be reinstalled before it accepts tasks again.
  local key=$CONF_DIR/task-signing.key
  if [ ! -f "$key" ]; then
    log "Creating the task signing key ($key)"
    openssl genpkey -algorithm ed25519 -out "$key"
  fi
  chown root:vcentral "$key"
  chmod 640 "$key"
  grep -q '^VCENTRAL_TASK_SIGNING_KEY_FILE=' "$ENV_FILE" \
    || printf '\n# Task signing key (added by install.sh)\nVCENTRAL_TASK_SIGNING_KEY_FILE=%s\n' "$key" >> "$ENV_FILE"
}

setup_certificate() {
  if [ ! -f "$CERT_DIR/vcentral.crt" ]; then
    log "Creating a self-signed HTTPS certificate"
    CERT_DIR="$CERT_DIR" "$PREFIX/scripts/gen-cert.sh" "${NAMES[@]}"
  fi
  chmod 755 "$CERT_DIR"
  chmod 644 "$CERT_DIR/vcentral.crt"
  chmod 600 "$CERT_DIR/vcentral.key"
}

setup_services() {
  log "systemd services"
  install -m 644 "$PREFIX/deploy/native/vcentral-api.service" /etc/systemd/system/vcentral-api.service
  install -m 644 "$PREFIX/deploy/native/vcentral-worker.service" /etc/systemd/system/vcentral-worker.service
  systemctl daemon-reload
  systemctl enable vcentral-api vcentral-worker >/dev/null
  systemctl restart vcentral-api
  # The API creates and upgrades the database tables; start the worker once it answers.
  for _ in $(seq 1 60); do
    curl -fsS http://127.0.0.1:8000/api/health >/dev/null 2>&1 && break
    sleep 1
  done
  curl -fsS http://127.0.0.1:8000/api/health >/dev/null 2>&1 \
    || die "The API did not start. See: journalctl -u vcentral-api -n 50"
  systemctl restart vcentral-worker

  log "nginx (HTTPS)"
  install -m 644 "$PREFIX/deploy/native/nginx-vcentral.conf" /etc/nginx/sites-available/vcentral
  ln -sf /etc/nginx/sites-available/vcentral /etc/nginx/sites-enabled/vcentral
  rm -f /etc/nginx/sites-enabled/default
  nginx -t -q || die "nginx configuration test failed"
  systemctl enable nginx >/dev/null
  systemctl reload nginx || systemctl restart nginx

  if command -v ufw >/dev/null && ufw status | grep -q "Status: active"; then
    ufw allow 80/tcp >/dev/null && ufw allow 443/tcp >/dev/null
    echo "  ufw: allowed ports 80 and 443"
  fi
}

# -- main ---------------------------------------------------------------------------------------------

if [ "$BUILD_ONLY" = 1 ]; then
  copy_source
  build
  log "Build finished in $PREFIX"
  exit 0
fi

[ "$(id -u)" -eq 0 ] || die "Run as root: sudo $0"
install_packages
if create_env_file; then
  cat <<EOF

vCentral configuration created: $ENV_FILE
Edit it now (sudo nano $ENV_FILE) and set at least:
  VCENTRAL_VCENTER_HOST, VCENTRAL_VCENTER_USER, VCENTRAL_VCENTER_PASSWORD, VCENTRAL_SUBNETS,
  VCENTRAL_PUBLIC_URL and VCENTRAL_ADMIN_PASSWORD (temporary; you change it at first sign-in).
Then run this script again.
EOF
  exit 0
fi
grep -q '^VCENTRAL_VCENTER_HOST=vcenter.example.local' "$ENV_FILE" \
  && die "Set VCENTRAL_VCENTER_HOST (and the other vCenter settings) in $ENV_FILE first."

copy_source
build
chown -R root:root "$PREFIX"
chmod -R go-w "$PREFIX"
setup_database
setup_certificate
setup_signing_key
setup_guacd
setup_ping
setup_services

ip=$(hostname -I 2>/dev/null | awk '{print $1}')
url=$(env_value VCENTRAL_PUBLIC_URL)
log "vCentral $VCENTRAL_VERSION is running"
cat <<EOF
Open:     ${url:-https://$ip/}
Status:   systemctl status vcentral-api vcentral-worker
Logs:     journalctl -u vcentral-api -u vcentral-worker -f
Config:   $ENV_FILE   (after editing: sudo systemctl restart vcentral-api vcentral-worker)
EOF
if [ -z "$(env_value VCENTRAL_ADMIN_PASSWORD)" ]; then
  echo "No admin password was set; find the generated one with: sudo journalctl -u vcentral-api | grep 'initial admin'"
fi
