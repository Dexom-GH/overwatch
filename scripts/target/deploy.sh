#!/usr/bin/env bash
# TARGET (Jetson) — deploy a released version of Overwatch to the device (#43).
# GATED / MANUAL: run by hand on (or against) the Jetson, never by CI — GitHub
# runners can't reach the device. Requires an explicit version arg and a typed
# confirmation, so it can't fire by accident. See docs/RELEASING.md.
#
#   Usage:  bash scripts/target/deploy.sh <version>        # e.g. 2026.6.0
#   Env:    OVERWATCH_DIR   checkout dir   (default: repo root inferred from here)
#           OVERWATCH_VENV  venv dir       (default: /srv/farmproject/venv)
#           SKIP_ENGINE_BUILD=1            skip the on-device TRT engine (re)build
#
# Idempotent / re-runnable. The systemd unit is INSTALLED BUT NOT ENABLED/STARTED:
# app.py only runs the full pipeline once #38 wires Inference/Fusion/Output stages
# into the Supervisor; enabling + the live PLAYING/Slack smoke-check is #81.
set -euo pipefail

VERSION="${1:-}"
if [ -z "$VERSION" ]; then
  echo "usage: bash scripts/target/deploy.sh <version>   (e.g. 2026.6.0)"
  exit 2
fi

echo "== Overwatch deploy to Jetson: v$VERSION =="
echo "This updates the on-device install, rebuilds engines, and installs the service unit."
echo "Type the version to confirm:"
read -r CONFIRM
if [ "$CONFIRM" != "$VERSION" ]; then
  echo "confirmation mismatch — aborting."
  exit 1
fi

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="${OVERWATCH_DIR:-$(cd "$HERE/../.." && pwd)}"
VENV="${OVERWATCH_VENV:-/srv/farmproject/venv}"
PIP="$VENV/bin/pip"

echo "-- deploy dir: $ROOT"
echo "-- venv:       $VENV"

# 1. Verify the provisioned env matches the build-order / pins.
echo "== [1/6] verify environment =="
bash "$HERE/30_verify_env.sh"

# 2. Fetch + check out the release ref (fixes 'device on a stale checkout' drift).
echo "== [2/6] check out release ref v$VERSION =="
git -C "$ROOT" fetch --tags --quiet
git -C "$ROOT" checkout "v$VERSION"

# 3. Refresh the installed package AND declared deps (fixes missing-dep drift,
#    e.g. python-dotenv added by #41). Target-only wheels (pyzed/torch/tensorrt/
#    pyds) come from the provisioning scripts, not here.
echo "== [3/6] refresh package + declared deps =="
"$PIP" install -e "$ROOT"
"$PIP" install -r "$ROOT/requirements.target.txt"

# 4. (Re)build TensorRT engines for THIS device (engines are device-specific, not
#    committed). ReID (MegaDescriptor) engine via #7's converter. The detector
#    engine is built from the fine-tuned farm model once #77 lands; the
#    DeepStream-Yolo builder names it model_b1_gpu0_fp16.engine and IGNORES
#    model-engine-file, so point configs at that name to reuse, not rebuild.
if [ "${SKIP_ENGINE_BUILD:-0}" = "1" ]; then
  echo "== [4/6] engine (re)build SKIPPED (SKIP_ENGINE_BUILD=1) =="
else
  echo "== [4/6] (re)build TensorRT engines on-device =="
  bash "$HERE/40_convert_megadescriptor.sh"
fi

# 5. Install (not enable) the systemd unit. Needs root — run deploy.sh as an
#    operator with sudo. Enabling/starting is gated on #38 (see #81).
echo "== [5/6] install systemd unit (disabled) =="
sudo install -m 644 "$HERE/overwatch.service" /etc/systemd/system/overwatch.service
sudo systemctl daemon-reload
echo "   installed /etc/systemd/system/overwatch.service (NOT enabled — #38/#81)"

# 6. Bounded smoke-check (no live pipeline; PLAYING + Slack delivery is #81).
echo "== [6/6] bounded smoke-check =="
OVERWATCH_VENV="$VENV" bash "$HERE/50_smoke_check.sh"

echo "== deploy v$VERSION complete (service installed, disabled; enable via #38/#81) =="
