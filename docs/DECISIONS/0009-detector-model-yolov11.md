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

**Gate rule (PO-approved).** Adopt YOLOv11 **iff all three** sub-gates pass:

1. **Animal classes (relative, ≥ v8):** per-class mAP@0.5 for **sheep, goat,
   poultry** is **≥ YOLOv8's** (baseline #77), measured on the **#77 animal val
   split** at identical input resolution. This is the apples-to-apples comparison
   the original rule was written for, and it stays unchanged.
2. **`person` (absolute, recall-first):** the v8 detector (#77) has **no `person`
   class**, so there is **no v8 person baseline to beat** — a relative "≥ v8" gate
   is *undecidable* for `person` (surfaced by B0, #146). `person` is therefore
   judged against an **absolute, in-domain bar** measured on the **bespoke
   farm-person VAL set** (built from #89 footage; the B0 §5 prerequisite). The bar
   is, in priority order:

   - **(primary, hard) Recall floor:** **person recall ≥ 0.90** at the deployed
     operating point (the `pre-cluster-threshold` / conf used for the demo, the
     same threshold used to measure fps), counting a detection at **IoU ≥ 0.5**.
     *Missing a person on a working farm is a safety event; a spurious person
     alert is a nuisance — so recall, not mAP, is the binding constraint.*
   - **(secondary, hard) No-regression vs off-the-shelf:** the fine-tuned v11's
     person recall **must be ≥ stock `yolo11n`'s person recall measured on the
     SAME farm-person val set**. This guarantees the farm fine-tune + the B0
     anti-forgetting recipe (COCO-person mix, frozen backbone/neck, all humans
     labeled) **did not erode** the off-the-shelf person capability in our domain.
   - **(informational) Precision / mAP:** report person mAP@0.5 and
     precision-at-operating-point for the record; they do **not** gate (low
     precision = nuisance alerts, acceptable for V1; can be tuned later).

   If the farm-person val set yields too few person instances to estimate recall
   stably (B0 targets ~100–300 frames, people are large/few-per-frame), B1 must
   flag it and either expand the val set or escalate the bar definition to the PO
   rather than passing the gate on a noisy estimate.
3. **fps (absolute, hard):** sustained **on-device fps ≥ camera rate** (real-time),
   measured at the same operating point as the person-recall measurement
   (baseline reference: v8 ~56 fps from #15; spike A saw v11n ~47 fps single-stream).

**All three are hard gates.** If v11 fails **any** of them, **keep YOLOv8 and
demote YOLOv11 to V2.**

**Measurement substrate.** Animal sub-gate (1) is measured on the **#77 animal val
split**; the `person` sub-gate (2) is measured on the **bespoke farm-person VAL
set from #89** (B0 §5 — a B1 prerequisite, not optional); the fps sub-gate (3) is
measured on-device at the deployed operating point. All metrics use identical input
resolution to the deployed engine. B1 also adds a **value-verification step**:
assert **non-NaN engine output on a real frame** before deploy/measure (the current
export guard only checks opset/layout).

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
