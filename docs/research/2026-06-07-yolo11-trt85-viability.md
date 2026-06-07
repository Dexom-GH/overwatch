# YOLOv11 on TRT 8.5 — Viability Spike Result

**Date:** 2026-06-07
**Spec:** [docs/superpowers/specs/2026-06-07-yolo11-trt85-viability-spike-design.md](../superpowers/specs/2026-06-07-yolo11-trt85-viability-spike-design.md)
**Plan:** [docs/superpowers/plans/2026-06-07-yolo11-trt85-viability-spike.md](../superpowers/plans/2026-06-07-yolo11-trt85-viability-spike.md)
**Decision:** ✅ **GO** — YOLOv11 is viable on the Jetson Xavier NX (TRT 8.5), **with one
required change to the export path** (trace on GPU, not CPU — see Finding below).

Sub-project **A** of the "YOLOv11 for people + animals" initiative (A→B→C→D).

## Method

Stock `yolo11n.pt` (COCO, no fine-tuning) → vendored DeepStream-Yolo v11 exporter
(opset 12) → host verify guard → **on-device** TensorRT 8.5 FP16 engine (`trtexec`) →
DeepStream `nvinfer` + the **unchanged** `NvDsInferParseYolo` parser, over
`sample_1080p_h264.mp4` (a street scene — exercises `person` + vehicles).

**Environment (device):** Jetson Xavier NX, JetPack 5.1.x, **TensorRT 8.5.02**,
torch `2.1.0a0+nv23.06` (CUDA), DeepStream + DeepStream-Yolo parser reused from the v8
detector path, ultralytics **8.4.61** (layered into a throwaway `--target` dir, shared
venv untouched).

## Results — the four gate criteria

| # | Gate | Result | Evidence |
|---|---|---|---|
| 1 | opset-12 ONNX export + `[1, anchors, 6]` layout + `onnx.checker` | ✅ PASS | `opset 12`, output `[1, 8400, 6]`, verify guard OK |
| 2 | **TRT 8.5 FP16 engine build** (the feared unknown) | ✅ PASS | `&&&& PASSED TensorRT v8502`; FP16 engine built (no rejected op/opset) |
| 3 | `nvinfer` parses plausible boxes | ✅ PASS (after fix) | **3512 detections / 300 frames, 300/300 frames with objects** |
| 4 | Throughput in the v8 ballpark | ✅ PASS | **~47 fps** end-to-end single-stream (1443 frames / 30.8 s, incl. ~7 s startup) vs v8 ~56 fps (#15); comfortably real-time |

**Gate-3 class breakdown** (COCO yolo11n on the street clip):

```
car 1901 · person 1290 · backpack 117 · bicycle 111 · truck 44 · bus 35 · motorcycle 12 · skateboard 2
```

`person` is detected strongly (1290) — **the people-detection goal is proven achievable
on YOLOv11.** Visual spot-check of burned-in `nvdsosd` frames matched the counts.

## Finding (load-bearing) — export must trace on the GPU

The first end-to-end attempt produced **zero detections**. Systematic debugging:

1. dropped `pre-cluster-threshold` 0.25 → 0.01: still zero (not a confidence issue);
2. pyds probe on `nvinfer` output: 0 objects over 300 frames;
3. raw ONNX/model output inspection: **all NaN**;
4. stock `yolo11n` (no monkeypatch, no DeepStream head): **also NaN**;
5. weights: **clean** (0/256 params NaN);
6. **CPU forward → NaN; CUDA forward → clean** (boxes in correct ~640 px range).

**Root cause:** this Jetson's `torch 2.1.0a0+nv23.06` produces **NaN on CPU for
YOLOv11's C2PSA attention layers**. The vendored DeepStream-Yolo exporter traces on
`torch.device("cpu")`, so the NaN bakes into the ONNX → into the FP16 engine → zero
detections. The model is correct on GPU.

**Fix:** trace the export on CUDA. `scripts/dev/spike_yolo11_export.py` now imports the
vendored exporter's helpers in-process and traces on the GPU (auto-selects cuda; new
`--device` flag), instead of shelling out to the exporter's CPU-only `main()`. After
re-exporting on CUDA and rebuilding the engine, gates 3–4 passed (numbers above). The
vendored exporter file itself is left verbatim.

## Other observations

- **Engine build is slow: ~20 min** (1234 s) for `yolo11n` on the Xavier NX — the
  C2PSA attention blocks are expensive for TRT to optimize. One-time per engine, but
  budget for it in B's build/CI-on-device steps (v8 builds far faster).
- DeepStream-Yolo parser + `[1, anchors, 6]` head + `net-scale-factor=1/255` carried
  over from the v8 detector **unchanged** — no parser work was needed for v11.
- ultralytics' v11 exporter is structurally identical to the v8 one (the v11
  `C3k2`/`C2PSA` handling lives inside ultralytics, not the export script) and shares
  the same output head — which is why the parser is reusable.

## Punch-list for sub-project B (retrain on `person` + animals)

- **Export on GPU.** Use `spike_yolo11_export.py --device cuda` (or default auto). A
  CPU trace silently produces a NaN engine on this hardware. Consider a host-side
  verify step that runs the engine/ONNX on a real frame and asserts non-NaN output
  before deploy (the current guard only checks opset/layout, not values).
- **Class map / contract.** Adding `person` changes `configs/animals.yaml` semantics
  (it would no longer be only animals — rename or split), `labels.txt`, and
  `num-detected-classes`. This is the most-reviewed surface — change deliberately.
- **mAP/fps decision gate** (deferred from A): fine-tune `yolo11n` on the farm +
  `person` set and compare mAP **and** fps against the v8 farm detector to confirm the
  migration is worth it. v11n ran ~47 fps single-stream here vs v8 ~56 fps — quantify
  on the real classes/resolution.
- Budget ~20 min/engine for on-device TRT builds.

## Reproduction (on `jetson-agent`)

```bash
cd /srv/farmproject/overwatch
export PYTHONPATH=/srv/farmproject/yolo-spike/pylibs       # throwaway ultralytics layer
export YOLO_CONFIG_DIR=/tmp/ultra
# export (traces on cuda):
/srv/farmproject/venv/bin/python scripts/dev/spike_yolo11_export.py --weights yolo11n.pt --out models/yolo11n.onnx
cp labels.txt src/overwatch/inference/deepstream/configs/labels_coco.txt
# build engine (~20 min):
/usr/src/tensorrt/bin/trtexec --onnx=models/yolo11n.onnx --fp16 \
  --saveEngine=models/yolo11n_fp16.engine --memPoolSize=workspace:4096
# count detections:
LD_PRELOAD=libgomp.so.1:libGLdispatch.so.0 \
GST_PLUGIN_PATH=/opt/nvidia/deepstream/deepstream/lib/gst-plugins \
/srv/farmproject/venv/bin/python scripts/dev/spike_yolo11_probe.py \
  src/overwatch/inference/deepstream/configs/nvinfer_yolo11_spike.txt \
  /opt/nvidia/deepstream/deepstream/samples/streams/sample_1080p_h264.mp4 \
  src/overwatch/inference/deepstream/configs/labels_coco.txt 300
```
