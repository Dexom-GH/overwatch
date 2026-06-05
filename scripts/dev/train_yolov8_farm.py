#!/usr/bin/env python3
"""Fine-tune + export the V1 3-class farm YOLOv8 detector (#77).

Produces ``models/yolov8_farm.onnx`` (gitignored) — the sheep/goat/poultry
detector the on-device engine build (#56) consumes. Detector classes are the V1
subset of ``configs/animals.yaml`` (tier 1-2; see ``overwatch.inference.labels``).

Host / off-device GPU only. ONNX export goes through the vendored DeepStream-Yolo
exporter (``vendor/deepstream-yolo/export_yoloV8.py``) so the output is the
``[1, anchors, 6]`` layout ``NvDsInferParseYolo`` needs, at **opset 12** (TRT 8.5
rejects opset >= 17). The script verifies opset, output layout, and class names —
failing loudly if any is wrong, so a bad artifact never reaches the device build
(#56 / #95).

Example::

    python scripts/dev/train_yolov8_farm.py \
        --data datasets/farm/data.yaml --weights yolov8n.pt \
        --epochs 100 --imgsz 640 --device 0 --out models/yolov8_farm.onnx

The ``data.yaml`` is the standard Ultralytics dataset config; its ``names`` MUST
be ``[sheep, goat, poultry]`` in that order (matches the canonical class ids).
Pin the Ultralytics version you used in the issue's training record.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

EXPECTED_NAMES = ["sheep", "goat", "poultry"]  # animals.yaml tier 1-2, in id order
TRT85_OPSET = 12


def _verify_data_names(data_yaml: str) -> None:
    import yaml

    cfg = yaml.safe_load(Path(data_yaml).read_text(encoding="utf-8"))
    names = cfg.get("names")
    if isinstance(names, dict):  # ultralytics also allows {0: sheep, ...}
        names = [names[k] for k in sorted(names)]
    if list(names or []) != EXPECTED_NAMES:
        raise SystemExit(
            "data.yaml names {!r} != expected {!r} (class ids must match "
            "animals.yaml: sheep=0, goat=1, poultry=2)".format(names, EXPECTED_NAMES)
        )


_DS_EXPORT = Path(__file__).resolve().parent / "vendor" / "deepstream-yolo" / "export_yoloV8.py"


def _export_onnx(best_pt: str, out: str, imgsz: int) -> str:
    """Export to ONNX in the DeepStream-Yolo layout (output ``[1, anchors, 6]``).

    Uses the vendored DeepStream-Yolo ``export_yoloV8.py`` — ``NvDsInferParseYolo``
    needs that layout; a plain Ultralytics export (``[1, 4+nc, anchors]``) parses
    to zero detections. ``--opset 12`` keeps it TRT-8.5-safe.
    """
    subprocess.run(
        [sys.executable, str(_DS_EXPORT), "-w", best_pt, "-s", str(imgsz),
         "--opset", str(TRT85_OPSET), "--simplify"],
        check=True,
    )
    produced = Path(best_pt).with_suffix(".onnx")  # exporter writes alongside the .pt
    out_path = Path(out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(produced, out_path)
    return str(out_path)


def _assert_opset_12(onnx_path: str) -> None:
    import onnx

    model = onnx.load(onnx_path)
    opsets = [op.version for op in model.opset_import if op.domain in ("", "ai.onnx")]
    if TRT85_OPSET not in opsets or any(v > TRT85_OPSET for v in opsets):
        raise SystemExit(
            "exported ONNX opset {} != {} — TRT 8.5 will reject it; re-export "
            "with opset=12, dynamo=False (memory: yolov8-onnx-export-for-trt85)".format(
                opsets, TRT85_OPSET
            )
        )


def _assert_ds_output_shape(onnx_path: str) -> None:
    import onnx

    model = onnx.load(onnx_path)
    dims = [d.dim_value for d in model.graph.output[0].type.tensor_type.shape.dim]
    if len(dims) != 3 or dims[-1] != 6:
        raise SystemExit(
            "ONNX output {} is not the DeepStream-Yolo layout [1, anchors, 6] — "
            "NvDsInferParseYolo would read garbage (zero detections). Export must "
            "use the vendored DeepStream-Yolo script, not plain Ultralytics.".format(dims)
        )


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Fine-tune + export 3-class farm YOLOv8 (#77)")
    ap.add_argument("--data", required=True, help="Ultralytics data.yaml (names: sheep,goat,poultry)")
    ap.add_argument("--weights", default="yolov8n.pt", help="pretrained weights to fine-tune from")
    ap.add_argument("--epochs", type=int, default=100)
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--batch", type=int, default=16, help="lower (e.g. 8/4) on small/shared VRAM")
    ap.add_argument("--device", default="0", help="'0' (GPU) or 'cpu'")
    ap.add_argument("--out", default="models/yolov8_farm.onnx")
    ap.add_argument("--name", default="yolov8_farm", help="run name under runs/detect/")
    # Windows: the multiprocessing DataLoader (workers>0) can deadlock at startup;
    # use --workers 0 (single-process) + --cache to keep epochs fast there.
    ap.add_argument("--workers", type=int, default=8, help="DataLoader workers (0 on Windows)")
    ap.add_argument("--cache", action="store_true", help="cache images in RAM (speeds workers=0)")
    ap.add_argument("--skip-train", action="store_true", help="export only (--weights is a trained .pt)")
    args = ap.parse_args(argv)

    _verify_data_names(args.data)

    if args.skip_train:
        best = args.weights
    else:
        from ultralytics import YOLO

        model = YOLO(args.weights)
        results = model.train(
            data=args.data, epochs=args.epochs, imgsz=args.imgsz, batch=args.batch,
            device=args.device, name=args.name,
            workers=args.workers, cache=args.cache,
        )
        best = str(Path(results.save_dir) / "weights" / "best.pt")
        print("[train] best weights:", best)

    onnx_path = _export_onnx(best, args.out, args.imgsz)
    _assert_opset_12(onnx_path)
    _assert_ds_output_shape(onnx_path)
    print("[export] wrote {} (opset {}, DeepStream-Yolo layout, 3-class)".format(
        onnx_path, TRT85_OPSET))
    print("[next] hand to #56 for the on-device ONNX->TRT FP16 engine build")
    return 0


if __name__ == "__main__":
    sys.exit(main())
