#!/bin/bash
# Keepalive for Buy Assistant on :6767: the web app plus its hourly watch (listings, alerts,
# the 11:30 digest). Idempotent: starts the app only if it isn't answering. Run from cron
# every 5 minutes and at boot (install.py sets that up). Log: data/server.log.
set -u
export PATH="/usr/local/bin:$HOME/.local/bin:$PATH"  # cron's PATH has no uv
cd "$(dirname "$0")/.." || exit 1
mkdir -p data
exec 9>data/serve.lock
flock -n 9 || exit 0
if curl -s -o /dev/null --max-time 3 "http://127.0.0.1:6767/static/style.css"; then
    exit 0
fi
# Only the app's own keys reach it: the Purelymail pair for alert email and the eBay pair
# for searches. The secrets file holds other credentials too, so it's read in a subshell
# that hands back just these four. No value reaches the log or a command line.
app_credentials() {
    # shellcheck disable=SC1091
    ( [ -r /etc/sandbox-secrets.sh ] && . /etc/sandbox-secrets.sh
      printf '%s\n' "${PURELYMAIL_ADDRESS:-}" "${PURELYMAIL_PASSWORD:-}" \
          "${EBAY_CLIENT_ID:-}" "${EBAY_CLIENT_SECRET:-}" )
}
{
    IFS= read -r PURELYMAIL_ADDRESS; IFS= read -r PURELYMAIL_PASSWORD
    IFS= read -r EBAY_CLIENT_ID; IFS= read -r EBAY_CLIENT_SECRET
} < <(app_credentials)
export PURELYMAIL_ADDRESS PURELYMAIL_PASSWORD EBAY_CLIENT_ID EBAY_CLIENT_SECRET
echo "$(date -Is) starting buy-assist" >> data/server.log
nohup uv run --frozen buy-assist >> data/server.log 2>&1 9>&- &
