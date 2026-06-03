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

## Spike findings — MegaDescriptor-T-224 on TRT 8.5 (#7, 2026-06-02, Xavier NX)

Validated end-to-end on-device (L4T 35.6 / TRT 8.5.2 / torch 2.1 nv). The
conversion **works**, with two gotchas:

- **Export at opset 16, NOT 17.** Opset 17 emits the fused `LayerNormalization`
  op, which the TRT 8.5 ONNX parser cannot import ("No importer registered for
  op: LayerNormalization ... Plugin not found") — native support is TRT 8.6+.
  Opset 16 decomposes it into primitives TRT 8.5 parses. Swin's `roll`/window
  shift did **not** trip TRT (it decomposes to Slice/Concat).
- **Pure FP16 is numerically broken for this model.** FP16 engine vs torch FP32:
  cosine **0.13** (near-orthogonal — Swin attention/LayerNorm overflows FP16's
  65504 range). The **FP32 engine is exact (cosine 1.00)**.
  - V1: **use the FP32 engine.** Xavier NX latency (DVFS, clocks not pinned):
    `MODE_15W_4CORE` → FP32 **~27.7 ms** (median 27.6, p99 35), FP16 **~10.3 ms**
    (median 10.0, p99 16); `MODE_10W_4CORE` → FP32 ~40.6 ms / FP16 ~16.7 ms. The
    p99 spikes are **over-current throttle** events (the device caps clocks to hold
    the 15W current budget — see `docs/HARDWARE.md`). For on-demand ReID (ADR-0003,
    off the streaming thread), well within budget. A cold (idle-GPU) call also pays
    a DVFS ramp.
  - Optimization (defer to slice #17): mixed precision — keep overflow-prone
    layers FP32 (`--precisionConstraints=obey --layerPrecisions=...`) to recover
    FP16 speed without the accuracy loss; re-validate cosine.

Embedding dim **768** (Swin-T). Engine sizes: FP32 109 MB, FP16 55 MB. Build
~3–6 min on Xavier.

**Toolchain friction (for provisioning):**
- The NVIDIA Jetson torch wheel needs `libopenblas.so.0` → `sudo apt install
  libopenblas0` (or stage the `.deb` + `LD_LIBRARY_PATH`).
- `pip install timm` silently replaces the CUDA torch with a CPU PyPI torch (the
  nv `2.1.0a0` pre-release fails timm's `torch>=` pin) → install ML deps with
  `--no-deps` and pin the nv torch.
- `timm` requires a **matching** `torchvision` (0.16.x for torch 2.1; a Jetson
  cp38 wheel NVIDIA does not host on its redist).

Reproduce: `scripts/target/40_convert_megadescriptor.sh` → `scripts/target/reid/
export_onnx.py` + `compare_trt.py`.
