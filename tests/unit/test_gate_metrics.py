"""Host unit tests for the ADR-0009 gate metrics (B1 / #147).

``scripts/dev/gate_metrics.py`` is host dev tooling, not part of the shipped
``overwatch`` package, so we load it by path (mirroring ``test_check_env.py`` /
``test_spike_yolo11_export.py``) rather than importing it as a module.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_GM = Path(__file__).resolve().parents[2] / "scripts" / "dev" / "gate_metrics.py"


def _load():
    spec = importlib.util.spec_from_file_location("gate_metrics", _GM)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Register before exec so dataclass annotation resolution (the module uses
    # `from __future__ import annotations`) can find the module in sys.modules.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


gm = _load()


# --- IoU --------------------------------------------------------------------
def test_iou_identical_is_one():
    assert gm.iou((0, 0, 10, 10), (0, 0, 10, 10)) == 1.0


def test_iou_disjoint_is_zero():
    assert gm.iou((0, 0, 10, 10), (20, 20, 30, 30)) == 0.0


def test_iou_half_overlap():
    # two 10x10 boxes overlapping in a 10x5 strip -> inter 50, union 150
    assert abs(gm.iou((0, 0, 10, 10), (0, 5, 10, 15)) - (50 / 150)) < 1e-9


# --- recall matching --------------------------------------------------------
def test_recall_matching_tp_fn():
    gts = [(0, 0, 10, 10), (100, 100, 110, 110)]
    preds = [(0, 0, 10, 10)]  # matches first gt only
    tp, fn = gm.recall_at_iou(preds, gts, iou_thr=0.5)
    assert (tp, fn) == (1, 1)
    assert gm.recall(tp, fn) == 0.5


def test_recall_one_pred_cannot_match_two_gts():
    gts = [(0, 0, 10, 10), (0, 0, 10, 10)]
    preds = [(0, 0, 10, 10)]
    tp, fn = gm.recall_at_iou(preds, gts, iou_thr=0.5)
    assert (tp, fn) == (1, 1)  # greedy one-to-one


def test_recall_no_gts_is_zero():
    assert gm.recall(0, 0) == 0.0


# --- gate decision ----------------------------------------------------------
def _good_args():
    return dict(
        animal_map_v11={"sheep": 0.81, "goat": 0.75, "poultry": 0.70},
        animal_map_v8={"sheep": 0.80, "goat": 0.75, "poultry": 0.68},
        person_recall_v11=0.92,
        person_recall_stock=0.88,
        person_instances=350,
        fps=47.0,
    )


def test_gate_adopts_when_all_pass():
    res = gm.evaluate_gate(**_good_args())
    assert res.adopt_v11 is True
    assert all(g.passed for g in res.subgates)


def test_gate_rejects_on_animal_regression():
    args = _good_args()
    args["animal_map_v11"] = {"sheep": 0.79, "goat": 0.75, "poultry": 0.70}  # sheep < v8
    res = gm.evaluate_gate(**args)
    assert res.adopt_v11 is False
    assert any(g.name == "animals>=v8" and not g.passed for g in res.subgates)


def test_gate_rejects_when_person_recall_below_floor():
    args = _good_args()
    args["person_recall_v11"] = 0.85  # < 0.90 floor (still > stock)
    args["person_recall_stock"] = 0.80
    res = gm.evaluate_gate(**args)
    assert res.adopt_v11 is False
    assert any(g.name == "person" and not g.passed for g in res.subgates)


def test_gate_rejects_when_person_regresses_vs_stock():
    args = _good_args()
    args["person_recall_v11"] = 0.91          # clears floor
    args["person_recall_stock"] = 0.94        # but worse than off-the-shelf
    res = gm.evaluate_gate(**args)
    assert res.adopt_v11 is False


def test_gate_invalid_when_too_few_person_instances():
    args = _good_args()
    args["person_instances"] = 120  # < 300 -> val set invalid, gate can't pass
    res = gm.evaluate_gate(**args)
    assert res.adopt_v11 is False
    person = [g for g in res.subgates if g.name == "person"][0]
    assert not person.passed and "instances" in person.detail


def test_gate_rejects_below_camera_rate():
    args = _good_args()
    args["fps"] = 22.0  # < 30
    res = gm.evaluate_gate(**args)
    assert res.adopt_v11 is False
    assert any(g.name == "fps>=30" and not g.passed for g in res.subgates)
