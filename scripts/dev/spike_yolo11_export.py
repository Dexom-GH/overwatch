#!/usr/bin/env python3
"""YOLOv11-on-TRT-8.5 spike: export stock yolo11n.pt and verify the artifact.

Needs a CUDA GPU + torch + ultralytics to export. Uses the vendored v11
exporter's helpers (``scripts/dev/vendor/deepstream-yolo/export_yolo11.py``) to
build the DeepStream-Yolo ``[1, anchors, 6]`` model, traces it to ONNX **on the
GPU** (see ``_run_export`` for why CPU tracing is broken on the Jetson), then
fails loudly if the ONNX is not TRT-8.5-safe (opset <= 16) or not that output
layout NvDsInferParseYolo decodes. Mirrors the fail-loudly discipline of
train_yolov8_farm.py so a bad artifact never reaches the device engine build.
"""
from __future__ import annotations

from typing import Optional

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


def _run_export(
    weights: str,
    onnx_out: str,
    opset: int = 12,
    imgsz: int = 640,
    device: "Optional[str]" = None,
) -> None:
    """Export ``weights`` to a DeepStream-Yolo ONNX, tracing on ``device``.

    Imports the vendored v11 exporter's helpers in-process (rather than shelling
    out to its CPU-only ``main()``) so the ONNX is traced on the GPU when one is
    present. **This is load-bearing on the Jetson Xavier NX:** its torch 2.1
    build produces NaN on CPU for YOLOv11's C2PSA attention, so a CPU-traced ONNX
    bakes NaN -> the TRT engine yields zero detections. CUDA tracing is clean.
    See docs/research/2026-06-07-yolo11-trt85-viability.md. ``device=None`` auto-
    selects cuda when available, else cpu.
    """
    import sys
    from pathlib import Path

    import torch
    import torch.nn as nn

    vendor = Path(__file__).resolve().parent / "vendor" / "deepstream-yolo"
    if str(vendor) not in sys.path:
        sys.path.insert(0, str(vendor))
    # Importing the vendored module also applies its dist2bbox monkeypatch.
    from export_yolo11 import DeepStreamOutput, yolo11_export  # type: ignore

    dev = torch.device(
        device or ("cuda" if torch.cuda.is_available() else "cpu")
    )
    print("[spike] exporting on device:", dev)
    inner = yolo11_export(weights, dev)
    model = nn.Sequential(inner, DeepStreamOutput()).to(dev).eval()
    out_path = Path(onnx_out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    dummy = torch.zeros(1, 3, imgsz, imgsz, device=dev)
    with torch.no_grad():
        torch.onnx.export(
            model,
            dummy,
            str(out_path),
            opset_version=opset,
            do_constant_folding=True,
            input_names=["input"],
            output_names=["output"],
        )
    # Mirror the vendored exporter: write labels.txt (class names) next to the ONNX.
    names = getattr(inner, "names", None)
    if names:
        ordered = [names[k] for k in sorted(names)] if isinstance(names, dict) else list(names)
        out_path.with_name("labels.txt").write_text(
            "".join("{}\n".format(n) for n in ordered), encoding="utf-8"
        )


def _main(argv=None) -> int:
    import argparse

    p = argparse.ArgumentParser(description="YOLOv11-on-TRT-8.5 spike export+verify")
    p.add_argument("--weights", default="yolo11n.pt", help="stock COCO weights (auto-downloaded by Ultralytics)")
    p.add_argument("--out", default="models/yolo11n.onnx", help="ONNX output path")
    p.add_argument("--opset", type=int, default=12)
    p.add_argument("--imgsz", type=int, default=640)
    p.add_argument(
        "--device",
        default=None,
        help="torch device to TRACE on (default: cuda if available, else cpu). "
        "On the Jetson Xavier NX you MUST trace on cuda — CPU tracing bakes NaN "
        "for YOLOv11 (see docs/research/2026-06-07-yolo11-trt85-viability.md).",
    )
    args = p.parse_args(argv)

    _run_export(args.weights, args.out, args.opset, args.imgsz, args.device)
    model = onnx.load(args.out)
    verify_deepstream_onnx(model)
    print("[spike] OK: {} is opset<= {}, DeepStream [1, anchors, 6] layout, valid".format(
        args.out, TRT85_MAX_OPSET))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
