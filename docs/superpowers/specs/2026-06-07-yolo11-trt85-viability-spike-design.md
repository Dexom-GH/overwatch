# YOLOv11-on-TRT-8.5 Viability Spike — Design

**Date:** 2026-06-07
**Status:** Approved (brainstorming) — pending implementation plan
**Scope:** Sub-project **A** of the "YOLOv11 for people + animals" initiative.

---

## Background

Overwatch's V1 detector is a custom fine-tuned **Ultralytics YOLOv8**, running as an
FP16 **TensorRT 8.5** engine inside DeepStream `nvinfer` on the Jetson Xavier NX
(~56 fps, #15). It ships a 3-class farm model (`sheep / goat / poultry`; tier 1-2 of
`configs/animals.yaml`). There is **no `person` class** today — V1 is animal-only.

The user wants to (1) move to **YOLOv11** (or latest), (2) **detect people as well as
animals**, (3) improve **classification**, and (4) improve **tracking / overall data**.

These goals were decomposed (and the decomposition approved) into four sequenced
sub-projects:

| # | Sub-project | Delivers |
|---|---|---|
| **A** | **YOLOv11-on-TRT-8.5 viability spike** (this doc) | Proof that v11 exports → opset-12 ONNX → TRT 8.5 FP16 engine → parses in `nvinfer`, like-for-like. |
| B | Class-set expansion + retrain (`person` + animals) | v11 fine-tuned on `person` + sheep/goat/poultry; "detect people and animals" + "improved classification". Carries the **mAP/fps comparison vs v8** as its acceptance gate. |
| C | Human semantics downstream | What fusion + Slack do with a detected person (intrusion / presence / worker-vs-intruder). |
| D | Tracking + data-quality pass | `nvtracker` tuning, ReID-backed association, ID-switch / track-stability metrics ("overall data"). |

A is the **gate**: it targets the single load-bearing unknown (does v11's newer
architecture survive the old TRT 8.5 toolchain?). If A fails, B–D either change
approach or are wasted — so A must fail *cheap*, before any training or contract change.

## Why this is the risky part

- The vendored DeepStream-Yolo exporter (`scripts/dev/vendor/deepstream-yolo/export_yoloV8.py`)
  special-cases v8's `C2f` block via `m.forward = m.forward_split`. **YOLOv11 replaces
  those blocks with `C3k2` and `C2PSA`** — so the existing exporter will not cleanly
  handle v11; the v11-aware exporter variant is required.
- TRT 8.5 (pinned by JetPack 5.1.x on Xavier NX) **rejects opset >= 17**. #76 already
  showed v8 ONNX export was painful for exactly this reason. v11 is newer architecture
  against the same old TRT — the export + engine build is where this most likely breaks.
- The DeepStream output head used by the exporter (transpose + max → `[1, anchors, 6]`)
  is generic across v8/v10/v11, which is the main reason there is hope the parser is
  reusable unchanged.

## Goal & nature

A **de-risking spike** (in the lineage of #119, #64, #35), **not** production code. Its
deliverable is a **go/no-go decision + a research report**, plus the minimum scaffolding
needed to reach it. It answers exactly one question:

> Can a YOLOv11 model become a working DeepStream detector on this Jetson's TRT 8.5?

Everything it produces is throwaway **except** the research report and the vendored v11
exporter.

## Success criteria — the go/no-go gate

**GO** requires all four, **on-device**:

1. `yolo11n.pt` (stock COCO weights, no training) exports to **opset-12 ONNX**, passing
   `onnx.checker` with the `[1, anchors, 6]` DeepStream-Yolo output layout (verified by
   the existing `inspect-onnx.py`).
2. TensorRT **8.5** builds an **FP16** engine from that ONNX with **no rejected
   op/opset**.
3. DeepStream `nvinfer` — using the **unchanged** DeepStream-Yolo `NvDsInferParseYolo`
   parser + `libnvdsinfer_custom_impl_Yolo.so` — emits non-empty, plausible boxes on a
   real farm clip.
4. A throughput reading is captured and is **in the same ballpark** as the current v8
   engine. (Record the number; this is informational, not a hard pass/fail.)

Any failure → **NO-GO**, with the fallback documented (see Risk & fallback).

## Components

| Component | Location | Role | Durability |
|---|---|---|---|
| **Vendored v11 exporter** | `scripts/dev/vendor/deepstream-yolo/export_yolo11.py` (+ `NOTICE` attribution) | v11-aware DeepStream-Yolo exporter handling `C3k2` / `C2PSA`. | **Durable** (only lasting code artifact). |
| **Spike export driver** | `scripts/dev/spike_yolo11_export.py` | Host: pull `yolo11n.pt`, run the vendored exporter, assert `opset == 12` + output layout + `onnx.checker`. Mirrors `train_yolov8_farm.py`'s fail-loudly discipline. | Spike. |
| **Spike nvinfer config** | `src/overwatch/inference/deepstream/configs/nvinfer_yolo11_spike.txt` | Copy of `nvinfer_detector.txt` pointed at the COCO engine + an 80-class COCO `labels.txt`, `num-detected-classes=80`, **same parser .so**. | Throwaway. |
| **Device build + run** | reuse `scripts/target/56_build_engines.sh` + the #58 RTSP demo runner | On-device trtexec FP16 build; run a farm clip through the pipeline; read fps. | Reused. |
| **Report** | `docs/research/2026-06-07-yolo11-trt85-viability.md` | Go/no-go, all four results, fps vs v8, and a punch-list of what B will need (exporter deltas, etc.). | **Durable.** |

## Flow

```
host:   yolo11n.pt
          -> vendored v11 export -> opset-12 ONNX  (+ host asserts: opset/layout/checker)
          -> deploy ONNX to device
device: trtexec FP16 build (TRT 8.5) -> TRT engine
          -> nvinfer (+ DeepStream-Yolo parser, unchanged) over a farm clip
          -> boxes + fps
          -> research report (go/no-go)
```

Host/target split is respected: **export on host**, **build + run on target**
(`jetson-agent`). No target-only deps imported on host; spike scripts live in
`scripts/dev/` (host) and reuse `scripts/target/` (device).

## Risk & fallback (the whole point)

- **Export emits unsupported opset/op** (v11 blocks trace to ops TRT 8.5 lacks):
  attempt exporter tweaks; if fundamentally blocked → **NO-GO fallback: sub-project B
  proceeds on YOLOv8**, still delivering person-detection via class expansion, and v11
  is logged to V2.
- **Parser layout mismatch** (v11 detection head differs from what `NvDsInferParseYolo`
  expects): scope a parser patch; if costly → same fallback.
- Either way the spike **fails cheap** — no training, no `animals.yaml` / `labels.txt` /
  production nvinfer changes are spent before v11 viability is known.

## Explicitly out of scope (→ B / C / D)

- No training, no farm classes, **no `person` semantics**, no tracking work.
- No changes to `configs/animals.yaml`, the generated `labels.txt`, or the production
  `nvinfer_detector.txt`. The COCO run exists purely to exercise the toolchain.
- The **mAP / accuracy comparison** vs v8 is **B's** acceptance gate, not A's (B has to
  run a training pass anyway).

## Verification

- **Host:** the export driver's opset / layout / `onnx.checker` asserts *are* the host
  test (fail loudly; a bad artifact never reaches the device).
- **Target (`needs:on-device`):** engine-build log shows a clean FP16 build; `nvinfer`
  box output on the farm clip; captured fps. The spike carries the `needs:on-device`
  label per project convention.

## Process note

This spec should land as a **groomed spike issue** (via the `product-owner` agent /
GitHub Issues, `Dexom-GH/overwatch`) before implementation, matching the repo's
"groom before you build" rule and Definition of Ready. A–D should be reflected in the
roadmap so C/D are not lost.
