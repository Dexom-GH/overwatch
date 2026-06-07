# YOLOv11-on-TRT-8.5 Viability Spike — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove (or disprove) that a stock YOLOv11 model can become a working DeepStream detector on the Jetson Xavier NX's TensorRT 8.5 toolchain — opset-12 ONNX → FP16 engine → `nvinfer` parse — and record a go/no-go decision.

**Architecture:** A de-risking spike. Host side: vendor the v11-aware DeepStream-Yolo exporter, drive a stock `yolo11n.pt` through it to an opset-12 ONNX, and fail loudly if the opset / output layout / validity are wrong (these checks are the host-testable core). Target side (`needs:on-device`): build an FP16 TRT 8.5 engine with `trtexec` and run a farm clip through `nvinfer` using the **unchanged** DeepStream-Yolo parser. Output is a research report + the only durable code artifact, the vendored exporter.

**Tech Stack:** Ultralytics YOLOv11, ONNX (opset 12), `onnx.checker`, TensorRT 8.5 (`trtexec`), DeepStream `nvinfer` + `NvDsInferParseYolo`, pytest (host unit tests), bash (target).

**Spec:** `docs/superpowers/specs/2026-06-07-yolo11-trt85-viability-spike-design.md`
**Branch:** `spike/yolo11-trt85-viability`

---

## File Structure

| File | Created/Modified | Responsibility | Durability |
|---|---|---|---|
| `scripts/dev/vendor/deepstream-yolo/export_yolo11.py` | Create | v11-aware DeepStream-Yolo exporter (handles `C3k2`/`C2PSA`; emits `[1, anchors, 6]` head). | **Durable** |
| `scripts/dev/vendor/deepstream-yolo/NOTICE.md` | Modify | Add attribution for the vendored v11 exporter. | Durable |
| `scripts/dev/spike_yolo11_export.py` | Create | Host driver: `yolo11n.pt` → vendored export → `verify_deepstream_onnx` asserts (opset/layout/checker). Contains the host-testable pure functions. | Spike |
| `tests/unit/test_spike_yolo11_export.py` | Create | Unit tests for `verify_deepstream_onnx` + layout/opset helpers (synthetic ONNX, no GPU/weights). | Spike |
| `src/overwatch/inference/deepstream/configs/nvinfer_yolo11_spike.txt` | Create | Throwaway nvinfer config: COCO `yolo11n` engine, 80 classes, **same parser .so**. | Throwaway |
| `tests/unit/test_nvinfer_yolo11_spike_config.py` | Create | Host sanity guard: spike config has `num-detected-classes=80`, `network-mode=2`, the DeepStream-Yolo parser func. | Spike |
| `docs/research/2026-06-07-yolo11-trt85-viability.md` | Create | The deliverable: go/no-go + four results + fps vs v8 + B punch-list. | **Durable** |
| `docs/ROADMAP_V1_V2.md` | Modify | Add the A→B→C→D pointer so C/D aren't lost. | Durable |

**TDD note for a spike:** the genuinely host-testable code is the ONNX-verification logic and the config sanity guard — those get real TDD (Tasks 2, 4). The vendored exporter (third-party), the export CLI orchestration (needs GPU/weights/network), and the on-device build+run (needs the Jetson) are runbook-style steps with exact commands and expected output — they cannot be unit-tested and are not faked with hollow tests.

---

## Task 1: Vendor the v11-aware DeepStream-Yolo exporter

**Files:**
- Create: `scripts/dev/vendor/deepstream-yolo/export_yolo11.py`
- Modify: `scripts/dev/vendor/deepstream-yolo/NOTICE.md`

Context: the existing `export_yoloV8.py` special-cases v8's `C2f` block via `m.forward = m.forward_split`. YOLOv11 uses `C3k2`/`C2PSA` blocks instead, so we vendor the v11 exporter from `marcoslucianops/DeepStream-Yolo` (`utils/export_yolo11.py`). It shares the generic `DeepStreamOutput` head (transpose + max → `[1, anchors, 6]`) — the reason the parser is expected to be reusable unchanged.

- [ ] **Step 1: Add the vendored exporter**

Create `scripts/dev/vendor/deepstream-yolo/export_yolo11.py` with the upstream
`utils/export_yolo11.py` content from marcoslucianops/DeepStream-Yolo. It mirrors
`export_yoloV8.py` but imports the v11 module set and does **not** apply the
`C2f.forward_split` hack. The key pieces that MUST be present and unchanged:

```python
# --- output head (identical to v8 exporter; what NvDsInferParseYolo expects) ---
class DeepStreamOutput(nn.Module):
    def forward(self, x):
        x = x.transpose(1, 2)
        boxes = x[:, :, :4]
        scores, labels = torch.max(x[:, :, 4:], dim=-1, keepdim=True)
        return torch.cat([boxes, scores, labels.to(boxes.dtype)], dim=-1)

# --- CLI: opset MUST default low enough for TRT 8.5; we always pass --opset 12 ---
parser.add_argument("--opset", type=int, default=12, help="ONNX opset version")
```

The exporter also writes a `labels.txt` from `model.names` (80 COCO names) — we use that on-device; no need to hand-author COCO labels.

- [ ] **Step 2: Record attribution**

Append to `scripts/dev/vendor/deepstream-yolo/NOTICE.md`:

```markdown

## export_yolo11.py

Vendored from marcoslucianops/DeepStream-Yolo (`utils/export_yolo11.py`), MIT.
Used to export Ultralytics YOLOv11 to the DeepStream `[1, anchors, 6]` ONNX
layout consumed by `NvDsInferParseYolo`. Spike: #A (YOLOv11-on-TRT-8.5 viability).
```

- [ ] **Step 3: Byte-compile sanity (no execution — third-party, needs GPU/weights)**

Run: `python -m py_compile scripts/dev/vendor/deepstream-yolo/export_yolo11.py`
Expected: exits 0, no output.

- [ ] **Step 4: Commit**

```bash
git add scripts/dev/vendor/deepstream-yolo/export_yolo11.py scripts/dev/vendor/deepstream-yolo/NOTICE.md
git commit -m "spike(yolo11): vendor v11-aware DeepStream-Yolo exporter"
```

---

## Task 2: Host-testable ONNX verification (TDD)

**Files:**
- Create: `scripts/dev/spike_yolo11_export.py`
- Test: `tests/unit/test_spike_yolo11_export.py`

The verification logic is pure and host-testable with synthetic ONNX models — no
GPU, weights, or network. We TDD it first, then the CLI wraps it (Task 3).

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_spike_yolo11_export.py`:

```python
"""Host unit tests for the YOLOv11 spike ONNX verification (no GPU/weights)."""
from __future__ import annotations

import onnx
import pytest
from onnx import TensorProto, helper

from scripts.dev.spike_yolo11_export import (
    DeepStreamLayoutError,
    OpsetError,
    verify_deepstream_onnx,
)


def _model(out_shape, opset):
    """A minimal valid ONNX model: Identity from input to output of out_shape."""
    info = helper.make_tensor_value_info("input", TensorProto.FLOAT, out_shape)
    out = helper.make_tensor_value_info("output", TensorProto.FLOAT, out_shape)
    node = helper.make_node("Identity", ["input"], ["output"])
    graph = helper.make_graph([node], "g", [info], [out])
    return helper.make_model(graph, opset_imports=[helper.make_opsetid("", opset)])


def test_accepts_opset12_deepstream_layout():
    # [1, anchors, 6] = boxes(4)+score(1)+label(1); opset 12 (TRT 8.5 safe)
    verify_deepstream_onnx(_model([1, 8400, 6], 12))  # must not raise


def test_rejects_opset_above_16():
    with pytest.raises(OpsetError):
        verify_deepstream_onnx(_model([1, 8400, 6], 17))


def test_rejects_non_deepstream_layout():
    # raw Ultralytics-style last dim (not 6) must be rejected
    with pytest.raises(DeepStreamLayoutError):
        verify_deepstream_onnx(_model([1, 8400, 4], 12))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_spike_yolo11_export.py -v`
Expected: FAIL — `ModuleNotFoundError`/`ImportError` (module + symbols not defined yet).

- [ ] **Step 3: Write minimal implementation**

Create `scripts/dev/spike_yolo11_export.py`:

```python
#!/usr/bin/env python3
"""YOLOv11-on-TRT-8.5 spike: export stock yolo11n.pt and verify the artifact.

Host/off-device GPU only. Drives the vendored v11 exporter
(``scripts/dev/vendor/deepstream-yolo/export_yolo11.py``) then fails loudly if
the ONNX is not TRT-8.5-safe (opset <= 16) and not the DeepStream-Yolo
``[1, anchors, 6]`` output layout NvDsInferParseYolo decodes. Mirrors the
fail-loudly discipline of train_yolov8_farm.py so a bad artifact never reaches
the device engine build.
"""
from __future__ import annotations

import onnx

TRT85_MAX_OPSET = 16  # TRT 8.5 rejects opset >= 17 (see #76)
DEEPSTREAM_LAST_DIM = 6  # boxes(4) + score(1) + label(1)


class OpsetError(ValueError):
    """ONNX opset is too high for TensorRT 8.5."""


class DeepStreamLayoutError(ValueError):
    """ONNX output is not the [1, anchors, 6] DeepStream-Yolo layout."""


def opset_of(model: "onnx.ModelProto") -> int:
    """Default-domain opset of an ONNX model."""
    for op in model.opset_import:
        if op.domain in ("", "ai.onnx"):
            return op.version
    return model.opset_import[0].version if model.opset_import else -1


def output_last_dim(model: "onnx.ModelProto") -> int:
    """Last declared dim of the first graph output (-1 if rank != 3 or dynamic)."""
    outs = model.graph.output
    if not outs:
        return -1
    dims = outs[0].type.tensor_type.shape.dim
    if len(dims) != 3:
        return -1
    return dims[2].dim_value if dims[2].dim_value else -1


def verify_deepstream_onnx(model: "onnx.ModelProto", expected_max_opset: int = TRT85_MAX_OPSET) -> None:
    """Raise if ``model`` is not a TRT-8.5-safe DeepStream-Yolo detector ONNX."""
    onnx.checker.check_model(model)
    opset = opset_of(model)
    if opset > expected_max_opset:
        raise OpsetError(
            "opset {} > {} — TensorRT 8.5 will reject it (re-export with --opset 12)".format(
                opset, expected_max_opset
            )
        )
    last = output_last_dim(model)
    if last != DEEPSTREAM_LAST_DIM:
        raise DeepStreamLayoutError(
            "output last dim {} != {} — not the DeepStream-Yolo [1, anchors, 6] "
            "layout; NvDsInferParseYolo would decode zero detections".format(
                last, DEEPSTREAM_LAST_DIM
            )
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_spike_yolo11_export.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add scripts/dev/spike_yolo11_export.py tests/unit/test_spike_yolo11_export.py
git commit -m "spike(yolo11): host-testable ONNX verify (opset<=16 + [1,N,6] layout)"
```

---

## Task 3: Export CLI orchestration (host, manual run)

**Files:**
- Modify: `scripts/dev/spike_yolo11_export.py`

Wraps the verified core in a CLI that pulls stock `yolo11n.pt`, runs the vendored
exporter as a subprocess, then verifies the resulting ONNX. Cannot be unit-tested
(needs Ultralytics + weights download + a CUDA/CPU export); run manually.

- [ ] **Step 1: Add the CLI**

Append to `scripts/dev/spike_yolo11_export.py`:

```python
def _run_export(weights: str, onnx_out: str, opset: int = 12, imgsz: int = 640) -> None:
    """Invoke the vendored v11 exporter as a subprocess."""
    import subprocess
    import sys
    from pathlib import Path

    exporter = Path(__file__).resolve().parent / "vendor" / "deepstream-yolo" / "export_yolo11.py"
    cmd = [sys.executable, str(exporter), "-w", weights, "--opset", str(opset), "-s", str(imgsz)]
    print("[spike] running:", " ".join(cmd))
    subprocess.run(cmd, check=True)
    produced = Path(weights).with_suffix(".onnx")
    if produced.resolve() != Path(onnx_out).resolve():
        produced.replace(onnx_out)


def _main(argv=None) -> int:
    import argparse

    p = argparse.ArgumentParser(description="YOLOv11-on-TRT-8.5 spike export+verify")
    p.add_argument("--weights", default="yolo11n.pt", help="stock COCO weights (auto-downloaded by Ultralytics)")
    p.add_argument("--out", default="models/yolo11n.onnx", help="ONNX output path")
    p.add_argument("--opset", type=int, default=12)
    p.add_argument("--imgsz", type=int, default=640)
    args = p.parse_args(argv)

    _run_export(args.weights, args.out, args.opset, args.imgsz)
    model = onnx.load(args.out)
    verify_deepstream_onnx(model)
    print("[spike] OK: {} is opset<= {}, DeepStream [1, anchors, 6] layout, valid".format(
        args.out, TRT85_MAX_OPSET))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
```

- [ ] **Step 2: Manual run (host with a GPU or CPU + Ultralytics installed)**

Run:
```bash
python scripts/dev/spike_yolo11_export.py --out models/yolo11n.onnx
```
Expected (GO path): exporter logs, then
`[spike] OK: models/yolo11n.onnx is opset<= 16, DeepStream [1, anchors, 6] layout, valid`
Expected (NO-GO path): a raised `OpsetError`/`DeepStreamLayoutError`, or an exporter
traceback if v11 blocks won't trace at opset 12 — **record the exact error in the report (Task 7).**

- [ ] **Step 3: Inspect the ONNX (cross-check with the existing tool)**

Run:
```bash
python .claude/skills/deepstream-import-vision-model/scripts/model/inspect-onnx.py models/yolo11n.onnx
```
Expected: `Opset: 12`, an output `shape=[1, <anchors>, 6]`, and `✓ ONNX model is valid`.
(`models/` is gitignored — the ONNX is not committed; its existence + this log feed the report.)

- [ ] **Step 4: Commit the CLI**

```bash
git add scripts/dev/spike_yolo11_export.py
git commit -m "spike(yolo11): export CLI (stock yolo11n -> opset-12 ONNX + verify)"
```

---

## Task 4: Spike nvinfer config + sanity guard (TDD)

**Files:**
- Create: `src/overwatch/inference/deepstream/configs/nvinfer_yolo11_spike.txt`
- Test: `tests/unit/test_nvinfer_yolo11_spike_config.py`

A throwaway nvinfer config pointing at the COCO `yolo11n` engine with 80 classes,
reusing the **same** DeepStream-Yolo parser .so as production. The host guard
asserts the three fields that, if wrong, silently break parsing.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_nvinfer_yolo11_spike_config.py`:

```python
"""Host sanity guard for the throwaway YOLOv11 spike nvinfer config."""
from __future__ import annotations

import re
from pathlib import Path

CONFIG = (
    Path(__file__).resolve().parents[2]
    / "src" / "overwatch" / "inference" / "deepstream" / "configs"
    / "nvinfer_yolo11_spike.txt"
)


def _field(name: str) -> str:
    m = re.search(r"^\s*{}\s*=\s*(.+?)\s*$".format(re.escape(name)), CONFIG.read_text(), re.M)
    assert m, "missing field: {}".format(name)
    return m.group(1)


def test_coco_class_count():
    assert _field("num-detected-classes") == "80"  # stock COCO yolo11n


def test_fp16_network_mode():
    assert _field("network-mode") == "2"  # FP16


def test_reuses_deepstream_yolo_parser():
    assert _field("parse-bbox-func-name") == "NvDsInferParseYolo"
    assert _field("custom-lib-path").endswith("libnvdsinfer_custom_impl_Yolo.so")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_nvinfer_yolo11_spike_config.py -v`
Expected: FAIL — `assert m` raises because the config file does not exist yet.

- [ ] **Step 3: Create the spike config**

Create `src/overwatch/inference/deepstream/configs/nvinfer_yolo11_spike.txt`:

```ini
# THROWAWAY spike config (YOLOv11-on-TRT-8.5 viability, sub-project A).
# Stock COCO yolo11n — NOT a farm model. Do NOT wire into the app. Delete after
# the spike's go/no-go is recorded. Mirrors nvinfer_detector.txt but for 80 COCO
# classes and the spike engine. Paths are CONFIG-DIR-relative (see #84/#97).
[property]
gpu-id=0
net-scale-factor=0.0039215697906911373
model-color-format=0
onnx-file=models/yolo11n.onnx
model-engine-file=models/yolo11n_fp16.engine
labelfile-path=labels_coco.txt
batch-size=1
network-mode=2
num-detected-classes=80
interval=0
gie-unique-id=1
process-mode=1
network-type=0
cluster-mode=2
maintain-aspect-ratio=1
symmetric-padding=1
parse-bbox-func-name=NvDsInferParseYolo
custom-lib-path=models/DeepStream-Yolo/nvdsinfer_custom_impl_Yolo/libnvdsinfer_custom_impl_Yolo.so

[class-attrs-all]
nms-iou-threshold=0.45
pre-cluster-threshold=0.25
topk=300
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_nvinfer_yolo11_spike_config.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/overwatch/inference/deepstream/configs/nvinfer_yolo11_spike.txt tests/unit/test_nvinfer_yolo11_spike_config.py
git commit -m "spike(yolo11): throwaway nvinfer config (COCO 80-class) + sanity guard"
```

---

## Task 5: On-device FP16 engine build (`needs:on-device`)

**Files:** none (runbook step on the Jetson via `jetson-agent`).

Deploy the host-produced `models/yolo11n.onnx` to the device, then build an FP16
engine with the **same** `trtexec` flags `56_build_engines.sh` uses for the
detector. This is where TRT 8.5 will reject v11 if it's going to.

- [ ] **Step 1: Stage the ONNX + COCO labels on the device**

From the host (per the deploy convention in MEMORY — files group-writable, no sudo):
```bash
scp models/yolo11n.onnx jetson-agent:/srv/farmproject/overwatch/models/yolo11n.onnx
scp labels.txt          jetson-agent:/srv/farmproject/overwatch/src/overwatch/inference/deepstream/configs/labels_coco.txt
```
(`labels.txt` is the 80-line COCO file the exporter wrote in Task 3.)
Expected: both copies succeed.

- [ ] **Step 2: Build the FP16 engine on-device**

On `jetson-agent`:
```bash
cd /srv/farmproject/overwatch
/usr/src/tensorrt/bin/trtexec --onnx=models/yolo11n.onnx --fp16 \
  --saveEngine=models/yolo11n_fp16.engine --memPoolSize=workspace:4096
```
Expected (GO): `&&&& PASSED` and `models/yolo11n_fp16.engine` written, **no**
"unsupported opset" / "no importer for op" errors.
Expected (NO-GO): trtexec aborts with an op/opset error — **capture the full
stderr for the report (Task 7); this is a primary NO-GO trigger.**

- [ ] **Step 3: Record the build evidence**

Save the trtexec output (pass/fail + any errors) to paste into the report. No commit
(engine is gitignored; the report is the durable record).

---

## Task 6: On-device `nvinfer` parse + fps reading (`needs:on-device`)

**Files:** none (runbook step on the Jetson).

Run a farm clip through a single-stream DeepStream pipeline using the spike config
and confirm `NvDsInferParseYolo` emits plausible boxes, then read throughput.

- [ ] **Step 1: Confirm the parser .so is staged**

On `jetson-agent`, ensure the DeepStream-Yolo parser exists at the config-relative
path (staged for the v8 detector by `57_stage_detector_assets.sh`):
```bash
ls -l /srv/farmproject/overwatch/models/DeepStream-Yolo/nvdsinfer_custom_impl_Yolo/libnvdsinfer_custom_impl_Yolo.so
```
Expected: the file exists (reused unchanged from the v8 path). If missing, run
`bash scripts/target/57_stage_detector_assets.sh` first.

- [ ] **Step 2: Run a single-stream test over a farm clip**

Use the canonical single-stream invocation from the import skill's reference
(`.claude/skills/deepstream-import-vision-model/references/pipeline-run.md`),
pointing the inference element at the spike config and the source at the demo clip
used by `demo_rtsp.sh`. Run with the static-TLS preloads required on this device
(per MEMORY — nvtracker/nvinfer static-TLS gotcha):
```bash
cd /srv/farmproject/overwatch
LD_PRELOAD=libgomp.so.1:libGLdispatch.so.0 \
GST_PLUGIN_PATH=/opt/nvidia/deepstream/deepstream/lib/gst-plugins \
gst-launch-1.0 -v uridecodebin uri=file:///srv/farmproject/clips/farm_demo.mp4 ! \
  nvvideoconvert ! mux.sink_0 nvstreammux name=mux batch-size=1 width=1280 height=720 ! \
  nvinfer config-file-path=src/overwatch/inference/deepstream/configs/nvinfer_yolo11_spike.txt ! \
  nvvideoconvert ! nvdsosd ! fakesink
```
(Adjust the clip path/dims to the actual demo clip. If `pipeline-run.md` provides a
ready single-stream harness, prefer it — the requirement is: spike config in, boxes out.)
Expected (GO): nvinfer logs non-zero detections; with `nvdsosd` you can dump a frame
to confirm boxes land on animals/people. Expected (NO-GO): zero detections every
frame (layout mismatch) — capture and report.

- [ ] **Step 3: Read throughput**

Re-run capped/timed (e.g. add `num-buffers` to the source or use the perf overlay)
and record the steady-state fps. Compare against the v8 detector's ~56 fps (#15) as
the informational ballpark from the spec. Save the number for the report.

---

## Task 7: Research report + roadmap pointer (the deliverable)

**Files:**
- Create: `docs/research/2026-06-07-yolo11-trt85-viability.md`
- Modify: `docs/ROADMAP_V1_V2.md`

- [ ] **Step 1: Write the report**

Create `docs/research/2026-06-07-yolo11-trt85-viability.md`:

```markdown
# YOLOv11 on TRT 8.5 — Viability Spike Result

**Date:** 2026-06-07
**Spec:** docs/superpowers/specs/2026-06-07-yolo11-trt85-viability-spike-design.md
**Decision:** GO | NO-GO   <!-- fill in -->

## Method
Stock `yolo11n.pt` (COCO, no training) → vendored DeepStream-Yolo v11 exporter
(opset 12) → host verify → on-device TRT 8.5 FP16 engine (`trtexec`) → DeepStream
`nvinfer` + unchanged `NvDsInferParseYolo` over the demo farm clip.

## Results (the four gate criteria)
1. **opset-12 ONNX export + layout + checker:** PASS/FAIL — <evidence: inspect-onnx output>
2. **TRT 8.5 FP16 engine build:** PASS/FAIL — <evidence: trtexec tail / error>
3. **nvinfer parses plausible boxes:** PASS/FAIL — <evidence: detection counts / frame dump>
4. **Throughput:** <fps> vs v8 ~56 fps (#15) — informational

## Decision & rationale
<GO: v11 viable; B may build on v11.>
<NO-GO: <which criterion failed + exact error>; fallback — B proceeds on YOLOv8,
v11 logged to V2.>

## Punch-list for sub-project B (if GO)
- Exporter deltas needed for the farm class set: <…>
- Engine-build flag changes: <…>
- Anything in the parser/config that needed touching: <…>
```
Fill every `<…>`/`PASS|FAIL`/`GO|NO-GO` from Tasks 3, 5, 6 evidence — **no
placeholders left in the committed report.**

- [ ] **Step 2: Add the roadmap pointer**

In `docs/ROADMAP_V1_V2.md`, add a short entry recording the A→B→C→D decomposition
and this spike's outcome, linking the spec and this report, so sub-projects C
(human semantics) and D (tracking/data-quality) are not lost.

- [ ] **Step 3: Commit**

```bash
git add docs/research/2026-06-07-yolo11-trt85-viability.md docs/ROADMAP_V1_V2.md
git commit -m "spike(yolo11): record TRT-8.5 viability go/no-go + A->D roadmap pointer"
```

- [ ] **Step 4: Run the full host test suite**

Run: `python -m pytest tests/unit -q`
Expected: all pass (existing suite + the two new spike test modules), no import errors
on host (target-only deps stay guarded).

---

## Self-Review (completed during planning)

- **Spec coverage:** A.2 success criteria → Tasks 3/5/6 (the four gates) + Task 7 report; A.3 components → Tasks 1 (exporter), 2-3 (driver), 4 (spike config), 5-6 (device build/run), 7 (report); A.5 fallback → Task 5/6 NO-GO captures + Task 7 decision section; A.6 out-of-scope (no training/person/contract change) → respected (stock COCO, throwaway config, no `animals.yaml`/`labels.txt`/prod-config edits); A.7 verification → host tests (Tasks 2/4/7-step4) + on-device `needs:on-device` (Tasks 5/6); process note (roadmap) → Task 7 step 2.
- **Placeholder scan:** the only intentional fill-ins are inside the *report template* (Task 7), which the executor fills from real evidence; flagged explicitly. No "TBD/handle errors/similar-to" in implementation steps.
- **Type consistency:** `verify_deepstream_onnx`, `OpsetError`, `DeepStreamLayoutError`, `opset_of`, `output_last_dim` names match between Task 2 test, Task 2 impl, and Task 3 CLI. Config field names in Task 4 test match the Task 4 config body.
