#!/usr/bin/env python3
"""ADR-0009 detector-adoption gate — measurement + decision logic (B1 / #147).

Pure, host-testable encoding of the YOLOv11-vs-YOLOv8 adoption gate decided in
``docs/DECISIONS/0009-detector-model-yolov11.md`` (PO-set 2026-06-07). No torch /
cv2 — just the metric math and the decision rule, so it can be unit-tested on the
host and reused by the on-device B1 measurement script.

Three HARD sub-gates; adopt YOLOv11 iff all pass (else keep YOLOv8, v11 -> V2):

1. Animals (relative): per-class mAP@0.5 (sheep/goat/poultry) >= v8 baseline (#77).
2. Person (absolute, recall-first), on the farm-person val set (>=300 instances):
   recall@IoU0.5 >= 0.90 AND >= stock yolo11n recall on the same set.
3. fps (absolute): on-device fps >= 30 (the V1 camera rate).

Python 3.8-compatible. Host-only (dev tooling, not shipped in the package).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Sequence, Tuple

Box = Tuple[float, float, float, float]  # x1, y1, x2, y2


@dataclass(frozen=True)
class GateThresholds:
    """The ADR-0009 PO-set gate values (2026-06-07)."""

    person_recall_floor: float = 0.90       # sub-gate 2 primary
    fps_floor: float = 30.0                  # sub-gate 3 (V1 camera rate)
    min_person_instances: int = 300         # val-set validity floor
    iou_thr: float = 0.5                     # match threshold for recall


def iou(a: "Box", b: "Box") -> float:
    """Intersection-over-union of two xyxy boxes (0.0 if they don't overlap)."""
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0.0:
        return 0.0
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - inter
    return inter / union if union > 0.0 else 0.0


def recall_at_iou(
    pred_boxes: "Sequence[Box]",
    gt_boxes: "Sequence[Box]",
    iou_thr: float = 0.5,
) -> "Tuple[int, int]":
    """Greedy one-to-one match preds->gts; return (true_positives, false_negatives).

    Each ground-truth box is matched to at most one prediction (the highest-IoU
    unused pred clearing ``iou_thr``). Recall = TP / (TP + FN). Predictions are
    assumed already confidence-filtered at the operating point.
    """
    used = [False] * len(pred_boxes)
    tp = 0
    for gt in gt_boxes:
        best_i, best_iou = -1, iou_thr
        for i, pred in enumerate(pred_boxes):
            if used[i]:
                continue
            v = iou(pred, gt)
            if v >= best_iou:
                best_i, best_iou = i, v
        if best_i >= 0:
            used[best_i] = True
            tp += 1
    fn = len(gt_boxes) - tp
    return tp, fn


def recall(tp: int, fn: int) -> float:
    """TP / (TP + FN); 0.0 when there are no ground-truth instances."""
    denom = tp + fn
    return tp / denom if denom > 0 else 0.0


@dataclass
class SubGate:
    name: str
    passed: bool
    detail: str


@dataclass
class GateResult:
    adopt_v11: bool
    subgates: "List[SubGate]" = field(default_factory=list)

    @property
    def reasons(self) -> "List[str]":
        return ["{}: {} ({})".format(g.name, "PASS" if g.passed else "FAIL", g.detail)
                for g in self.subgates]


def evaluate_gate(
    animal_map_v11: "Dict[str, float]",
    animal_map_v8: "Dict[str, float]",
    person_recall_v11: float,
    person_recall_stock: float,
    person_instances: int,
    fps: float,
    thr: "GateThresholds" = GateThresholds(),
) -> "GateResult":
    """Apply ADR-0009's three hard sub-gates; adopt v11 iff all pass.

    ``animal_map_*`` map class-name -> mAP@0.5 (must share keys). ``person_recall_*``
    are measured on the same farm-person val set at the deployed operating point.
    """
    subgates: List[SubGate] = []

    # Sub-gate 1: every animal class mAP@0.5(v11) >= v8.
    regressions = [
        "{} {:.3f}<{:.3f}".format(c, animal_map_v11.get(c, 0.0), animal_map_v8[c])
        for c in animal_map_v8
        if animal_map_v11.get(c, 0.0) < animal_map_v8[c]
    ]
    subgates.append(SubGate(
        "animals>=v8", not regressions,
        "all classes >= v8" if not regressions else "regressions: " + ", ".join(regressions),
    ))

    # Sub-gate 2: person recall floor AND no-regression vs stock, on a valid val set.
    val_ok = person_instances >= thr.min_person_instances
    floor_ok = person_recall_v11 >= thr.person_recall_floor
    stock_ok = person_recall_v11 >= person_recall_stock
    person_ok = val_ok and floor_ok and stock_ok
    if not val_ok:
        detail = "val set has {} person instances < {} required (invalid; expand)".format(
            person_instances, thr.min_person_instances)
    else:
        detail = "recall {:.3f} (floor {:.2f}: {}, vs stock {:.3f}: {})".format(
            person_recall_v11, thr.person_recall_floor,
            "ok" if floor_ok else "LOW",
            person_recall_stock, "ok" if stock_ok else "REGRESSED")
    subgates.append(SubGate("person", person_ok, detail))

    # Sub-gate 3: fps >= V1 camera rate.
    fps_ok = fps >= thr.fps_floor
    subgates.append(SubGate(
        "fps>=30", fps_ok, "{:.1f} fps (floor {:.0f})".format(fps, thr.fps_floor)))

    return GateResult(adopt_v11=all(g.passed for g in subgates), subgates=subgates)


__all__ = [
    "Box", "GateThresholds", "iou", "recall_at_iou", "recall",
    "SubGate", "GateResult", "evaluate_gate",
]
