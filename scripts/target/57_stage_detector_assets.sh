#!/usr/bin/env bash
# TARGET (Jetson) — stage the detector's runtime assets where the canonical
# nvinfer config resolves them, on a fresh deploy (#97).
#
# WHY. gst-nvinfer resolves the asset paths in its config file **relative to the
# config file's own directory** (verified on-device, #84) — NOT the process CWD.
# src/overwatch/inference/deepstream/configs/nvinfer_detector.txt names:
#   custom-lib-path=models/DeepStream-Yolo/nvdsinfer_custom_impl_Yolo/libnvdsinfer_custom_impl_Yolo.so
#   model-engine-file=models/yolov8_farm_fp16.engine
#   labelfile-path=labels.txt
# so nvinfer looks under <config-dir>/models/... and <config-dir>/labels.txt. But
# the engine is built under the repo-root models/ (56_build_engines.sh) and the
# DeepStream-Yolo bbox parser .so is built under the #76 yolo-spike tree — so the
# config does NOT resolve as-is and nvinfer fails to load the custom parser /
# engine (→ zero detections). #49 hit this; #84 pinned the cause to config-relative
# resolution.
#
# FIX (two symlinks, no config edit, no Python change):
#   1. symlink the parser .so into the repo-root models/ (so models/ holds every
#      asset the config names: parser + engine + onnx);
#   2. symlink <config-dir>/models -> repo-root models/, so nvinfer's config-
#      relative `models/...` lookups resolve onto the real assets. labels.txt is
#      committed in the config dir, so labelfile-path already resolves.
# Both links are gitignored; a re-deploy just refreshes them.
#
#   bash scripts/target/57_stage_detector_assets.sh
#   PARSER_SO=/path/to/libnvdsinfer_custom_impl_Yolo.so bash scripts/target/57_stage_detector_assets.sh
#
#   Env: OVERWATCH_DIR  checkout/run dir (default: repo root inferred from here)
#        PARSER_SO      DeepStream-Yolo parser .so (default: the #76 yolo-spike build)
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="${OVERWATCH_DIR:-$(cd "$HERE/../.." && pwd)}"

# Source of the bbox parser .so — built by #76 under the yolo-spike DeepStream-Yolo
# tree (override PARSER_SO if it lives elsewhere on a given device).
PARSER_SO="${PARSER_SO:-/srv/farmproject/yolo-spike/DeepStream-Yolo/nvdsinfer_custom_impl_Yolo/libnvdsinfer_custom_impl_Yolo.so}"

MODELS="$REPO/models"
PARSER_LINK="$MODELS/DeepStream-Yolo/nvdsinfer_custom_impl_Yolo/libnvdsinfer_custom_impl_Yolo.so"
CONFIG_DIR="$REPO/src/overwatch/inference/deepstream/configs"
CONFIG_MODELS="$CONFIG_DIR/models"     # -> $MODELS, so nvinfer's config-relative models/ resolves
LABELS="$CONFIG_DIR/labels.txt"

echo "== stage detector assets (#97) =="
echo "-- config dir: $CONFIG_DIR"
echo "-- models dir: $MODELS"

fail=0

# 1. Parser .so into the repo-root models/ tree (alongside the engine).
if [ ! -f "$PARSER_SO" ]; then
  echo "[FAIL] parser .so not found: $PARSER_SO" >&2
  echo "       build it via #76 (DeepStream-Yolo nvdsinfer_custom_impl_Yolo) or set PARSER_SO." >&2
  fail=1
else
  mkdir -p "$(dirname "$PARSER_LINK")"
  ln -sfn "$PARSER_SO" "$PARSER_LINK"
  echo "[OK]   parser .so   -> $PARSER_LINK"
  echo "                     -> $PARSER_SO"
fi

# 2. config-dir/models -> repo-root models/, so nvinfer's config-relative `models/...`
#    (custom-lib-path, model-engine-file, onnx-file) resolves onto the real assets.
#    Replace any stale real dir / wrong link first so the result is exactly the link.
if [ -L "$CONFIG_MODELS" ] || [ -e "$CONFIG_MODELS" ]; then
  rm -rf "$CONFIG_MODELS"
fi
ln -s "$MODELS" "$CONFIG_MODELS"
echo "[OK]   config models -> $CONFIG_MODELS -> $MODELS"

# 3. labels.txt is committed in the config dir; nvinfer resolves labelfile-path
#    (=labels.txt) config-relative onto it. Just confirm it is present.
if [ ! -f "$LABELS" ]; then
  echo "[FAIL] labels.txt not found: $LABELS" >&2
  echo "       generate it from configs/animals.yaml via overwatch.inference.labels." >&2
  fail=1
else
  echo "[OK]   labelfile     -> $LABELS"
fi

if [ "$fail" != "0" ]; then
  echo "== detector-asset staging INCOMPLETE — resolve the above before enabling the service (#81) ==" >&2
  exit 1
fi

echo "== detector assets staged — nvinfer config resolves config-relative =="
