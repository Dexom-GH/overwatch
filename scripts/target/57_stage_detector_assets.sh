#!/usr/bin/env bash
# TARGET (Jetson) — stage the detector's runtime assets where the canonical
# nvinfer config resolves them, on a fresh deploy (#97).
#
# WHY. src/overwatch/inference/deepstream/configs/nvinfer_detector.txt names its
# assets RUN-DIR-relative (the systemd unit runs with WorkingDirectory =
# /srv/farmproject/overwatch, the repo root — see overwatch.service):
#   custom-lib-path=models/DeepStream-Yolo/nvdsinfer_custom_impl_Yolo/libnvdsinfer_custom_impl_Yolo.so
#   labelfile-path=labels.txt
# But on this device the DeepStream-Yolo bbox parser .so is built under the #76
# yolo-spike tree, and labels.txt lives in the package's configs/ dir — so the
# config does NOT resolve as-is and nvinfer fails to load the custom parser
# (→ zero detections). #49 hit exactly this (it only worked with a hand-made
# absolute-path config). This script stages both assets at the run-dir paths the
# committed config names, so the canonical config works unmodified.
#
# It does NOT edit the committed config (that would dirty the deploy's checkout)
# and it changes no Python. The parser .so + labels.txt are symlinked (not copied)
# into the gitignored run-dir locations, so a re-deploy just refreshes the links.
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

# Run-dir targets the committed nvinfer config resolves against (CWD = $REPO).
PARSER_LINK="$REPO/models/DeepStream-Yolo/nvdsinfer_custom_impl_Yolo/libnvdsinfer_custom_impl_Yolo.so"
LABELS_SRC="$REPO/src/overwatch/inference/deepstream/configs/labels.txt"
LABELS_LINK="$REPO/labels.txt"

echo "== stage detector assets (#97) =="
echo "-- run dir: $REPO"

fail=0

# 1. Parser .so -> models/DeepStream-Yolo/... (matches custom-lib-path).
if [ ! -f "$PARSER_SO" ]; then
  echo "[FAIL] parser .so not found: $PARSER_SO" >&2
  echo "       build it via #76 (DeepStream-Yolo nvdsinfer_custom_impl_Yolo) or set PARSER_SO." >&2
  fail=1
else
  mkdir -p "$(dirname "$PARSER_LINK")"
  ln -sfn "$PARSER_SO" "$PARSER_LINK"
  echo "[OK]   custom-lib-path -> $PARSER_LINK"
  echo "         -> $PARSER_SO"
fi

# 2. labels.txt -> run-dir labels.txt (matches labelfile-path; the Python label
#    loader keeps reading the configs/ copy config-relative, unaffected).
if [ ! -f "$LABELS_SRC" ]; then
  echo "[FAIL] labels.txt not found: $LABELS_SRC" >&2
  echo "       generate it from configs/animals.yaml via overwatch.inference.labels." >&2
  fail=1
else
  ln -sfn "$LABELS_SRC" "$LABELS_LINK"
  echo "[OK]   labelfile-path -> $LABELS_LINK"
  echo "         -> $LABELS_SRC"
fi

if [ "$fail" != "0" ]; then
  echo "== detector-asset staging INCOMPLETE — resolve the above before enabling the service (#81) ==" >&2
  exit 1
fi

echo "== detector assets staged (parser .so + labels.txt) — nvinfer config resolves from the run dir =="
