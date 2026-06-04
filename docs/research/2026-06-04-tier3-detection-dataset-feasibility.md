# Spike #35 — Tier-3 detection dataset feasibility (rabbit, guinea_pig)

**Date:** 2026-06-04 · **Timebox:** 2 days (feasibility-first) · **Status:** complete
**Question:** Can we collect + label enough **rabbit** and **guinea_pig** detection
data on the real farm to train a *usable* detector within the V1 window?

This is the data-acquisition feasibility surfaced by the #5 detector research
(which established that custom fine-tuning is required regardless of architecture —
COCO omits goat/rabbit/guinea pig — and flagged Tier-3 data scarcity as the single
biggest V1 schedule risk). `configs/animals.yaml` is the class-set source of truth;
rabbit (id 3) and guinea_pig (id 4) are `tier: 3` (bespoke-data-gated).

---

## 1. Is there usable public data? (refines the "essentially none" premise)

Not literally zero, but **nothing farm-relevant at usable scale** — and crucially
**none in our context** (domestic animals in dense pens, fixed overhead/oblique
camera):

| Species | What exists publicly | Usable to *train*? | Usable to *bootstrap* a pre-labeler? |
|---|---|---|---|
| rabbit | Roboflow cottontail sets (~95 + ~87 imgs), a "rabbit-detector" set (~326 imgs, mixed rabbit/guinea-pig/squirrel), a rabbit **classification** set (~1600 imgs) | **No** — wild cottontails in the field, wrong domain; tiny; classification ≠ detection | **Partially** — enough to seed a weak rabbit box-proposer |
| guinea_pig | images.cv guinea-pig set (~1.3k) is **classification only**; no real detection set | **No** | **Barely** — essentially start from zero-shot open-vocab |

**Takeaway:** the #5 "essentially no public detection datasets" claim holds for our
purpose. Guinea pig is the harder of the two (no detection bootstrap at all).

## 2. Labeling pipeline decision (exit criterion 1)

**Recommended: model-assisted, human-in-the-loop in CVAT.**

1. **Capture** raw pen footage on-device via the existing RTSP/record path (the
   #11 record/replay harness); sample frames at low FPS to avoid near-duplicates.
2. **Pre-label** with an **open-vocabulary detector** — YOLO-World or Grounding
   DINO with the text prompts `"rabbit"` / `"guinea pig"` — to propose boxes;
   optionally tighten with SAM2. Run via **CVAT's auto-annotation API** (supports
   HuggingFace/Roboflow models) or the lighter Python-native **X-AnyLabeling**.
3. **Human review/correct** every frame in CVAT (mandatory — see §3 on why
   zero-shot is weak for our rare/dense classes), then export **YOLO format**
   straight into the #77 fine-tune.
4. **Iterate**: after the first ~150–200 corrected images/species, train a quick
   in-house YOLOv8n and use *it* as the pre-labeler for the rest (active-learning
   loop) — this beats zero-shot once the pen domain is learned.

Rationale: CVAT is open-source, self-hostable (no data leaves the farm — fits the
ADR-0007 posture), battle-tested, and has a first-class auto-annotation path.

## 3. Volume + effort estimate (exit criterion 2)

**Volume target.** Ultralytics' "best" guidance is **≥1500 images and ≥10k
instances per class**. A *usable* (not SOTA) detector in a **fixed pen** (one
camera, stable lighting, low scene diversity) plus transfer learning from COCO +
layer-freezing + augmentation lands well below that. Working target:

- **~500–800 labeled images/species** as the *usable floor* (1500/10k = stretch).
- Pen scenes are **dense** (≈8–15 animals/frame), so instance counts climb fast —
  good for the ≥10k-instance bar, bad for per-box effort (small, similar, occluded).

**Annotation effort.** Per-box time for small/occluded/similar targets ≈ 15–25 s
including the occlusion judgement; at ~10 boxes/frame ≈ 3 min/frame **manual**.

| | rabbit | guinea_pig | both |
|---|--:|--:|--:|
| Images (usable floor) | ~600 | ~600 | 1200 |
| Manual annotation | ~30 h | ~30 h | ~60 h |
| + QA / rework (~30%) | | | ~80 h manual |
| **With model-assist** (realistic **~40–50%** cut for this niche, *not* the headline 80% — see below) | | | **~40–50 h** |

Plus, **elapsed**: ~1–2 days on-farm capture, ~0.5 day tooling/auto-annotate setup,
2–3 train/eval iterations → **~1 working week, one person**, for both species.

**Why not the headline auto-label speedups?** Voxel51 reports verified auto-labeling
~90–95% of human performance and ~5,000× faster — **but only for common classes**;
quality degrades sharply on rare/specialized classes (LVIS F1 ≈ 0.215). Farm
rabbits/guinea-pigs in pens are precisely that rare/dense/occluded case, so we
**cannot** trust unreviewed auto-labels — hence the ~40–50% assist (mostly from the
in-house active-learning loop in step 4), not 80%.

## 4. Go / defer recommendation (exit criterion 3 → ROADMAP)

**DEFER the Tier-3 *detector* (rabbit, guinea_pig) to V2 — but start *data capture*
in V1 now (cheap).**

Reasoning:
- The **P0 demo spine (#16 / #84) runs on Tier 1** (sheep/goat) and is explicitly
  **not** blocked by Tier 3.
- A serious Tier-3 detector is **~1 focused person-week** of capture + label +
  iterate, with **guinea pig genuinely risky** (no detection bootstrap data). That
  is real, schedulable work — feasible, but not worth spending the V1 window on a
  non-spine capability.
- **Capture is near-free now.** Recording pen footage during V1 (raw clips via the
  RTSP/record path) costs almost nothing and means V2 labeling does **not** wait on
  re-capture or a return trip to the farm. This de-risks V2 without taxing V1.

So: demote the Tier-3 **detector** to V2 (a documented boundary move, not silent),
and keep a small **V1 "capture Tier-3 pen footage now"** chore.

**Consequence for #77:** with Tier-3 deferred, the V1 farm detector (#77) ships as
a **3-class** model (sheep, goat, poultry) rather than 5-class. This is a scope
refinement for #77's groom — flagged here, not silently changed.

## 5. Follow-on actions (exit criterion 4)

1. **V1 chore (new):** "Capture + archive Tier-3 pen footage (rabbit, guinea_pig)
   for later labeling" — low effort; records raw clips now so V2 labeling isn't
   gated on re-capture. `type:chore`, `area:capture`, `prio:P2`, `v1`.
2. **V2 item (new or convert):** "Tier-3 detector (rabbit, guinea_pig): label +
   fine-tune" — carries this pipeline + estimate. `type:slice`, `area:inference`,
   `v2`. (Could be the V2 home that #77's 5th/4th classes split into.)
3. **#77 groom note:** narrow V1 scope to 3 classes (sheep/goat/poultry); Tier-3
   classes move to the V2 item above.
4. **`configs/animals.yaml`:** rabbit/guinea_pig `tier: 3` comments already say
   "in V1 only if labeling lands in time, else demoted to V2" — this spike resolves
   that to **demoted**; no id/label change (we still own the canonical map for V2).

---

## Sources

- [YOLOv8 best practices / data-per-class guidance — Ultralytics community](https://community.ultralytics.com/t/looking-for-best-practices-for-fine-tuning-yolov8-on-custom-dataset/717) and [Ultralytics YOLOv8 docs](https://docs.ultralytics.com/models/yolov8)
- [Verified zero-shot auto-labeling: cost/quality vs human — Voxel51](https://voxel51.com/blog/zero-shot-auto-labeling-rivals-human-performance) · [Complete guide to auto-labeling — Voxel51](https://voxel51.com/blog/the-complete-guide-to-auto-labeling)
- [CVAT auto-annotation API](https://docs.cvat.ai/docs/api_sdk/sdk/auto-annotation/) · [Automated data labeling guide — CVAT](https://www.cvat.ai/resources/blog/automated-data-labeling-guide)
- [X-AnyLabeling (Python-native AI-assisted annotation)](https://dev.to/jack_wang_d47b1f7f781c64f/meet-x-anylabeling-the-python-native-ai-powered-annotation-tool-for-modern-cv-507b) · [Auto-labeling with Grounding DINO — TDS](https://medium.com/data-science/automatic-labeling-of-object-detection-datasets-using-groundingdino-b66c486656fe)
- [Bounding-box annotation cost/throughput trade-offs — Label Your Data](https://labelyourdata.com/articles/data-annotation/bounding-box-annotation)
- Public Tier-3 data (scarcity check): [Cottontail-Rabbits (Roboflow, ~95 imgs)](https://public.roboflow.com/object-detection/cottontail-rabbits-video-dataset) · [Eastern Cottontail Rabbits (~87 imgs)](https://public.roboflow.com/object-detection/eastern-cottontail-rabbits) · [Rabbit-detector (~326 imgs, mixed)](https://universe.roboflow.com/objectdetection-pshgf/rabbit-detector-3glty) · [Guinea-pig set — classification only (images.cv)](https://images.cv/dataset/guinea-pig-image-classification-dataset)
