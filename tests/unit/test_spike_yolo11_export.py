"""Host unit tests for the YOLOv11 spike ONNX verification (no GPU/weights)."""
from __future__ import annotations

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
