#!/usr/bin/env bash
# TARGET (Jetson) — one-command #84 RTSP demo (#58). Brings the supervised systemd
# service up against a LIVE RTSP camera or a RECORDED CLIP and drives a real Slack
# alert, so the milestone is repeatable for stakeholders. Companion runbook:
# docs/DEMO_RTSP.md.
#
# The live-vs-clip knob is just the first capture source's URL: an `rtsp://...`
# camera or a `file://...` clip — both decode through the same DeepStream graph
# (nvurisrcbin), so the clip demo exercises the identical pipeline with no camera.
#
# Privileged steps (writing $DEMO_DIR under /etc, `systemctl restart`) need sudo.
# On a NON-SUDO login use --dry-run: it renders + validates the demo config and
# prints exactly the privileged actions it would take, touching nothing.
#
#   Usage:
#     bash scripts/target/demo_rtsp.sh --mode live --url rtsp://CAM/stream1
#     bash scripts/target/demo_rtsp.sh --mode clip --clip /srv/farmproject/clips/demo.mp4
#     bash scripts/target/demo_rtsp.sh --mode clip --clip ./demo.mp4 --dry-run
#
#   Env: OVERWATCH_VENV (default /srv/farmproject/venv)
#        OVERWATCH_DIR  (default: repo root inferred from this script)
#        OVERWATCH_DEMO_DIR (default /etc/overwatch/demo) — rendered demo config dir
#        OVERWATCH_ENV_FILE (default /etc/overwatch/overwatch.env) — holds SLACK_WEBHOOK
set -euo pipefail

SERVICE="overwatch"
VENV="${OVERWATCH_VENV:-/srv/farmproject/venv}"
PY="$VENV/bin/python"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="${OVERWATCH_DIR:-$(cd "$HERE/../.." && pwd)}"
DEMO_DIR="${OVERWATCH_DEMO_DIR:-/etc/overwatch/demo}"
ENV_FILE="${OVERWATCH_ENV_FILE:-/etc/overwatch/overwatch.env}"

MODE=""; URL=""; CLIP=""; DRYRUN=0; TIMEOUT=60; SOURCE_ID="cam-0"; FPS=15

usage() {
  sed -n '2,29p' "$0"
  exit "${1:-0}"
}

while [ $# -gt 0 ]; do
  case "$1" in
    --mode) MODE="${2:-}"; shift 2 ;;
    --url) URL="${2:-}"; shift 2 ;;
    --clip) CLIP="${2:-}"; shift 2 ;;
    --service) SERVICE="${2:-}"; shift 2 ;;
    --timeout) TIMEOUT="${2:-}"; shift 2 ;;
    --source-id) SOURCE_ID="${2:-}"; shift 2 ;;
    --fps) FPS="${2:-}"; shift 2 ;;
    --dry-run) DRYRUN=1; shift ;;
    -h|--help) usage 0 ;;
    *) echo "unknown arg: $1" >&2; usage 2 ;;
  esac
done

# --- resolve the source URL from the mode -----------------------------------
case "$MODE" in
  live)
    [ -n "$URL" ] || { echo "ERROR: --mode live needs --url rtsp://..." >&2; exit 2; }
    SRC_URL="$URL"
    ;;
  clip)
    [ -n "$CLIP" ] || { echo "ERROR: --mode clip needs --clip <video file>" >&2; exit 2; }
    if [ ! -f "$CLIP" ]; then echo "ERROR: clip not found: $CLIP" >&2; exit 2; fi
    ABS_CLIP="$(cd "$(dirname "$CLIP")" && pwd)/$(basename "$CLIP")"
    SRC_URL="file://$ABS_CLIP"
    ;;
  *)
    echo "ERROR: --mode must be 'live' or 'clip'" >&2; usage 2 ;;
esac

echo "== Overwatch #84 RTSP demo ($MODE) =="
echo "-- source:   $SRC_URL"
echo "-- service:  $SERVICE"
echo "-- demo cfg: $DEMO_DIR"
[ "$DRYRUN" = "1" ] && echo "-- DRY RUN: render + validate only; no /etc writes, no systemctl"

# --- preconditions ----------------------------------------------------------
echo "-- checking preconditions..."
"$PY" -c "import overwatch" 2>/dev/null \
  || { echo "FAIL: overwatch not importable in $VENV (provision: jetson-env-setup)"; exit 1; }

# SLACK_WEBHOOK must reach the service env (never printed). Only checkable if the
# env file is readable to us; otherwise just remind.
if [ -r "$ENV_FILE" ]; then
  grep -q '^SLACK_WEBHOOK=' "$ENV_FILE" \
    || { echo "FAIL: SLACK_WEBHOOK not set in $ENV_FILE (#41)"; exit 1; }
  echo "   [OK] SLACK_WEBHOOK present in $ENV_FILE"
else
  echo "   [warn] $ENV_FILE not readable here — ensure SLACK_WEBHOOK is set for the service (#41)"
fi

# --- render the demo config dir (copy configs/, rewrite sources[0]) ----------
if [ "$DRYRUN" = "1" ]; then
  RENDER_DIR="$(mktemp -d)"
  trap 'rm -rf "$RENDER_DIR"' EXIT
else
  RENDER_DIR="$DEMO_DIR"
  mkdir -p "$RENDER_DIR"   # privileged under /etc — needs sudo
fi
cp -r "$ROOT/configs/." "$RENDER_DIR/"

# Rewrite the single capture source to the chosen URL and validate the merged
# config loads (schema + secret rules) before we point the service at it.
OW_RENDER_DIR="$RENDER_DIR" OW_SRC_URL="$SRC_URL" OW_SRC_ID="$SOURCE_ID" OW_FPS="$FPS" "$PY" - <<'PY'
import os
import yaml

render_dir = os.environ["OW_RENDER_DIR"]
path = os.path.join(render_dir, "default.yaml")
with open(path) as fh:
    data = yaml.safe_load(fh)

# One mono source (ADR-0006); DeepStream decodes this URL via nvurisrcbin.
# Replace the whole `capture` block so the legacy scalar form ({source,source_id,
# fps}) shipped in default.yaml is dropped — leaving both forms is rejected.
data["capture"] = {"sources": [{
    "type": "rtsp",
    "source_id": os.environ["OW_SRC_ID"],
    "url": os.environ["OW_SRC_URL"],
    "fps": int(os.environ["OW_FPS"]),
}]}
with open(path, "w") as fh:
    yaml.safe_dump(data, fh, sort_keys=False)

# Validate the way the service will load it ($OVERWATCH_CONFIG_DIR/default.yaml).
os.environ["OVERWATCH_CONFIG_DIR"] = render_dir
from overwatch.config.loader import load_config
cfg = load_config()
src = cfg.capture.sources[0]
print("   [OK] demo config validates (source={} url-set, bus={})".format(src.type, cfg.bus.transport))
PY

if [ "$DRYRUN" = "1" ]; then
  echo ""
  echo "DRY RUN complete. To run for real (needs sudo), this would:"
  echo "  1. render the demo config into $DEMO_DIR (as above)"
  echo "  2. point the service at it:  OVERWATCH_CONFIG_DIR=$DEMO_DIR  (systemd drop-in)"
  echo "  3. sudo systemctl restart $SERVICE"
  echo "  4. wait for active + watch the journal for errors, then confirm the Slack post"
  exit 0
fi

# --- point the service at the demo config (systemd drop-in) -----------------
DROPIN_DIR="/etc/systemd/system/${SERVICE}.service.d"
mkdir -p "$DROPIN_DIR"
cat > "$DROPIN_DIR/10-demo.conf" <<EOF
[Service]
Environment=OVERWATCH_CONFIG_DIR=$DEMO_DIR
EOF
systemctl daemon-reload

# --- (re)start + verify liveness --------------------------------------------
START_TS="$(date '+%Y-%m-%d %H:%M:%S')"
echo "-- restarting $SERVICE ..."
systemctl restart "$SERVICE"

deadline=$(( $(date +%s) + TIMEOUT ))
while [ "$(date +%s)" -lt "$deadline" ]; do
  if systemctl is-active --quiet "$SERVICE"; then break; fi
  sleep 1
done
if ! systemctl is-active --quiet "$SERVICE"; then
  echo "FAIL: $SERVICE did not reach active within ${TIMEOUT}s. Recent log:"
  journalctl -u "$SERVICE" --since "$START_TS" --no-pager | tail -30
  exit 1
fi
echo "   [OK] $SERVICE is active"

# Fail fast on an obvious startup error (missing secret, source 401, engine miss).
if journalctl -u "$SERVICE" --since "$START_TS" --no-pager | grep -Eiq "Traceback|ERROR|Missing required secret"; then
  echo "WARN: errors in the service log since start — inspect:"
  journalctl -u "$SERVICE" --since "$START_TS" --no-pager | grep -Ei "Traceback|ERROR|Missing required secret" | tail -10
fi

echo ""
echo "== demo is up =="
echo "Now confirm the demo artifact:"
echo "  - the pipeline is detecting/tracking (journalctl -u $SERVICE -f)"
echo "  - a threshold crossing posts a real Slack alert (watch the channel; <=5s e2e)"
echo "  - to restore normal config: rm $DROPIN_DIR/10-demo.conf && systemctl daemon-reload && systemctl restart $SERVICE"
