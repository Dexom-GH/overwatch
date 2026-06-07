# ADR 0009 — Detector model = YOLOv11 (supersedes YOLOv8 for V1)

- **Status:** Proposed
- **Date:** 2026-06-07
- **Deciders:** Product Owner (pending the #147 gate)

## Context

V1 needs to detect **people AND animals** from a single detector. The current V1
detector is **YOLOv8** (stock `yolov8n` in #76; 3-class farm fine-tune in #77;
parser/labels in #56; ~56 fps on-device in #15). We explored upgrading to
**YOLOv11** to add a `person` class and (claimed) better classification/tracking.

A **device-verified viability spike (spike A, GO)** established that v11 is
runnable on the Xavier NX / TRT 8.5:

- Stock `yolo11n` exports at **opset 12**, builds an **FP16 TensorRT 8.5 engine**,
  and `nvinfer` detects `person` + vehicles at **~47 fps single-stream** using the
  **unchanged DeepStream-Yolo parser + net-scale-factor carried over from v8**.
- **Device gotcha:** this Jetson's **torch 2.1 emits NaN on CPU** for v11's C2PSA
  attention — the export **must trace on GPU**
  (`scripts/dev/spike_yolo11_export.py --device cuda`).
- **On-device TRT engine build ~20 min.**
- Evidence: `docs/research/2026-06-07-yolo11-trt85-viability.md`, the spec under
  `docs/superpowers/specs/2026-06-07-yolo11-trt85-viability-spike-design.md`, and
  **PR #145**.

**Licensing:** Ultralytics YOLOv11 is cleared by **ADR-0007** (non-commercial /
AGPL-3.0 posture) — no new licensing blocker.

Spike A proved v11 *runs*. It did **not** prove v11 is *better than v8* for our
classes, nor that it stays real-time once fine-tuned. That comparison is the
**B1 gate spike (#147)**.

## Options considered

### Option A — Keep YOLOv8 (status quo)
- Pros: already device-verified (#77/#15, ~56 fps); no migration cost.
- Cons: adding `person` still requires a retrain regardless; foregoes v11's
  claimed classification/tracking improvements.

### Option B — Adopt YOLOv11 (supersede v8 for V1)
- Pros: single detector for people + animals; potential mAP/tracking gains;
  spike A already proved the TRT 8.5 path and parser reuse.
- Cons: GPU-trace export constraint; ~20-min engine builds; must prove it does not
  regress per-class mAP and stays real-time.

## Decision

**PENDING — decided by the B1 gate spike (#147).** Status stays **Proposed** until
B1 reports measured numbers.

**Gate rule (PO-approved):** adopt YOLOv11 **iff**

- its **per-class mAP ≥ YOLOv8's**, **AND**
- it sustains **on-device fps ≥ camera rate** (real-time).

**fps is a hard gate, not informational.** If v11 fails either condition,
**keep YOLOv8 and demote YOLOv11 to V2.**

Both metrics are measured on **identical val data + input resolution**, v11 vs v8
(baseline #77, ~56 fps from #15). B1 also adds a **value-verification step**:
assert **non-NaN engine output on a real frame** before deploy (the current export
guard only checks opset/layout).

## Consequences

- **If Accepted (v11 wins):**
  - The V1 detector becomes v11; the class-set contract change lands via **B2 (#149)**
    (`configs/animals.yaml` → `configs/classes.yaml`, append `person` as highest id:
    sheep=0, goat=1, poultry=2, person=3; V2 tier-3 ids shift accordingly).
  - **B3 (#150)** demos people + animals e2e on-device → tracks on bus → Slack.
  - The export tooling must enforce the **GPU-trace** path and the **non-NaN-frame**
    check (`scripts/dev/spike_yolo11_export.py --device cuda`).
  - This ADR flips to **Accepted**; the roadmap YOLOv11 entry links the decision.
- **If Rejected (v8 retained):** keep #77's v8 detector; `person` and v11 move to V2;
  this ADR flips to a recorded rejection.
- **Forward-port:** `person` *detection* is pulled into V1 (`v2-fwd`, `# V2→V1:`);
  human *semantics* stay V2 (#152).
- **Revisit if:** v8 later regresses, a newer Ultralytics release changes the
  TRT 8.5 export story, or the C2PSA/NaN issue is fixed upstream.

## Related

- Resolved by: **#147** (B1 gate spike). Decision tracker: **#148**.
- Inputs: spike A (PR #145, `docs/research/2026-06-07-yolo11-trt85-viability.md`),
  v8 detector #76/#77/#56/#57/#15, ADR-0007 (licensing).
- Downstream: B0 dataset feasibility **#146**, B2 contract **#149**, B3 demo **#150**,
  D1 tracking metrics **#151**, V2 human semantics **#152**.
