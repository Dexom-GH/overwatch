#!/usr/bin/env bash
# #56: reproducible on-device TensorRT engine build + manifest.
#
# Builds BOTH V1 engines under models/ from pinned ONNX sources, idempotently,
# and writes a manifest recording model id / opset / precision / TRT version so a
# redeploy rebuilds equivalent engines (no silent drift). TARGET-ONLY (TensorRT
# 8.5 on the Jetson). Engines stay gitignored; this script + the manifest are the
# reproducibility record.
#
#   bash scripts/target/56_build_engines.sh          # build anything missing
#   FORCE=1 bash scripts/target/56_build_engines.sh  # rebuild all engines
#
# Inputs (under models/, produced upstream — NOT by this script):
#   - yolov8_farm.onnx        detector, from #77's best.pt via DeepStream-Yolo
#                             utils/export_yoloV8.py (--opset 12). MUST be the
#                             DeepStream-Yolo output layout ([1, anchors, 6]) so
#                             NvDsInferParseYolo decodes it — a plain Ultralytics
#                             export ([1, 4+nc, anchors]) parses to ZERO dets.
#   - megadescriptor_t224.onnx  ReID, from scripts/target/40_convert_megadescriptor.sh (#7).
#
# Precision: detector FP16; ReID FP32 (ADR-0003 — pure FP16 Swin overflows).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
MODELS="$REPO/models"
TRTEXEC="${TRTEXEC:-/usr/src/tensorrt/bin/trtexec}"
PY="${PY:-/srv/farmproject/venv/bin/python3}"
WS="${WS:-4096}"          # TRT 8.5 workspace pool (MiB); Xavier NX shares ~8 GB
FORCE="${FORCE:-0}"
mkdir -p "$MODELS"

build() {  # name onnx engine extra_flags
    local name="$1" onnx="$2" engine="$3" flags="$4"
    if [ -f "$engine" ] && [ "$FORCE" != "1" ]; then
        echo "[skip] ${name}: $(basename "$engine") exists (FORCE=1 to rebuild)"
        return 0
    fi
    [ -f "$onnx" ] || { echo "[ERR] ${name}: missing ONNX ${onnx}" >&2; return 1; }
    echo "[build] ${name}: $(basename "$onnx") -> $(basename "$engine")"
    # shellcheck disable=SC2086
    "$TRTEXEC" --onnx="$onnx" $flags --saveEngine="$engine" --memPoolSize=workspace:"${WS}" >/dev/null
    echo "[ok] built $(basename "$engine")"
}

build "detector yolov8_farm (fp16)" "$MODELS/yolov8_farm.onnx" \
      "$MODELS/yolov8_farm_fp16.engine" "--fp16"
build "reid megadescriptor_t224 (fp32)" "$MODELS/megadescriptor_t224.onnx" \
      "$MODELS/megadescriptor_t224_fp32.engine" ""

# --- manifest (host-checkable) ---------------------------------------------
opset_of() { "$PY" -c 'import onnx,sys; print(onnx.load(sys.argv[1]).opset_import[0].version)' "$1" 2>/dev/null || echo unknown; }
sha16()    { sha256sum "$1" 2>/dev/null | cut -c1-16; }
TRTV="$(dpkg-query -W -f='${Version}' libnvinfer-bin 2>/dev/null || echo unknown)"
NOW="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
cat > "$MODELS/engine_manifest.json" <<JSON
{
  "tensorrt": "${TRTV}",
  "built_utc": "${NOW}",
  "engines": [
    {"id": "yolov8_farm", "role": "detector", "precision": "fp16",
     "onnx": "yolov8_farm.onnx", "opset": "$(opset_of "$MODELS/yolov8_farm.onnx")",
     "engine": "yolov8_farm_fp16.engine", "sha256_16": "$(sha16 "$MODELS/yolov8_farm_fp16.engine")"},
    {"id": "megadescriptor_t224", "role": "reid", "precision": "fp32",
     "onnx": "megadescriptor_t224.onnx", "opset": "$(opset_of "$MODELS/megadescriptor_t224.onnx")",
     "engine": "megadescriptor_t224_fp32.engine", "sha256_16": "$(sha16 "$MODELS/megadescriptor_t224_fp32.engine")"}
  ]
}
JSON
echo "[manifest] wrote $MODELS/engine_manifest.json"
cat "$MODELS/engine_manifest.json"
