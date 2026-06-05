# Vendored: DeepStream-Yolo `export_yoloV8.py`

`export_yoloV8.py` is vendored **verbatim** from
[marcoslucianops/DeepStream-Yolo](https://github.com/marcoslucianops/DeepStream-Yolo)
(`utils/export_yoloV8.py`), **MIT License** (see `LICENSE.md`).

Why vendored: the on-device `NvDsInferParseYolo` parser requires this script's
`DeepStreamOutput` ONNX layout — `output [1, anchors, 6]` (boxes + score + label).
A plain Ultralytics `model.export(format="onnx")` emits `[1, 4+nc, anchors]`, which
the parser decodes as garbage → **zero detections**. `scripts/dev/train_yolov8_farm.py`
calls this exporter (`--opset 12` for TensorRT 8.5) so the produced
`models/yolov8_farm.onnx` is parser-correct. See #56 / #95 and memory
`yolov8-onnx-export-for-trt85`.

Not modified except CRLF→LF normalization. Update by re-copying from the upstream repo.
