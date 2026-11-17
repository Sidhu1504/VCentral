#!/bin/sh
# Print a vCentral report on the vCentral server (native install), e.g. for cron or e-mail.
#
#   sudo scripts/report.sh                               # list the reports
#   sudo scripts/report.sh tools                         # VMs without (working) VMware Tools, as CSV
#   sudo scripts/report.sh tools --format table          # readable in the terminal
#   sudo scripts/report.sh usage --days 7 -o /tmp/usage.csv
#
# Same data as the Reports page. Docker install: docker compose exec app python -m app.cli report tools
set -eu

PREFIX=${VCENTRAL_PREFIX:-/opt/vcentral}
ENV_FILE=${VCENTRAL_ENV_FILE:-/etc/vcentral/vcentral.env}

[ -r "$ENV_FILE" ] || { echo "Cannot read $ENV_FILE (run with sudo)." >&2; exit 1; }
[ -x "$PREFIX/venv/bin/python" ] || { echo "vCentral is not installed in $PREFIX." >&2; exit 1; }

set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a
cd "$PREFIX/backend"
exec "$PREFIX/venv/bin/python" -m app.cli report "$@"
