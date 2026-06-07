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
echo "== [1/9] verify environment =="
bash "$HERE/30_verify_env.sh"

# 2. Fetch + check out the release ref (fixes 'device on a stale checkout' drift).
echo "== [2/9] check out release ref v$VERSION =="
git -C "$ROOT" fetch --tags --quiet
git -C "$ROOT" checkout "v$VERSION"

# 3. Refresh the installed package AND declared deps (fixes missing-dep drift,
#    e.g. python-dotenv added by #41). Target-only wheels (pyzed/torch/tensorrt/
#    pyds) come from the provisioning scripts, not here.
echo "== [3/9] refresh package + declared deps =="
"$PIP" install -e "$ROOT"
"$PIP" install -r "$ROOT/requirements.target.txt"

# 4. Stage the operator-console SPA bundle (#124, ADR-0008). The SPA is built in
#    CI and attached to the release as dashboard-dist.tar.gz; the device only
#    SERVES the prebuilt dist/ — no Node / npm is ever installed on-device. The
#    backend (output/dashboard/server.py) serves from web/dist; if the asset is
#    absent (e.g. a build predating the SPA) it serves the JSON API only, so this
#    is non-fatal.
echo "== [4/9] stage operator-console SPA bundle =="
DIST_PARENT="$ROOT/src/overwatch/output/dashboard/web"
DIST_DIR="$DIST_PARENT/dist"
DIST_URL="https://github.com/Dexom-GH/overwatch/releases/download/v$VERSION/dashboard-dist.tar.gz"
if curl -fsSL -o /tmp/overwatch-dashboard-dist.tar.gz "$DIST_URL"; then
  rm -rf "$DIST_DIR"
  mkdir -p "$DIST_PARENT"
  tar -xzf /tmp/overwatch-dashboard-dist.tar.gz -C "$DIST_PARENT"
  rm -f /tmp/overwatch-dashboard-dist.tar.gz
  echo "   staged SPA bundle -> $DIST_DIR (no Node on-device)"
else
  echo "[WARN] no dashboard-dist.tar.gz for v$VERSION — operator console will serve the JSON API only"
fi

# 5. (Re)build TensorRT engines for THIS device (engines are device-specific, not
#    committed). ReID (MegaDescriptor) engine via #7's converter. The detector
#    engine is built from the fine-tuned farm model once #77 lands; the
#    DeepStream-Yolo builder names it model_b1_gpu0_fp16.engine and IGNORES
#    model-engine-file, so point configs at that name to reuse, not rebuild.
if [ "${SKIP_ENGINE_BUILD:-0}" = "1" ]; then
  echo "== [5/9] engine (re)build SKIPPED (SKIP_ENGINE_BUILD=1) =="
else
  echo "== [5/9] (re)build TensorRT engines on-device =="
  bash "$HERE/40_convert_megadescriptor.sh"
fi

# 6. Stage the detector's runtime assets (#97): symlink the DeepStream-Yolo parser
#    .so + labels.txt into the run-dir paths the canonical nvinfer config names, so
#    nvinfer loads the custom bbox parser (else: zero detections). Fatal — a missing
#    parser .so means the pipeline can't detect, so fail the deploy here, not at run.
echo "== [6/9] stage detector assets =="
OVERWATCH_DIR="$ROOT" bash "$HERE/57_stage_detector_assets.sh"

# 7. Install (not enable) the systemd unit. Needs root — run deploy.sh as an
#    operator with sudo. Enabling/starting is gated on #38 (see #81).
echo "== [7/9] install systemd unit (disabled) =="
sudo install -m 644 "$HERE/overwatch.service" /etc/systemd/system/overwatch.service
sudo systemctl daemon-reload
echo "   installed /etc/systemd/system/overwatch.service (NOT enabled — #38/#81)"

# 8. Bounded smoke-check (no live pipeline; PLAYING + Slack delivery is #81).
echo "== [8/9] bounded smoke-check =="
OVERWATCH_VENV="$VENV" bash "$HERE/50_smoke_check.sh"

# 9. Startup-precondition health-check (#55). Non-fatal here: at deploy time the
#    RTSP camera may not be cabled/reachable yet — report preconditions but don't
#    abort the deploy. The same check gates boot/on-demand (exit code honoured by
#    55_healthcheck.sh) and is wired before the service is enabled (#81).
echo "== [9/9] startup-precondition health-check =="
OVERWATCH_VENV="$VENV" bash "$HERE/55_healthcheck.sh" || \
  echo "[WARN] startup preconditions not all met (see above) — resolve before enabling the service (#81)"

echo "== deploy v$VERSION complete (service installed, disabled; enable via #38/#81) =="
