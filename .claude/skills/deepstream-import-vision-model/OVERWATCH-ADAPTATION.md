# Overwatch adaptation notes

This skill is a **verbatim vendored fork** of NVIDIA's `deepstream-import-vision-model`
(v1.2.1). The upstream `references/*.md` were written for **DeepStream 9.0 / TensorRT 10
/ Ubuntu 24.04**, mostly on **datacenter GPUs (H100, RTX 6000)**. Overwatch runs on a
**Jetson Xavier NX**. This doc is the translation layer. **Apply it to every command you
copy out of a reference doc.** The reference docs are intentionally left uncorrected so
they stay a faithful upstream copy and stay easy to re-sync.

## Verification status — READ THIS

- ✅ **Stack-agnostic, low risk:** `scripts/model/inspect-onnx.py`,
  `scripts/model/make-static-batch-onnx.py` (pure ONNX/Python), and the parser
  *concepts* in `pipeline-run.md`.
- ⚠️ **NOT device-verified.** None of the `trtexec`/`deepstream-app`/`nvinfer`
  commands in the reference docs have been run on our Xavier NX from this adaptation.
  The deltas below are derived from the project's pinned stack and prior issues
  (#76 opset, ADR-0001/-0003), **not** from a green on-device run. Treat the first
  real import (#77) as the verification pass and correct this doc with what you learn.

## Stack delta table (upstream → Overwatch)

| Dimension | Upstream assumes | Overwatch reality | Action |
|-----------|------------------|-------------------|--------|
| **DeepStream** | 9.0, `pyservicemaker` Python API | 6.x (JetPack 5.1.x), `pyds` bindings | Ignore all `pyservicemaker` references. Pipeline wiring is `deepstream-pipeline`'s job. |
| **TensorRT** | 10.x | **8.5** | trtexec flags differ — see "trtexec flags" below. |
| **OS / arch** | Ubuntu 24.04, x86 / modern Jetson | Ubuntu 20.04, **ARM64 Xavier NX** | — |
| **Python** | one `build/.venv_optimum` with `pip install torch optimum` | Python **3.8** target; **never pip `torch`** (host or device). | Export ONNX on **host** (Python 3.12). Build TRT on **device** in `/srv/farmproject/venv` (already has torch/tensorrt/pyds). |
| **ONNX opset** | 17/18; prefer dynamo exporter | TRT 8.5 **rejects opset ≥ 17** | Force **`dynamo=False, opset_version=12`** and a self-contained ONNX. The upstream "accept opset 18 / prefer dynamo" advice is **the opposite of what we need** — ignore it. |
| **Engine workspace** | `--memPoolSize=workspace:32768M` | 16 GB **unified** memory shared with the OS | Use **2048–4096 MiB**. On TRT 8.5 the trtexec flag is commonly `--workspace=4096` (MiB); confirm `trtexec --help` on-device. |
| **Batch / streams** | `MAX_BS=64`, double to 256+, derive `PEAK_GPU_STREAMS` | **one ZED camera** | Build a **batch-1** (or small fixed) engine. **Skip** the iterative doubling loop and the entire PEAK_GPU_STREAMS / multi-stream-sweep apparatus (Step 5 scaling, Step 7). |
| **Encoder** | `nvv4l2h264enc` primary, theora fallback | `nvv4l2h264enc` is the Jetson HW encoder | Upstream primary path is correct — **keep it**. |
| **Sample video** | `/opt/nvidia/.../sample_720p.mp4` | same path on the device | Fine for a quick single-stream visual check; the real source is the ZED. |
| **Report/PDF** | wkhtmltopdf + pandoc + mermaid/puppeteer, 5 charts, PDF | — | **Do not install on the Jetson.** Optional, host-side only. Perf numbers belong to the ADR-0001 benchmark gate (#8), not here. |
| **Parser zero-init** | `obj = {};` for DS 9.0 OBB `rotation_angle` | DS 6.x has **no** `rotation_angle` | Still zero-init for hygiene; the OBB rationale is moot. |

## trtexec flags: TRT 8.5 vs the upstream TRT 10 commands

`engine-build.md` uses TRT-10 syntax. On TRT 8.5, before copying a command, run
`trtexec --help` on the device and check:

- **Workspace:** upstream `--memPoolSize=workspace:32768M`. TRT 8.5 generally uses
  `--workspace=<MiB>` (e.g. `--workspace=4096`). The upstream "`M` not `MiB`" gotcha
  is a TRT-10 `--memPoolSize` parsing quirk and may not apply to the 8.5 flag.
- **Shapes:** keep small. For one camera: `--minShapes`/`--optShapes`/`--maxShapes`
  all at `1x3xHxW`, or just build a static batch-1 engine.
- **`--fp16`:** keep — FP16 is the Overwatch default (matches `trt-model-conversion`).
- **`--noDataTransfers`, `--skipInference`:** verify these exist on 8.5; drop if not.
- **Engine naming:** the upstream `{model}_dynamic_b{MAX_BS}.engine` convention only
  matters because the report scripts parse it. We are not running those, so name the
  engine whatever `deepstream-pipeline` / the nvinfer config expects.

## Where this hands off to the other skills

```
                 deepstream-import-vision-model (this skill)
   host:  acquire model ──► export ONNX (opset 12) ──► inspect-onnx.py gate
   target:                                   └─► build FP16 TRT engine
   target: write custom bbox parser + Makefile + labels.txt + nvinfer config
                                            │
                  ┌─────────────────────────┴───────────────────────────┐
                  ▼                                                       ▼
        deepstream-pipeline                                   trt-model-conversion
   (decode→nvinfer→nvtracker graph,                        (the *separate* MegaDescriptor
    probe callbacks, ZED depth seam) —                      Swin→TRT FP16 ReID path; on-demand
    consumes the nvinfer config this                        dispatch per ADR-0003. Different
    skill produces)                                         model, different skill.)
```

## Suggested first use (issue #77 — YOLOv8 TRT engine)

1. **Host:** export YOLOv8 → ONNX with `dynamo=False, opset_version=12`
   (see memory `yolov8-onnx-export-for-trt85`; proven in #76).
2. **Host:** `python scripts/model/inspect-onnx.py model.onnx` — confirm `Opset: 12`
   and read off `input_name` / `height` / `width`. If opset ≠ 12, stop and re-export.
3. **Device** (`jetson-agent`, venv `/srv/farmproject/venv`): build the FP16 engine
   with workspace ≤ 4096 MiB, batch-1.
4. **Device:** write the bbox parser per `references/pipeline-run.md` §6b — YOLOv8
   output decode, `network-type=0`, `net-scale-factor=1/255` (verify with ONNX
   Runtime first), `cluster-mode=2`.
5. **Device:** KITTI dump sanity gate (§6g) — confirm non-zero, sane detections.
6. Hand the nvinfer config to **`deepstream-pipeline`** for graph integration.
7. **Skip** steps 7–8 (multi-stream sweep + PDF report).
