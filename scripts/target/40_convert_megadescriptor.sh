#!/usr/bin/env bash
# TARGET (Jetson) — convert MegaDescriptor-T-224 (Swin-Tiny) to an FP16 TensorRT
# 8.5 engine and place it under models/. See the trt-model-conversion skill for
# the detailed procedure and TRT 8.5 friction notes.
set -euo pipefail

echo "== Overwatch: convert MegaDescriptor -> TensorRT FP16 =="

OUT="models/megadescriptor_t224_fp16.engine"

# TODO:
#   1. Load MegaDescriptor-T-224 (WildlifeDatasets/wildlife-tools).
#   2. Export to ONNX (watch Swin op support on opset; see skill for gotchas).
#   3. Build TRT engine: trtexec --onnx=... --fp16 --saveEngine="$OUT"
#      (or the Python TRT builder API for finer control).
#   4. Validate the engine loads and produces an embedding of expected dim.

echo "target engine path: $OUT"
echo "== conversion (skeleton — steps TODO; see trt-model-conversion skill) =="
