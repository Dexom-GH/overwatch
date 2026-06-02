---
name: trt-model-conversion
description: Use when converting a PyTorch model to a TensorRT FP16 engine on the Jetson for Overwatch — especially MegaDescriptor-T-224 (Swin-Tiny) for on-demand ReID. Covers the Swin->ONNX->TRT 8.5 path, FP16 build, validation, and known conversion friction.
---

# TensorRT model conversion (Swin / MegaDescriptor)

Convert a PyTorch model to an **FP16 TensorRT 8.5 engine** that runs on the
target. Primary use: **MegaDescriptor-T-224** (Swin-Tiny, ~28M params, from
`WildlifeDatasets/wildlife-tools`) for on-demand ReID (ADR-0003). Runs on the
device — TensorRT is target-only.

## Pipeline

```
PyTorch model  ->  ONNX export  ->  TensorRT FP16 engine  ->  validate  ->  models/
```

Driver script: `scripts/target/40_convert_megadescriptor.sh`. Output engine:
`models/megadescriptor_t224_fp16.engine` (gitignored — engines are device/
version-specific; rebuild, don't commit).

## Steps

1. **Load the model** (`wildlife-tools` MegaDescriptor-T-224), set `.eval()`.
2. **Export to ONNX.** Use a fixed input shape (224x224) and a recent opset
   (try 16/17). Verify with `onnx.checker` and `onnxruntime` if available.
3. **Build the engine** — `trtexec --onnx=model.onnx --fp16
   --saveEngine=models/megadescriptor_t224_fp16.engine` (or the Python TRT
   builder API for explicit profiles / calibration control).
4. **Validate** — load the engine, run a known crop, confirm the embedding has
   the expected dimensionality and FP16 vs FP32 cosine similarity is high
   (sanity, not bit-exact).

## Swin -> TensorRT 8.5 friction (expect these)

- **Unsupported / awkward ops.** Swin's `roll`, window partition/reshape, and
  certain `LayerNorm`/`einsum` patterns can trip ONNX export or TRT 8.5 parsing.
  Mitigations: bump/lower the opset; simplify with `onnx-simplifier`; if a node
  is unsupported, refactor that op in the export wrapper.
- **Dynamic shapes.** Prefer a **fixed** input profile for V1 (single 224x224
  crop) to avoid optimization-profile complexity on TRT 8.5.
- **FP16 numerics.** LayerNorm-heavy transformers can lose precision in FP16;
  validate similarity, and if a layer is unstable consider keeping it FP32
  (mixed precision) via builder flags.
- **Build time / memory.** Engine builds are slow on Xavier NX; build once,
  cache the engine under `models/`.

## Where this plugs in

The resulting engine is loaded by
`src/overwatch/inference/reid/megadescriptor.py` and fired on-demand from a
DeepStream probe (see the `deepstream-pipeline` skill and ADR-0003). It must be
callable off the streaming thread.

## Verify

- The engine builds and `scripts/target/30_verify_env.sh` confirms TensorRT 8.5.
- A validation run produces an embedding of expected dim with high FP16/FP32
  similarity. Record latency via the `model-convert-benchmark` workflow.
