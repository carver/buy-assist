#!/bin/bash
# Restart Buy Assistant on :6767, for new code, dependencies, migrations or credentials:
# stops whatever is listening on the port, then starts the app the way the keepalive does.
set -u
cd "$(dirname "$0")" || exit 1
pid=$(ss -ltnpH 'sport = :6767' | grep -o 'pid=[0-9]*' | head -n 1 | cut -d= -f2)
if [ -n "$pid" ]; then
    kill "$pid"
    for _ in $(seq 50); do
        kill -0 "$pid" 2>/dev/null || break
        sleep 0.2
    done
    if kill -0 "$pid" 2>/dev/null; then
        echo "buy-assist (pid $pid) didn't stop within 10s" >&2
        exit 1
    fi
fi
uv sync -q --frozen || exit 1
scripts/serve.sh
for _ in $(seq 50); do
    if curl -s -o /dev/null --max-time 1 "http://127.0.0.1:6767/static/style.css"; then
        echo "buy-assist is up on :6767"
        exit 0
    fi
    sleep 0.2
done
echo "buy-assist didn't answer within 10s; see data/server.log" >&2
exit 1
