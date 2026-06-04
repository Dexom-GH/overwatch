---
name: deepstream-import-vision-model
description: >
  Use when bringing a NEW object-detection model (e.g. YOLOv8 for #5/#77, or any
  HuggingFace/NGC detector) into the Overwatch DeepStream nvinfer pipeline on the
  Jetson — covers ONNX export at the TRT-8.5-safe opset, the FP16 TRT engine build,
  the custom nvinfer bbox parser + Makefile + labels.txt, single-stream validation,
  and a KITTI detection sanity gate. Detection models only. Not for the MegaDescriptor
  ReID classifier (that is trt-model-conversion) and not for graph/probe wiring
  (that is deepstream-pipeline).
license: CC-BY-4.0 AND Apache-2.0
metadata:
  author: NVIDIA CORPORATION (upstream) — adapted for Overwatch
  upstream: https://github.com/NVIDIA/skills/tree/main/skills/deepstream-import-vision-model
  upstream_version: 1.2.1
---

# DeepStream Import Vision Model (Overwatch fork)

This is a **vendored, adapted fork** of NVIDIA's `deepstream-import-vision-model`
skill. The upstream skill targets **DeepStream 9.0 / TensorRT 10 / Ubuntu 24.04**,
mostly on **datacenter GPUs**. Overwatch targets the opposite end: **Jetson Xavier NX,
JetPack 5.1.x, Ubuntu 20.04, Python 3.8, TensorRT 8.5, DeepStream 6.x, a single ZED
camera**. The upstream commands therefore **do not run as-is** here.

> **READ FIRST: [OVERWATCH-ADAPTATION.md](OVERWATCH-ADAPTATION.md).** It is the
> delta table between upstream and our stack — opset, workspace size, batch sizing,
> venv, encoder, and what to drop on an edge device. Every command you copy from a
> `references/*.md` doc must be filtered through it. The reference docs are vendored
> **verbatim from upstream** (DS9/TRT10 assumptions intact) for depth and provenance;
> they are NOT corrected in place.

## What this skill is for (and what it is NOT)

- **For:** importing a *new object-detection model* into our `nvinfer` path —
  acquire/export ONNX → build an FP16 TRT engine → write the custom bbox parser,
  Makefile, and `labels.txt` → validate detections. This is the front half of
  issues like **#5** (YOLOv8 detect) and **#77** (its TRT engine).
- **NOT for the ReID classifier.** The MegaDescriptor Swin→TRT FP16 path has its own
  skill — use **`trt-model-conversion`**. Its on-demand dispatch is ADR-0003.
- **NOT for pipeline wiring.** The decode→nvinfer→nvtracker graph, the probe-callback
  pattern, and the ZED-depth fusion seam are owned by **`deepstream-pipeline`**.
  This skill produces the *engine + parser + nvinfer config* that pipeline consumes.
- **Detection only.** Reject classification/segmentation/depth architectures by the
  `architectures` suffix in `config.json` (see references/model-acquire.md). Embeddings
  models (ReID) are out of scope here by definition.

## The hard constraints that differ from upstream (summary)

These are the traps that will silently waste a device session. Full detail and the
"why" live in [OVERWATCH-ADAPTATION.md](OVERWATCH-ADAPTATION.md).

| Topic | Upstream | **Overwatch / Xavier NX** |
|-------|----------|---------------------------|
| ONNX opset | 17/18, prefer dynamo exporter | **`dynamo=False, opset_version=12`** — TRT 8.5 rejects opset ≥ 17. Proven in #76. → memory `yolov8-onnx-export-for-trt85` |
| TRT / trtexec | TensorRT 10.x | **TensorRT 8.5**; flags differ (`--workspace` vs `--memPoolSize`, see below) |
| Workspace | `--memPoolSize=workspace:32768M` (32 GB) | Xavier NX shares **~8 GB unified memory** — use **2048–4096 MiB**. On TRT 8.5 the flag is often `--workspace=4096` (MiB) |
| `MAX_BS` / streams | start 64, double to 256+ | **1 ZED camera → batch-size 1.** The whole PEAK_GPU_STREAMS multi-stream sweep is moot here; build a `b1` (or small) engine |
| Python venv | `build/.venv_optimum`, `pip install torch optimum` | **Never pip `torch` on host or device.** ONNX *export* runs on the **host** Python (3.12, see memory); TRT *build* runs **on-device** in the shared venv `/srv/farmproject/venv`. → memory `jetson-device-access`, `host-python-interpreter` |
| Encoder | `nvv4l2h264enc` primary, theora fallback | `nvv4l2h264enc` **is** the Jetson encoder — the upstream primary path is correct here; keep it |
| PDF report stack | wkhtmltopdf + pandoc + mermaid/puppeteer, 5 charts | **Heavyweight; do not install on the Jetson.** Optional, host-side only. The benchmark/report half is not the V1 deliverable |
| Parser zero-init | `NvDsInferObjectDetectionInfo obj = {};` for DS 9.0 OBB `rotation_angle` | Still zero-init (good hygiene), but DS 6.x has **no `rotation_angle`** — the OBB rationale does not apply |

## Workflow (adapted)

Run on the **host** for steps that touch ONNX/Python, on the **target** (`jetson-agent`)
for steps that touch `trtexec`/`nvinfer`/DeepStream. Engines are **not portable across
TRT versions** — build the engine on-device with the same `libnvinfer` DeepStream uses.

| Step | Phase | Reference (upstream, filter via adaptation doc) | Overwatch note |
|------|-------|--------|----------------|
| 1–3 | Acquire / export ONNX | [references/model-acquire.md](references/model-acquire.md) | Export on **host**; force `dynamo=False, opset_version=12`. Verify opset with `scripts/model/inspect-onnx.py`. |
| 4–5 | Build TRT engine | [references/engine-build.md](references/engine-build.md) | Build on **target**. Drop workspace to ≤4096 MiB; `MAX_BS=1`; skip the doubling/PEAK loop. |
| 6 | Parser + nvinfer config | [references/pipeline-run.md](references/pipeline-run.md) | Most-portable section. `network-type=0`, correct `net-scale-factor`, `cluster-mode`. Hand the resulting `nvinfer` config to **deepstream-pipeline**. |
| 6g | KITTI detection sanity gate | [references/pipeline-run.md](references/pipeline-run.md) | Keep this — cheap, catches wrong `net-scale-factor`/parser. Do NOT gate on the multi-stream perf runs. |
| 7–8 | Multi-stream benchmark + PDF | [references/pipeline-run.md](references/pipeline-run.md), [references/report-generation.md](references/report-generation.md) | **Largely skip on edge.** One camera ≠ a stream sweep. If you need perf numbers, that belongs with the ADR-0001 benchmark gate (#8), not here. |

## The genuinely portable, verified-safe pieces

These transfer to our stack with no version risk:

- **`scripts/model/inspect-onnx.py`** — prints opset, IR version, input/output shapes,
  operators, validity, and a machine-parseable `input_name/height/width` summary.
  **Use it as the opset-12 gate**: if it prints `Opset: 17` (or higher), the export
  was wrong for TRT 8.5 — re-export before wasting a device build. (Apache-2.0, verbatim.)
- **`scripts/model/make-static-batch-onnx.py`** — bakes a static batch dim into a
  batch-1 ONNX and patches `Reshape` nodes. Useful because we want a small fixed batch,
  not a dynamic one. (Apache-2.0, verbatim.)
- **The parser guidance in `references/pipeline-run.md`** — `network-type=0` (not the
  legacy `model-type`), the `net-scale-factor` family table (wrong factor = zero
  detections), `cluster-mode` (2 = DS NMS, 4 = fused TRT NMS), coordinate clipping, and
  the YOLO/DETR/SSD output-decode patterns. The `NvDsInferObjectDetectionInfo` struct
  and `nvdsinfer_custom_impl.h` exist on DS 6.x too.

## Critical rules (kept from upstream, still true here)

1. **`network-type=0`** in the nvinfer config, or the custom `parse-bbox-func-name`
   is never invoked → silent zero detections.
2. **Verify `net-scale-factor` from real ONNX-Runtime output ranges** before writing
   the parser. Wrong scale = zero detections. The KITTI dump (6g) is the gate.
3. **Build the engine on the device**, with the TRT version DeepStream links. Engines
   are not portable across TRT versions (host-built → runtime 0% GPU / stuck pipeline).
4. **Zero-init the parser struct**: `NvDsInferObjectDetectionInfo obj = {};` — cheap
   hygiene even though DS 6.x lacks the OBB `rotation_angle` field.
5. **Object detection only** — reject other architectures from `config.json` first.

## Quick error reference (Overwatch-relevant subset)

| Error | Fix |
|-------|-----|
| `inspect-onnx.py` prints `Opset: 17`/`18` | Re-export with `dynamo=False, opset_version=12` (TRT 8.5). → memory `yolov8-onnx-export-for-trt85` |
| TRT build OOM / "insufficient workspace" on Xavier | Lower workspace to 2048–4096 MiB — you do NOT have 32 GB. Check TRT-8.5 flag name (`--workspace` vs `--memPoolSize`). |
| Engine rebuilds every run / 0% GPU at runtime | Engine built with a different TRT version than DeepStream links, or `model-engine-file` path wrong. Rebuild on-device. |
| Zero detections, parser never logs | `network-type` not `0`, or wrong `net-scale-factor`. |
| `setDimensions` negative dims | Add `infer-dims=3;H;W` for dynamic ONNX. |
| `No module named 'pyservicemaker'` | Upstream-only (DS 9.0 API). Overwatch uses **pyds** — ignore; see deepstream-pipeline. |

## Provenance & license

Forked from NVIDIA `deepstream-import-vision-model` v1.2.1
(`https://github.com/NVIDIA/skills`), license **CC-BY-4.0 AND Apache-2.0**. The
`references/*.md` docs and `scripts/model/*.py` are vendored **verbatim**; the Python
scripts carry their original Apache-2.0 SPDX headers. See [NOTICE](NOTICE). The
upstream `skill.oms.sig` signature, `evals/`, and the report/benchmark scripts
(`scripts/deepstream/`, `scripts/engine/`, `scripts/report/`) were **not** vendored —
the signature would be invalidated by adaptation, and the report/sweep machinery is
datacenter-oriented and not part of the V1 edge deliverable. Pull them from upstream
if you ever need them.
