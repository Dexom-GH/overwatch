#!/usr/bin/env bash
# TARGET (Jetson) — startup-precondition health-check / watchdog (#55). Verifies
# the system is ready BEFORE the pipeline comes up:
#   - capture source(s) reachable (RTSP camera TCP connect / ZED device present)
#   - detector + tracker configs and the ReID engine file present
#   - bus transport endpoint bindable (ZeroMQ)
#   - SQLite EventStore writable
#
# Distinct from the #43 bounded smoke-check (50_smoke_check.sh), which only checks
# that the package + declared deps import and the config validates. This checks
# RUNTIME preconditions. Runnable on demand and at boot (usable as a systemd
# ExecStartPre); invoked non-fatally by deploy.sh. Exit 0 = all preconditions
# pass, 1 = at least one failed.
#
#   Env: OVERWATCH_VENV    venv dir (default: /srv/farmproject/venv)
#        OVERWATCH_CONFIG  optional config path
set -euo pipefail

PY="${OVERWATCH_VENV:-/srv/farmproject/venv}/bin/python"
echo "== Overwatch startup-precondition health-check (#55) =="
if [ -n "${OVERWATCH_CONFIG:-}" ]; then
  exec "$PY" -m overwatch.healthcheck --plain --config "$OVERWATCH_CONFIG"
fi
exec "$PY" -m overwatch.healthcheck --plain
