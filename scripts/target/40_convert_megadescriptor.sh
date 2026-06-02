#!/usr/bin/env bash
# TARGET (Jetson) — convert MegaDescriptor-T-224 (Swin-Tiny) to an FP16 TensorRT
# 8.5 engine and place it under models/. See the trt-model-conversion skill for
# the detailed procedure and TRT 8.5 friction notes.
set -euo pipefail

echo "== Overwatch: convert MegaDescriptor -> TensorRT FP16 =="

OUT="models/megadescriptor_t224_fp16.engine"

HERE="$(cd "$(dirname "$0")" && pwd)"
WORK="${WORK:-$PWD}"                       # run from a writable work dir
ONNX="${WORK}/megadescriptor_t224.onnx"
TRTEXEC="${TRTEXEC:-/usr/src/tensorrt/bin/trtexec}"
# fp32 = correct (cosine ~1.0, ~40ms on Xavier); fp16 = fast (~17ms) but Swin
# overflows FP16 -> garbage embeddings (cosine ~0.13). See trt-model-conversion skill.
PREC="${PREC:-fp32}"
ENGINE="${WORK}/megadescriptor_t224_${PREC}.engine"

# 1. Export to ONNX. CRITICAL: opset 16 (opset 17's fused LayerNormalization is
#    unparseable by TensorRT 8.5; native support is TRT 8.6+).
python3 "${HERE}/reid/export_onnx.py" "$ONNX" 16

# 2. Build the engine.
if [ "$PREC" = "fp16" ]; then
    echo "WARNING: pure FP16 Swin overflows -> validate the cosine below; prefer fp32 for V1."
    "$TRTEXEC" --onnx="$ONNX" --fp16 --saveEngine="$ENGINE" --memPoolSize=workspace:4096
else
    "$TRTEXEC" --onnx="$ONNX" --saveEngine="$ENGINE" --memPoolSize=workspace:4096
fi

# 3. Validate against the torch FP32 reference saved by the export step.
"$TRTEXEC" --loadEngine="$ENGINE" --loadInputs=input:"${WORK}/ref_input.bin" \
    --exportOutput="${WORK}/trt_out.json" --iterations=20 --avgRuns=20
python3 "${HERE}/reid/compare_trt.py" "${WORK}/trt_out.json"

echo "== done: $ENGINE  (expect cosine ~1.0 for fp32; fp16 is lossy for Swin) =="
echo "   copy/symlink to models/megadescriptor_t224_fp16.engine for the runtime loader."
