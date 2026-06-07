"""Host unit tests for the YOLOv11 spike ONNX verification (no GPU/weights).

The spike export helper lives in ``scripts/dev/`` — host dev tooling, not part of
the shipped ``overwatch`` package — so we load it by path (mirroring
``test_check_env.py``) rather than importing it as a module. Importing it as
``scripts.dev....`` only resolves under ``python -m pytest`` (cwd on sys.path),
not under CI's bare ``pytest`` invocation.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from onnx import TensorProto, helper

_SPIKE = Path(__file__).resolve().parents[2] / "scripts" / "dev" / "spike_yolo11_export.py"


def _load_spike():
    spec = importlib.util.spec_from_file_location("spike_yolo11_export", _SPIKE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


spike = _load_spike()


def _model(out_shape, opset):
    """A minimal valid ONNX model: Identity from input to output of out_shape."""
    info = helper.make_tensor_value_info("input", TensorProto.FLOAT, out_shape)
    out = helper.make_tensor_value_info("output", TensorProto.FLOAT, out_shape)
    node = helper.make_node("Identity", ["input"], ["output"])
    graph = helper.make_graph([node], "g", [info], [out])
    return helper.make_model(graph, opset_imports=[helper.make_opsetid("", opset)])


def test_accepts_opset12_deepstream_layout():
    # [1, anchors, 6] = boxes(4)+score(1)+label(1); opset 12 (TRT 8.5 safe)
    spike.verify_deepstream_onnx(_model([1, 8400, 6], 12))  # must not raise


def test_rejects_opset_above_16():
    with pytest.raises(spike.OpsetError):
        spike.verify_deepstream_onnx(_model([1, 8400, 6], 17))


def test_rejects_non_deepstream_layout():
    # raw Ultralytics-style last dim (not 6) must be rejected
    with pytest.raises(spike.DeepStreamLayoutError):
        spike.verify_deepstream_onnx(_model([1, 8400, 4], 12))


def test_rejects_dynamic_last_dim():
    # a symbolic (string) last dim yields dim_value == 0 -> dynamic axis,
    # exactly the bad ONNX the guard exists to reject
    with pytest.raises(spike.DeepStreamLayoutError):
        spike.verify_deepstream_onnx(_model([1, 8400, "n"], 12))
