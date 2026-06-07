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
