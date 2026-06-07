# Spike #146 (B0) — Person + animal detection dataset feasibility & labeling plan

**Date:** 2026-06-07 · **Timebox:** 1 day (desk research + inventory) · **Status:** complete
**Question:** Can we assemble a training set that lets a *single* detector reliably
detect **`person` AND the V1 animals** (sheep/goat/poultry) in **farm context**, and
does stock-COCO `person` suffice to start, or is farm-person fine-tuning required
(domain shift: overhead/oblique pen angles, work clothing, occlusion, low light)?

This is the data-acquisition feasibility for the YOLOv11 "people + animals" initiative
(spike A GO — `docs/research/2026-06-07-yolo11-trt85-viability.md`; ADR-0009). It feeds
the **B1 fine-tune (#147)** and the **ADR-0009 gate**, and mirrors the method of the
Tier-3 spike (#35, `docs/research/2026-06-04-tier3-detection-dataset-feasibility.md`).

---

## TL;DR — **B PROCEEDS. Not data-gated.** (the inverse of Tier-3)

Tier-3 (rabbit/guinea_pig) was data-*gated* because farm-relevant data was essentially
absent. `person` is the **opposite case**: it is the single most abundant class in
general detection data, and **`yolo11n` is already COCO-pretrained on it** — spike A
saw it fire strongly out-of-the-box (`person`=1290 detections on a street clip). So the
bottleneck is **not** acquiring person data. The real risk is a **training-design**
one: a naive farm-only fine-tune would make the model *forget* `person`. The plan below
neutralizes that cheaply.

---

## 1. Data sources for `person` in farm context (exit criterion 1)

| Source | What it gives | Role in B1 |
|---|---|---|
| **Stock COCO `person`** | ~**66,808 images / 273,469 instances** — by far COCO's largest class; already baked into the `yolo11n` pretrain | **Primary `person` training signal.** Free, huge, diverse. |
| **Our pen/farm footage (#89, + the on-device farm clips)** | Real in-domain frames — fixed overhead/oblique pen cameras, handlers in work clothing, occlusion by animals, variable light. #89 captures pen footage for Tier-3 V2 but **incidentally records the handlers** → a near-free source of *farm-context person* frames | **In-domain VAL set** (and optional training top-up). |
| **Public agriculture/precision-livestock CV datasets** | Surveys (precision-livestock-farming, agricultural-field datasets) catalogue many animal sets; **farm-worker/overhead-person sets are sparse and not drop-in**, but a few ag-safety/worker sets exist | Low priority — not needed to start; mine only if §3 shows a recall gap. |
| **Synthetic** (e.g. rendered farm-worker imagery) | Could fill overhead/oblique person poses | Deferred — unnecessary given COCO + #89 cover the need. |

**Chicks4FreeID (#28)** is a poultry **ReID** set — *not* a person source; noted only to
rule it out for this question.

## 2. Domain-shift assessment (exit criterion 2)

**Stock-COCO `person` suffices to START; a small in-domain val set must confirm it.**

- COCO `person` is everyday/eye-level imagery; the farm adds **overhead/oblique angles,
  coveralls/PPE, partial occlusion by animals, and low light**. This is a **real but
  mild** shift for a person detector — far milder than Tier-3 animals, where the class
  itself was unseen. Person is a high-prior, heavily-pretrained class; detectors
  generalize across viewpoint/clothing far better for it than for a novel rare animal.
- Spike A is **positive but not conclusive** evidence: `yolo11n` detected `person`
  robustly on a 1080p street clip — but that is *not* the farm camera geometry. We
  cannot claim in-domain recall without measuring on farm frames.
- **Therefore:** treat stock-COCO `person` as the starting training signal, but build a
  **small farm-context person VAL set** (from #89 footage) to *measure* in-domain recall
  and decide whether a farm-person **training** top-up is warranted. The val set is
  needed regardless — see §5, it is a B1 prerequisite for the ADR-0009 person gate.

## 3. The real risk: catastrophic forgetting (not data scarcity)

The literature is unambiguous: fine-tuning a COCO model on a **new, narrow set**
(our 3 farm animals) **erodes the original classes** unless original-class data is mixed
back in. Two failure modes specific to us:

1. **Label conflict / negative learning.** If B1's farm training frames contain people
   who are **left unlabeled** (very likely — handlers are in shot), the trainer treats
   those pixels as background and actively teaches the model to **suppress `person`**.
2. **Catastrophic forgetting.** Even with no people in the farm frames, fine-tuning only
   on animals drifts the shared backbone/neck away from the `person` representation.

**Mitigations (B1 must adopt at least one; recommend both):**
- **Mix a COCO `person` subset into the fine-tune** — the only *reliable* anti-forgetting
  measure per the sources. A balanced slice (order ~1.5–3k person images, skewed toward
  outdoor/oblique/occluded) alongside the farm animal frames.
- **Freeze backbone + neck, train the head** with a low LR for the short farm run — cheap
  insurance that preserves pretrained `person` features (good fit since the farm set is
  small).
- **Label every person in any farm training frame** used — no unlabeled humans in the
  training split.

This is the central finding of B0: the work is **data *design*, not data *acquisition*.**

## 4. Recommended train-set composition for B1 (#147) (exit criterion 3)

4-class detector: `sheep, goat, poultry, person` (ids per B2/#149: sheep=0, goat=1,
poultry=2, person=3).

| Class | Train source | Rough target | Notes |
|---|---|--:|---|
| sheep / goat / poultry | existing #77 3-class farm set | as #77 | unchanged; this is the v8↔v11 apples-to-apples animal data |
| person | **COCO `person` subset** (outdoor/oblique-skewed) | ~1.5–3k imgs | retains pretrained person; prevents forgetting |
| person (optional top-up) | farm-context frames from #89 | ~300–600 imgs | **only if §5 val shows a recall gap**; model-assisted CVAT |
| **val (person)** | **bespoke farm frames from #89** | ~100–300 imgs | **required** — measures in-domain person recall for the gate |
| val (animals) | #77 val split | as #77 | unchanged |

Recipe: farm animals + COCO-person subset, **backbone+neck frozen**, low-LR head fine-tune;
all people in farm frames labeled. Export **on GPU** (`spike_yolo11_export.py --device cuda`)
per ADR-0009 / spike A.

## 5. Labeling effort (exit criterion 2) — LOW

The opposite of Tier-3's ~1 person-week. No new person *training* corpus is needed (COCO
provides it). The only mandatory bespoke labeling is the **farm-context person VAL set**:

| Item | Frames | Effort |
|---|--:|--:|
| Farm-person **val** set (from #89) | ~100–300 | **~1–2 person-days** (model-assisted CVAT — pre-label with COCO `yolo11n`/YOLO-World `"person"`, human-review; people are large/few-per-frame, fast to box) |
| Optional farm-person **train** top-up | ~300–600 | ~2–3 days — **gated** on the val set showing a real recall gap |

So: **~1–2 days to start** (just the val set), with an optional ~2–3 day top-up only if
measured recall demands it. Reuses the #35/#77 CVAT model-assisted pipeline; no new tooling.

## 6. Decision (exit criterion 3) → feeds B1 / ADR-0009

**GO — B proceeds now. `person` + animal detection is NOT data-gated.**

- Start B1 on **#77 farm animals + a COCO `person` subset**, with the forgetting-safe
  recipe (mix + freeze-backbone/neck + label all humans).
- Build the **small farm-person val set from #89 in parallel** — it is a **B1
  prerequisite** (you cannot measure person mAP for the ADR-0009 gate without it).
- Commit to a farm-person **training** top-up **only if** the val set shows COCO-person
  in-domain recall is inadequate. Don't pre-spend that effort.

### Follow-on adjustments to B1 / ADR-0009 (exit criterion 4)
1. **B1 must use the anti-forgetting recipe** (mix COCO person + freeze backbone/neck +
   no unlabeled humans) — *not* a naive farm-only fine-tune. Add this to #147's acceptance.
2. **B1 needs the farm-person val set first** (the ~1–2 day labeling task). Add it as a
   B1 sub-task / blocker; #89 footage is the source.
3. **ADR-0009 gate wrinkle (flag, don't silently resolve):** the gate reads "per-class
   mAP(v11) ≥ v8". **v8 (#77) has no `person` class**, so person mAP has *no v8
   baseline* to beat. The gate must be read as: *animal* classes ≥ v8, **and** `person`
   meets an **absolute** in-domain bar (e.g. person mAP ≥ a set threshold, or ≥ stock
   `yolo11n` person recall measured on the same farm-person val set). Recommend B1/#148
   define that absolute person bar explicitly so the gate is decidable.
4. If #89 hasn't captured handler-present footage yet, ensure the next pen-footage
   capture deliberately includes people (near-free, mirrors #35's "capture now" logic).

---

## Sources

- COCO `person` scale: [COCO 2017 — Dataset Ninja](https://datasetninja.com/coco-2017) ·
  [People counting using COCO — Medium](https://medium.com/@BH_Chinmay/people-counting-using-coco-dataset-ccc266d1851e)
- Catastrophic forgetting / mix-data mitigation: [Fine-Tune YOLO on a Custom Dataset — Ultralytics docs](https://docs.ultralytics.com/guides/finetuning-guide) ·
  [Train a specific class without compromising others — Ultralytics discussion #6849](https://github.com/orgs/ultralytics/discussions/6849) ·
  [Fine-Tuning Without Forgetting: Adaptation of YOLOv8 Preserves COCO Performance (arXiv 2505.01016)](https://arxiv.org/html/2505.01016v1)
- Agriculture CV dataset landscape (farm-worker/overhead sparsity): [Systematic survey of public CV datasets for precision livestock farming — Computers and Electronics in Agriculture](https://dl.acm.org/doi/10.1016/j.compag.2024.109718) ·
  [Agricultural Computer Vision Dataset Survey](https://smartfarminglab.github.io/field_dataset_survey/)
- Method reuse: `docs/research/2026-06-04-tier3-detection-dataset-feasibility.md` (#35),
  spike A `docs/research/2026-06-07-yolo11-trt85-viability.md`, ADR-0009.
